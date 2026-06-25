"""Episode 08: independent evidence roots, not number of role labels."""
def assess(reports):
    roots = {root for report in reports for root in report["roots"]}
    claims = {report["claim"] for report in reports}
    if len(claims) > 1:
        return "CONFLICT"
    if len(roots) < 2:
        return "NEEDS_INDEPENDENT_EVIDENCE"
    return "READY_FOR_SEMANTIC_REVIEW"


def independent_majority_error(p):
    return 3 * p * p * (1 - p) + p ** 3
