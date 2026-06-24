"""Episode 07: deterministic control policy around a replaceable planner."""
def next_control(state):
    if state["budget_left"] <= 0:
        return "PAUSE_BUDGET"
    if state.get("unknown_delivery"):
        return "RECONCILE"
    if state.get("conflicting_evidence"):
        return "ASK_FOR_SCOPE_OR_MORE_EVIDENCE"
    if state.get("stalled", 0) >= 2:
        return "ASK_FOR_HELP" if state.get("replans", 0) >= 1 else "REPLAN"
    if state.get("plan_evidence_version") != state.get("evidence_version"):
        return "REPLAN"
    return "CONTINUE"


def run_fixture(adaptive):
    # In this controlled world, source A is permanently unavailable; B is accessible.
    # A real planner is not being evaluated here.
    state = {"budget_left": 6, "stalled": 0, "replans": 0,
             "plan_evidence_version": 1, "evidence_version": 1}
    source, events, ready = "A", [], False
    while state["budget_left"] > 0:
        control = next_control(state) if adaptive else "CONTINUE"
        if control == "REPLAN":
            source = "B"
            state["replans"] += 1
            state["stalled"] = 0
        elif control != "CONTINUE":
            break
        state["budget_left"] -= 1
        success = source == "B"
        events.append({"source": source, "success": success})
        if success:
            ready = True
            break
        state["stalled"] += 1
    return {"ready": ready, "tool_calls": len(events), "replans": state["replans"], "events": events}
