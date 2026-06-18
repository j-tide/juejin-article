from .authorization import Grant, authorize
from .runtime import digest


def experiment():
    body = b"reviewed fixture report"
    grant = Grant("task-A", "local-review-inbox", digest(body), 200)
    action = {"kind": "deliver_review", "target": "local-review-inbox", "artifact_sha": digest(body)}
    assert authorize("task-A", None, body, grant, 100) == "invalid_schema"
    cases = [
        ("authorized_snapshot", "task-A", action, body, grant, 100, "allowed"),
        ("retrieved_text_claims_approval", "task-A", action | {"approved": True}, body, grant, 100, "invalid_schema"),
        ("redirect_target", "task-A", action | {"target": "unapproved-local-sink"}, body, grant, 100, "target_denied"),
        ("modified_after_review", "task-A", action, b"changed", grant, 100, "artifact_changed"),
        ("expired", "task-A", action, body, grant, 200, "expired_grant"),
        ("cross_task", "task-B", action, body, grant, 100, "wrong_task"),
        ("no_trusted_approval", "task-A", action, body, None, 100, "no_grant"),
    ]
    rows = []
    for label, task, proposal, content, permission, now, expected in cases:
        actual = authorize(task, proposal, content, permission, now)
        assert actual == expected, (label, actual)
        rows.append({"case": label, "decision": actual})
    return {"episode": 4, "cases": rows,
            "boundary": "Tests execution authorization, not a model's resistance to prompt injection or report truth."}
