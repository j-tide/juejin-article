import json
from pathlib import Path
import subprocess
import sys
import tempfile
from .runtime import Store, VersionConflict, digest

CONTRACT = {"version": 1, "goal": "research report ready for review"}


def experiment():
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for point in ("before_commit", "after_commit"):
            path = Path(folder) / (point + ".db")
            store = Store(path)
            store.create("task-A", CONTRACT)
            store.close()
            child = subprocess.run([sys.executable, "-m", "lab.episode_02", str(path), point],
                                   cwd=Path(__file__).resolve().parents[1], capture_output=True)
            assert child.returncode == (71 if point == "before_commit" else 72)
            store = Store(path)
            recovered = store.load("task-A")
            events = store.events("task-A")
            artifact = store.artifact("task-A", "report")
            expected = int(point == "after_commit")
            assert recovered["version"] == len(events) == expected
            assert (artifact is not None) == bool(expected)
            rows.append({"crash": point, "exit_code": child.returncode,
                         "version": recovered["version"], "events": len(events),
                         "artifact_present": artifact is not None})
            if expected:
                try:
                    store.checkpoint("task-A", 0, {}, "stale_write", {})
                    raise AssertionError("stale write accepted")
                except VersionConflict:
                    pass
            assert recovered["contract_sha"] == digest(CONTRACT)
            store.close()
    return {"episode": 2, "cases": rows, "stale_version_rejected": True,
            "boundary": "Local SQLite commit; remote tool effects are not atomic with this transaction."}


if __name__ == "__main__":
    store = Store(sys.argv[1])
    store.checkpoint("task-A", 0, {"phase": "REVIEW_READY"}, "report_verified",
                     {"source": "fixture"}, ("report", b"fixture report"), sys.argv[2])
