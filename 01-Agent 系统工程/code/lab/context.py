"""Episode 05: preserve explicit control facts; source bodies remain addressable."""
import copy
from .runtime import canonical, digest


class ContextBudgetExceeded(Exception):
    pass


PINNED = ("task_id", "contract_sha", "state_version", "constraints", "pending_operations", "failed_paths")


def compact(state, budget_chars):
    # This is a character budget for a deterministic lab, not an LLM tokenizer.
    view = {key: copy.deepcopy(state[key]) for key in PINNED}
    view["evidence_handles"] = [{"id": item["id"], "sha": digest(item["text"].encode())}
                                for item in state["evidence"]]
    view["summary"] = ""
    minimum = len(canonical(view))
    if minimum > budget_chars:
        raise ContextBudgetExceeded((minimum, budget_chars))
    view["summary"] = state["narrative"][:budget_chars - minimum]
    # JSON escaping may expand text; trim until the encoded view respects the budget.
    while len(canonical(view)) > budget_chars:
        view["summary"] = view["summary"][:-1]
    return view


def lookup(handle, sources):
    text = sources[handle["id"]]
    if digest(text.encode()) != handle["sha"]:
        raise ValueError("source_version_changed")
    return text


def toy_decision(view):
    """Deliberately naive deterministic consumer; NOT model behavior."""
    if view.get("constraints", {}).get("review_required"):
        return "request_review"
    return "propose_delivery"
