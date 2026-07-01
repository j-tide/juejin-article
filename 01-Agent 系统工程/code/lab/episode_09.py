from .evaluation import macro_scores, task_scores


def experiment():
    tasks = [{"task": label, "n": 5, "c": success}
             for label, success in zip("ABCD", (5, 4, 2, 0))]
    k1, k2 = macro_scores(tasks, 1), macro_scores(tasks, 2)
    assert abs(k1["pass_at_k"] - .55) < 1e-12
    assert abs(k2["pass_at_k"] - .675) < 1e-12
    assert abs(k2["pass_all_k"] - .425) < 1e-12
    try:
        task_scores(2, 1, 3)
        raise AssertionError("k greater than n accepted")
    except ValueError:
        pass
    return {"episode": 9, "dataset": "synthetic teaching counts, not model runs", "tasks": tasks,
            "k_1": k1, "k_2": k2, "n_too_small": "explicit_error",
            "assumptions": "Same task definition and reset conditions; exchangeability required for future-trial interpretation."}
