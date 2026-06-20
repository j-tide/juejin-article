from pathlib import Path
import tempfile
from .memory import MemoryStore


def experiment():
    with tempfile.TemporaryDirectory() as folder:
        store = MemoryStore(Path(folder) / "memory.db")
        for mid, key, value, parents in [
            ("E1", "raw-source", "source-v1", ()),
            ("M1", "supports-retry", "yes", ("E1",)),
            ("M2", "delivery-plan", "retry-is-safe", ("M1",)),
            ("R1", "report", "ready", ("M2",)),
            ("E2", "unrelated-source", "independent", ()),
        ]:
            store.add(mid, "project-A", key, value, parents)
            assert store.read("project-A", key, 10)["status"] == "MISSING"
            store.approve(mid, 10)
        assert store.read("project-B", "supports-retry", 10)["status"] == "MISSING"
        affected = store.revoke("E1")
        assert affected == ["E1", "M1", "M2", "R1"]
        assert store.read("project-A", "report", 10)["status"] == "MISSING"
        assert store.read("project-A", "unrelated-source", 10)["status"] == "FOUND"
        assert store.read("project-A", "unrelated-source", 1000)["status"] == "MISSING"
        for mid, value in (("C1", "yes"), ("C2", "no")):
            store.add(mid, "project-A", "conflicting-claim", value)
            store.approve(mid, 10)
        conflict = store.read("project-A", "conflicting-claim", 10)["status"]
        assert conflict == "CONFLICT"
        store.db.close()
        return {"episode": 6, "revocation_affected": affected,
                "unrelated_record_survived": True, "cross_scope": "MISSING",
                "expired_record": "MISSING", "conflicting_claims": conflict,
                "boundary": "STALE means revalidation needed, not that every descendant is false."}
