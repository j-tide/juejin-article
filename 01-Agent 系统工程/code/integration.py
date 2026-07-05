"""A deterministic research/report/delivery workflow using the chapter modules.

Approval is a trusted local test fixture. All destinations are local simulations.
"""
import json
from pathlib import Path
import tempfile
from lab.acceptance import Contract, Evidence, GOOD, verify
from lab.authorization import Grant, authorize
from lab.collaboration import assess
from lab.context import compact
from lab.delivery import DeliveryClient, DeliveryService
from lab.memory import MemoryStore
from lab.planning import next_control
from lab.runtime import Store, digest


def experiment():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        store = Store(root / "runtime.db")
        contract = {"version": 1, "review_required": True}
        store.create("task-A", contract)
        sources = {"a": Evidence("a", "fixture://A", "Local fixture A."),
                   "b": Evidence("b", "fixture://B", "Local fixture B.")}
        memory = MemoryStore(root / "memory.db")
        for key, item in sources.items():
            memory.add(key, "project-A", "source:" + key, item.text)
            memory.approve(key, 10)
        memory.add("report-1", "project-A", "report", "fixture report", ("a", "b"))
        memory.approve("report-1", 10)
        view = compact({"task_id": "task-A", "contract_sha": digest(contract), "state_version": 0,
                        "constraints": {"review_required": True}, "pending_operations": [],
                        "failed_paths": [], "narrative": "A report is being prepared.",
                        "evidence": [{"id": k, "text": v.text} for k, v in sources.items()]}, 1000)
        assert view["constraints"]["review_required"]
        assert next_control({"budget_left": 4, "evidence_version": 1, "plan_evidence_version": 1}) == "CONTINUE"
        assert assess([{"roots": ["a"], "claim": "fixture"}, {"roots": ["b"], "claim": "fixture"}]) == "READY_FOR_SEMANTIC_REVIEW"
        body = GOOD.encode()
        assert verify(body, sources, Contract()) == []
        version = store.checkpoint("task-A", 0, {"phase": "REVIEW_READY"}, "artifact_verified",
                                   {"artifact_sha": digest(body)}, ("report", body))
        grant = Grant("task-A", "local-review-inbox", digest(body), 200)
        action = {"kind": "deliver_review", "target": grant.target, "artifact_sha": grant.artifact_sha}
        assert authorize("task-A", action, store.artifact("task-A", "report"), grant, 100) == "allowed"
        service = DeliveryService(root / "service.db")
        client = DeliveryClient(root / "intents.db", service)
        key = "task-A/review-1"
        assert client.send(key, body, lose_reply=True) is None
        version = store.checkpoint("task-A", version, {"phase": "DELIVERY_UNKNOWN"}, "delivery_uncertain", {"op_key": key})
        client.db.close()
        client = DeliveryClient(root / "intents.db", service)
        receipt = client.reconcile(key)
        version = store.checkpoint("task-A", version, {"phase": "DELIVERED"}, "delivery_confirmed", {"receipt": receipt})
        affected = memory.revoke("a")
        assert "report-1" in affected
        version = store.checkpoint("task-A", version,
                                   {"phase": "DELIVERED", "validity": "NEEDS_REVALIDATION"},
                                   "evidence_revoked", {"affected": affected})
        result = {"mode": "local fixture integration, no LLM", "delivery_count": service.count(),
                  "task": store.load("task-A"), "events": store.events("task-A"),
                  "accepted_artifact_unchanged": store.artifact("task-A", "report") == body,
                  "revocation_does_not_undo_delivery": True}
        assert result["delivery_count"] == 1 and version == 4
        store.close()
        for db in (client.db, service.db, memory.db):
            db.close()
        return result


if __name__ == "__main__":
    print(json.dumps(experiment(), ensure_ascii=False, indent=2))
