"""Episode 10: replay a real defect found in episode 01's original validator."""
import re
from .runtime import digest


def reference_check(text, observed, strict):
    if strict:
        raw = re.findall(r"\[E:([^\]\r\n]*)\]", text)
        errors = [] if text.count("[E:") == len(raw) else ["malformed_citation"]
        errors += ["invalid_citation_id:" + x for x in raw if not re.fullmatch(r"[a-z0-9_-]+", x)]
    else:
        raw = re.findall(r"\[E:([a-z0-9_-]+)\]", text)
        errors = []
    return errors + ["unobserved_evidence:" + x for x in sorted(set(raw) - set(observed))]


def experiment():
    text = "结论有两个来源 [E:a] [E:b]，以及未知来源 [E:UNKNOWN]。"
    observed = ["a", "b"]
    before = reference_check(text, observed, False)
    after = reference_check(text, observed, True)
    assert before == [] and "unobserved_evidence:UNKNOWN" in after
    regressions = []
    for label, body, valid in [("valid", "[E:a] [E:b]", True),
                               ("unknown_lower", "[E:missing]", False),
                               ("unknown_upper", "[E:UNKNOWN]", False),
                               ("unclosed", "[E:broken", False),
                               ("empty_id", "[E:]", False)]:
        errors = reference_check(body, observed, True)
        assert (not errors) == valid
        regressions.append({"case": label, "accepted": not errors})
    trace = [
        {"task_id": "task-A", "action_id": "a1", "kind": "evidence_observed", "ids": observed},
        {"task_id": "task-A", "action_id": "a2", "kind": "artifact_written", "sha": digest(text.encode())},
        {"task_id": "task-A", "action_id": "a3", "parent_action_id": "a2", "kind": "validation",
         "validator_version": "original", "errors": before},
    ]
    return {"episode": 10, "frozen_artifact_sha": digest(text.encode()),
            "before_errors": before, "after_errors": after, "regressions": regressions,
            "trace": trace, "boundary": "Evidence for one parser defect; not a causal attribution method for arbitrary LLM failures."}
