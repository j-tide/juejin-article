"""Episode 09: finite-sample combinatorial estimators for exchangeable trials."""
from math import comb


def task_scores(n, c, k):
    if not (0 <= c <= n and 1 <= k <= n):
        raise ValueError("invalid_trial_counts_or_k")
    total = comb(n, k)
    return {"pass_at_k": 1 - (comb(n - c, k) if n - c >= k else 0) / total,
            "pass_all_k": (comb(c, k) if c >= k else 0) / total}


def macro_scores(tasks, k):
    scores = [task_scores(task["n"], task["c"], k) for task in tasks]
    if not scores:
        raise ValueError("empty_dataset")
    return {key: sum(row[key] for row in scores) / len(scores) for key in scores[0]}
