"""Episode 04: a trusted grant is a host object, never parsed from retrieved text."""
from dataclasses import dataclass
from .runtime import digest


@dataclass(frozen=True)
class Grant:
    task_id: str
    target: str
    artifact_sha: str
    expires_at: int
    operation: str = "deliver_review"


def authorize(task_id, action, body, grant, now):
    if not isinstance(action, dict) or set(action) != {"kind", "target", "artifact_sha"}:
        return "invalid_schema"
    if grant is None:
        return "no_grant"
    if task_id != grant.task_id:
        return "wrong_task"
    if now >= grant.expires_at:
        return "expired_grant"
    if action["kind"] != grant.operation:
        return "operation_denied"
    if action["target"] != grant.target:
        return "target_denied"
    if action["artifact_sha"] != grant.artifact_sha or digest(body) != grant.artifact_sha:
        return "artifact_changed"
    return "allowed"
