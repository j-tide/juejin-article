"""Episode 01: deterministic fault injection for an Agent runtime.

Python 3.10+, standard library only. No LLM, network, or external side effects.
ReplayPolicy supplies scripted proposals; these are NOT model benchmarks.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


@dataclass(frozen=True)
class Contract:
    version: str = "review-contract-v1"
    sections: tuple = ("结论", "证据", "局限")
    min_sources: int = 2
    max_turns: int = 8
    max_tool_calls: int = 5


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    text: str


# Explicitly synthetic notes used only to exercise provenance checks.
FIXTURES = {
    "a": Evidence("a", "fixture://runtime", "运行时可以按既定规则检查交付物。"),
    "b": Evidence("b", "fixture://evaluation", "格式完整不能证明结论有充分依据。"),
}


@dataclass
class State:
    phase: str = "CREATED"
    turns: int = 0
    tool_calls: int = 0
    checks: int = 0
    feedback: list = field(default_factory=list)
    observed: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    receipt: dict | None = None
    accepted_bytes: bytes | None = None


def verify(snapshot: bytes | None, observed: dict, contract: Contract) -> list:
    """Structural checks only: not a semantic truth or citation-entailment check."""
    if snapshot is None:
        return ["artifact_missing"]
    text = snapshot.decode("utf-8")
    errors = []
    if not text.strip():
        errors.append("artifact_empty")
    for heading in contract.sections:
        pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
        block = re.search(pattern, text, re.MULTILINE | re.DOTALL)
        if not block or not block.group(1).strip():
            errors.append("section_missing_or_empty:" + heading)
    raw_citations = re.findall(r"\[E:([^\]\r\n]*)\]", text)
    if text.count("[E:") != len(raw_citations):
        errors.append("malformed_citation")
    citations = set(raw_citations)
    errors.extend("invalid_citation_id:" + key for key in sorted(citations)
                  if not re.fullmatch(r"[a-z0-9_-]+", key))
    unknown = citations - observed.keys()
    errors.extend("unobserved_evidence:" + key for key in sorted(unknown))
    sources = {observed[key].source for key in citations if key in observed}
    if len(sources) < contract.min_sources:
        errors.append("insufficient_observed_sources")
    return errors


class Harness:
    """Single-process lab; persistence and remote side effects are out of scope."""

    def __init__(self, workspace: Path, contract=Contract(), gate=True):
        self.workspace = workspace
        workspace.mkdir(parents=True, exist_ok=True)
        self.draft = workspace / "draft.md"
        if self.draft.exists():
            raise ValueError("New tasks need a clean workspace; recovery is introduced in episode 02.")
        self.contract = contract
        self.gate = gate
        self.state = State()

    def event(self, kind, **data):
        self.state.events.append({"seq": len(self.state.events) + 1, "kind": kind, **data})

    def finish_proposal(self):
        state = self.state
        state.phase = "VALIDATING"
        # Read once. Validate and accept exactly the same immutable bytes.
        snapshot = self.draft.read_bytes() if self.draft.is_file() else None
        if self.gate:
            state.checks += 1
            state.feedback = verify(snapshot, state.observed, self.contract)
            if state.feedback:
                self.event("validation_rejected", errors=list(state.feedback))
                state.phase = "RUNNING"
                return False
        policy_json = json.dumps(asdict(self.contract), sort_keys=True).encode()
        state.accepted_bytes = snapshot
        state.receipt = {
            "artifact_sha256": digest(snapshot) if snapshot is not None else None,
            "contract_sha256": digest(policy_json),
            "contract_version": self.contract.version,
            "verification": "structural_gate" if self.gate else "bypassed_for_experiment",
            "evidence_sha256": {
                key: digest(item.text.encode()) for key, item in sorted(state.observed.items())
            },
        }
        state.phase = "REVIEW_READY"
        self.event("accepted", receipt=state.receipt)
        return True

    def run(self, policy):
        state = self.state
        if state.phase != "CREATED":
            raise ValueError("A run cannot be resumed by calling run again in episode 01.")
        state.phase = "RUNNING"
        while state.turns < self.contract.max_turns:
            observation = {
                "feedback": list(state.feedback),
                "observed_evidence": sorted(state.observed),
                "evidence": [asdict(item) for _, item in sorted(state.observed.items())],
                "draft": self.draft.read_text() if self.draft.is_file() else None,
                "turns_used": state.turns,
            }
            state.turns += 1
            action = policy(observation)
            if not isinstance(action, dict):
                state.phase = "BLOCKED"
                self.event("invalid_action")
                return state
            kind = action.get("kind")
            self.event("proposal", action=action)
            if kind == "finish":
                if set(action) != {"kind"}:
                    state.phase = "BLOCKED"
                    self.event("invalid_finish_parameters")
                    return state
                if self.finish_proposal():
                    return state
                continue
            if not isinstance(kind, str) or kind not in {"read_evidence", "write_report"}:
                state.phase = "BLOCKED"
                self.event("action_denied", name=kind)
                return state
            if state.tool_calls >= self.contract.max_tool_calls:
                state.phase = "EXHAUSTED"
                self.event("tool_budget_exhausted")
                return state
            state.tool_calls += 1
            if kind == "read_evidence":
                key = action.get("id")
                if set(action) != {"kind", "id"} or not isinstance(key, str) or key not in FIXTURES:
                    state.feedback = ["invalid_evidence_request"]
                    self.event("tool_error", errors=list(state.feedback))
                    continue
                state.observed[key] = FIXTURES[key]
                self.event("evidence_observed", id=key)
            else:
                text = action.get("text")
                if set(action) != {"kind", "text"} or not isinstance(text, str):
                    state.feedback = ["invalid_report_request"]
                    self.event("tool_error", errors=list(state.feedback))
                    continue
                self.draft.write_bytes(text.encode("utf-8"))
                self.event("artifact_written", sha256=digest(text.encode("utf-8")))
            state.feedback = []
        state.phase = "EXHAUSTED"
        self.event("turn_budget_exhausted")
        return state


class ReplayPolicy:
    """Predefined proposals; repeats the final proposal after the script ends."""

    def __init__(self, actions):
        self.actions = actions
        self.index = 0

    def __call__(self, observation):
        action = self.actions[min(self.index, len(self.actions) - 1)]
        self.index += 1
        return dict(action)


READ_A = {"kind": "read_evidence", "id": "a"}
READ_B = {"kind": "read_evidence", "id": "b"}
FINISH = {"kind": "finish"}
GOOD = "## 结论\n运行时按契约检查报告。\n## 证据\n运行时记录见 [E:a]；校验边界见 [E:b]。\n## 局限\n这些本地测试笔记不能证明任意模型的实际可靠性。\n"
NO_LIMITATIONS = GOOD.split("## 局限")[0]
UNSUPPORTED = GOOD.replace("运行时按契约检查报告。", "只要有 Harness，任何模型都能保证百分之百正确。")


def write(text):
    return {"kind": "write_report", "text": text}


CASES = [
    ("直接声称完成", [FINISH], "EXHAUSTED"),
    ("写出空报告", [write(""), FINISH], "EXHAUSTED"),
    ("引用未读取资料", [write(GOOD), FINISH], "EXHAUSTED"),
    ("引用不存在编号", [READ_A, READ_B, write(GOOD.replace("[E:b]", "[E:missing]")), FINISH], "EXHAUSTED"),
    ("报告缺少局限", [READ_A, READ_B, write(NO_LIMITATIONS), FINISH], "EXHAUSTED"),
    ("被拒绝后补齐", [READ_A, READ_B, write(NO_LIMITATIONS), FINISH, write(GOOD), FINISH], "REVIEW_READY"),
    ("合格的正例", [READ_A, READ_B, write(GOOD), FINISH], "REVIEW_READY"),
    ("格式合格但结论夸大", [READ_A, READ_B, write(UNSUPPORTED), FINISH], "REVIEW_READY"),
    ("越权修改状态", [{"kind": "set_status", "status": "REVIEW_READY"}], "BLOCKED"),
    ("不停调用工具", [READ_A], "EXHAUSTED"),
]


def run_experiments(output):
    output.mkdir(parents=True, exist_ok=True)
    rows, traces = [], {}
    with tempfile.TemporaryDirectory() as temporary:
        for index, (label, actions, expected) in enumerate(CASES):
            row = {"case": label}
            for gate, name in [(False, "without_gate"), (True, "with_gate")]:
                harness = Harness(Path(temporary) / f"case-{index}-{name}", gate=gate)
                state = harness.run(ReplayPolicy(actions))
                row[name] = state.phase
                row[name + "_turns"] = state.turns
                row[name + "_tools"] = state.tool_calls
                if gate:
                    assert state.phase == expected, (label, state.phase, expected)
                    assert state.turns <= harness.contract.max_turns
                    assert state.tool_calls <= harness.contract.max_tool_calls
                    if state.phase == "REVIEW_READY":
                        assert verify(state.accepted_bytes, state.observed, harness.contract) == []
                        assert state.receipt["artifact_sha256"] == digest(state.accepted_bytes)
                traces[f"{index + 1:02d}-{name}"] = state.events
            rows.append(row)
    report = {
        "experiment": "deterministic harness contract tests; no live model",
        "contract": asdict(Contract()),
        "rows": rows,
        "scope": "Structural acceptance and execution bounds only; semantic truth is not verified.",
    }
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "traces.json").write_text(json.dumps(traces, ensure_ascii=False, indent=2) + "\n")
    for row in rows:
        print(f"{row['case']}: {row['without_gate']} -> {row['with_gate']}")
    print("10 scenarios passed; the semantic blind spot is intentionally reproduced.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("lab-results"))
    run_experiments(parser.parse_args().output)
