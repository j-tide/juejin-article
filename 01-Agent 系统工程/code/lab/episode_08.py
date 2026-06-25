from .collaboration import assess, independent_majority_error


def experiment():
    same_source = [{"role": role, "claim": "retry-always-safe", "roots": ["root-A"]}
                   for role in ("researcher", "writer", "reviewer")]
    check = {"role": "independent-reviewer", "claim": "requires-idempotency", "roots": ["root-B"]}
    decision = assess(same_source)
    conflict = assess(same_source + [check])
    p, q, residual = 0.2, 0.15, 0.1
    correlated = q + (1 - q) * independent_majority_error(residual)
    assert decision == "NEEDS_INDEPENDENT_EVIDENCE"
    assert conflict == "CONFLICT"
    assert abs(independent_majority_error(p) - 0.104) < 1e-12
    assert abs(correlated - 0.1738) < 1e-12
    return {"episode": 8, "three_votes_one_root": decision,
            "contradicting_independent_root": conflict,
            "analytic_independent_error_p_0_2": round(independent_majority_error(p), 4),
            "analytic_shared_failure_q_0_15_residual_p_0_1": round(correlated, 4),
            "analytic_correlated_single_agent_error": q + (1 - q) * residual,
            "boundary": "Analytical assumptions and scripted provenance fixtures, not multi-model benchmarks."}
