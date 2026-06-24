from .planning import next_control, run_fixture


def experiment():
    fixed, adaptive = run_fixture(False), run_fixture(True)
    assert fixed["ready"] is False and fixed["tool_calls"] == 6
    assert adaptive["ready"] is True and adaptive["tool_calls"] == 3
    base = {"budget_left": 3, "evidence_version": 1, "plan_evidence_version": 1}
    controls = {
        "unknown_delivery": next_control(base | {"unknown_delivery": True}),
        "contradiction": next_control(base | {"conflicting_evidence": True}),
        "repeat_failure_after_replan": next_control(base | {"stalled": 2, "replans": 1}),
        "exhausted": next_control(base | {"budget_left": 0}),
    }
    assert list(controls.values()) == ["RECONCILE", "ASK_FOR_SCOPE_OR_MORE_EVIDENCE", "ASK_FOR_HELP", "PAUSE_BUDGET"]
    return {"episode": 7, "fixed_plan": fixed, "event_triggered": adaptive,
            "controls": controls, "boundary": "Known fixture fallback, no evidence of LLM planning superiority."}
