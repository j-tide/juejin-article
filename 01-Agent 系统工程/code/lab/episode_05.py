from .context import ContextBudgetExceeded, compact, lookup, toy_decision
from .runtime import canonical, digest


def experiment():
    state = {"task_id": "task-A", "contract_sha": digest({"review_required": True}),
             "state_version": 3, "constraints": {"review_required": True},
             "pending_operations": ["delivery-1:UNKNOWN"],
             "failed_paths": ["search:same-query"],
             "evidence": [{"id": "source-a-v1", "text": "A source with explicit limitations."}],
             "narrative": "Sources have been collected; the report draft is ready. " * 100}
    naive = {"summary": "Sources have been collected; the report draft is ready."}
    view = compact(state, 600)
    assert toy_decision(naive) == "propose_delivery"
    assert toy_decision(view) == "request_review"
    assert view["pending_operations"] == state["pending_operations"]
    assert len(canonical(view)) <= 600
    changed_rejected = False
    try:
        lookup(view["evidence_handles"][0], {"source-a-v1": "different body"})
    except ValueError:
        changed_rejected = True
    try:
        compact(state, 20)
        raise AssertionError("budget violation hidden")
    except ContextBudgetExceeded:
        pass
    return {"episode": 5, "original_chars": len(canonical(state)),
            "structured_chars": len(canonical(view)), "naive_action": toy_decision(naive),
            "structured_action": toy_decision(view), "tiny_budget": "explicit_failure",
            "source_version_change_rejected": changed_rejected,
            "measurement": "Unicode characters in canonical JSON, not tokens or model accuracy"}
