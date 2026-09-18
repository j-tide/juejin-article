"""Compare one narrowly-scoped policy change against a separate synthetic gate."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from .evaluation import EvalConfig, EvaluationError, EvaluationSuite, evaluate, replay_input, summarize
from .evaluation_demo import policy as baseline_policy


class PolicyUpdateError(ValueError):
    """The candidate-update fixture or its promotion rules are malformed."""


def _text(value, code, maximum=240):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PolicyUpdateError(code)
    return value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CandidateProposal:
    proposal_id: str
    source_failure_id: str
    policy_revision: str
    trigger_phrases: tuple
    purpose: str


class PolicyUpdateSuite:
    """A development calibration set and an incident-disjoint promotion gate."""

    def __init__(self, raw):
        if not isinstance(raw, dict) or raw.get("schema_version") != "policy-update-v1":
            raise PolicyUpdateError("invalid_policy_update_schema")
        if raw.get("synthetic") is not True:
            raise PolicyUpdateError("policy_update_requires_synthetic_marker")
        expected = {"schema_version", "synthetic", "proposal", "calibration", "gate"}
        if set(raw) != expected:
            raise PolicyUpdateError("unexpected_policy_update_fields")
        proposal = raw["proposal"]
        if not isinstance(proposal, dict) or set(proposal) != {
            "id", "source_failure_id", "policy_revision", "trigger_phrases", "purpose"
        }:
            raise PolicyUpdateError("invalid_proposal")
        phrases = proposal["trigger_phrases"]
        if not isinstance(phrases, list) or not phrases:
            raise PolicyUpdateError("proposal_trigger_phrases_required")
        phrases = tuple(_text(item, "invalid_proposal_trigger_phrase", 80) for item in phrases)
        if len(set(phrases)) != len(phrases):
            raise PolicyUpdateError("duplicate_proposal_trigger_phrase")
        self.proposal = CandidateProposal(
            _text(proposal["id"], "invalid_proposal_id"),
            _text(proposal["source_failure_id"], "invalid_source_failure_id"),
            _text(proposal["policy_revision"], "invalid_policy_revision"),
            phrases,
            _text(proposal["purpose"], "invalid_proposal_purpose", 600),
        )
        try:
            self.calibration = EvaluationSuite(raw["calibration"])
            self.gate = EvaluationSuite(raw["gate"])
        except EvaluationError as exc:
            raise PolicyUpdateError("invalid_embedded_evaluation_suite") from exc
        calibration_ids = {case.ticket_id for case in self.calibration.cases}
        gate_ids = {case.ticket_id for case in self.gate.cases}
        if self.proposal.source_failure_id not in calibration_ids:
            raise PolicyUpdateError("source_failure_not_in_calibration")
        if calibration_ids & gate_ids:
            raise PolicyUpdateError("ticket_reused_between_calibration_and_gate")
        calibration_incidents = {case.incident_id for case in self.calibration.cases}
        gate_incidents = {case.incident_id for case in self.gate.cases}
        if calibration_incidents & gate_incidents:
            raise PolicyUpdateError("incident_reused_between_calibration_and_gate")
        self.digest = hashlib.sha256(_canonical(raw).encode()).hexdigest()

    @classmethod
    def from_file(cls, path=None):
        path = Path(path or Path(__file__).resolve().parents[1] / "fixtures" / "policy-update-cases.json")
        return cls(json.loads(path.read_text()))


def candidate_policy(input_data, config, trigger_phrases):
    """The entire candidate is an explicit negative-phrase guard, not a learned rule."""
    text = "\n".join(message.text for message in input_data.messages)
    if "优惠券" in text and any(phrase in text for phrase in trigger_phrases):
        return {
            "action": "ask",
            "facts": [],
            "root_cause": None,
            "questions": ["campaign"],
            "tool_calls": 0,
            "usage": None,
        }
    return baseline_policy(input_data, config)


def _records_by_ticket(records):
    return {record["ticket_id"]: record for record in records}


def _same_output(left, right):
    return left["output"] == right["output"] and left["status"] == right["status"]


def _dimension_not_worse(before, after):
    verdict = {}
    for name, old in before["dimensions"].items():
        new = after["dimensions"][name]
        if old["eligible"] != new["eligible"]:
            verdict[name] = False
        elif old["eligible"] == 0:
            verdict[name] = True
        else:
            verdict[name] = new["passed"] >= old["passed"]
    return verdict


def _run_suite(suite, baseline_config, candidate_config, proposal):
    before = evaluate(suite.cases, baseline_config, baseline_policy)
    after = evaluate(
        suite.cases,
        candidate_config,
        lambda input_data, config: candidate_policy(input_data, config, proposal.trigger_phrases),
    )
    cases = {case.ticket_id: case for case in suite.cases}
    unchanged_outside_scope = True
    changed_ticket_ids = []
    for ticket_id, record in _records_by_ticket(before).items():
        revised = _records_by_ticket(after)[ticket_id]
        if not _same_output(record, revised):
            changed_ticket_ids.append(ticket_id)
            text = "\n".join(event.text for event in replay_input(cases[ticket_id]).messages)
            unchanged_outside_scope = unchanged_outside_scope and "优惠券" in text and any(
                phrase in text for phrase in proposal.trigger_phrases
            )
    return {
        "baseline": {"records": before, "summary": summarize(before)},
        "candidate": {"records": after, "summary": summarize(after)},
        "changed_ticket_ids": changed_ticket_ids,
        "unchanged_outside_scope": unchanged_outside_scope,
    }

def compare_candidate(suite, baseline_config=None):
    """Return a promotion decision; it can only make a candidate eligible for shadowing."""
    baseline_config = baseline_config or EvalConfig(
        "local-ticket-router", "fixture-r1", None, tool_budget=2, max_rounds=4, seconds=45
    )
    candidate_config = EvalConfig(
        "local-ticket-router",
        suite.proposal.policy_revision,
        None,
        tool_budget=2,
        max_rounds=4,
        seconds=45,
    )
    calibration = _run_suite(suite.calibration, baseline_config, candidate_config, suite.proposal)
    gate = _run_suite(suite.gate, baseline_config, candidate_config, suite.proposal)
    calibration_after = _records_by_ticket(calibration["candidate"]["records"])[suite.proposal.source_failure_id]
    calibration_before = _records_by_ticket(calibration["baseline"]["records"])[suite.proposal.source_failure_id]
    source_failure_fixed = (
        calibration_before["score"]["question_coverage"] is False
        and calibration_after["score"]["question_coverage"] is True
    )
    gate_dimensions = _dimension_not_worse(gate["baseline"]["summary"], gate["candidate"]["summary"])
    candidate_calls = sum(record["output"]["tool_calls"] for record in gate["candidate"]["records"]
                          if record["status"] == "completed")
    baseline_calls = sum(record["output"]["tool_calls"] for record in gate["baseline"]["records"]
                          if record["status"] == "completed")
    gates = {
        "source_failure_fixed_in_calibration": source_failure_fixed,
        "calibration_scope_respected": calibration["unchanged_outside_scope"],
        "gate_scope_respected": gate["unchanged_outside_scope"],
        "gate_dimensions_not_worse": all(gate_dimensions.values()),
        "gate_tool_calls_not_higher": candidate_calls <= baseline_calls,
    }
    return {
        "mode": "synthetic_scoped_policy_update",
        "synthetic": True,
        "suite_digest": suite.digest,
        "proposal": asdict(suite.proposal),
        "baseline_config": {**asdict(baseline_config), "digest": baseline_config.digest},
        "candidate_config": {**asdict(candidate_config), "digest": candidate_config.digest},
        "calibration": calibration,
        "gate": {**gate, "dimension_not_worse": gate_dimensions,
                 "baseline_tool_calls": baseline_calls, "candidate_tool_calls": candidate_calls},
        "promotion_gates": gates,
        "decision": "eligible_for_shadow" if all(gates.values()) else "rejected",
        "limitations": [
            "候选来自一个已知失败，校准集只能证明它修复了该失败，不能证明泛化。",
            "promotion gate 为独立合成样本；没有真实模型、飞书或生产活动配置。",
            "eligible_for_shadow 不是线上批准；小范围启用仍需独立版本、范围和人工审核。",
        ],
    }
