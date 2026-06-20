"""Episode 06: dependency-aware records, explicit promotion, revocation, and scope."""
import json
import sqlite3


class MemoryStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories(
          id TEXT PRIMARY KEY, scope TEXT, claim_key TEXT, value TEXT,
          status TEXT, expires INTEGER, parents TEXT);
        CREATE TABLE IF NOT EXISTS memory_events(seq INTEGER PRIMARY KEY, id TEXT, action TEXT);
        """)

    def add(self, mid, scope, key, value, parents=(), expires=1000):
        if mid in parents:
            raise ValueError("self_dependency")
        for parent in parents:
            if self.db.execute("SELECT id FROM memories WHERE id=?", (parent,)).fetchone() is None:
                raise KeyError(parent)
        with self.db:
            self.db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?)",
                            (mid, scope, key, value, "CANDIDATE", expires, json.dumps(parents)))
            self.db.execute("INSERT INTO memory_events(id,action) VALUES(?,?)", (mid, "candidate_created"))

    def approve(self, mid, now):
        row = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        if row is None or row["status"] != "CANDIDATE" or row["expires"] <= now:
            raise ValueError("not_approvable")
        for parent in json.loads(row["parents"]):
            p = self.db.execute("SELECT * FROM memories WHERE id=?", (parent,)).fetchone()
            if p["status"] != "ACCEPTED" or p["expires"] <= now or p["scope"] != row["scope"]:
                raise ValueError("invalid_parent")
        with self.db:
            self.db.execute("UPDATE memories SET status='ACCEPTED' WHERE id=?", (mid,))
            self.db.execute("INSERT INTO memory_events(id,action) VALUES(?,?)", (mid, "trusted_review_accepted"))

    def revoke(self, mid):
        rows = [dict(r) for r in self.db.execute("SELECT * FROM memories")]
        if mid not in {r["id"] for r in rows}:
            raise KeyError(mid)
        affected = {mid}
        while True:
            larger = affected | {r["id"] for r in rows if affected.intersection(json.loads(r["parents"]))}
            if larger == affected:
                break
            affected = larger
        with self.db:
            for item in sorted(affected):
                status = "REVOKED" if item == mid else "STALE"
                self.db.execute("UPDATE memories SET status=? WHERE id=?", (status, item))
                self.db.execute("INSERT INTO memory_events(id,action) VALUES(?,?)", (item, status))
        return sorted(affected)

    def read(self, scope, key, now):
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM memories WHERE scope=? AND claim_key=? AND status='ACCEPTED' AND expires>?",
            (scope, key, now))]
        # Parent expiry can occur without a background job; check all ancestors on read.
        def live(mid, seen):
            if mid in seen:
                return False
            row = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
            return (row is not None and row["scope"] == scope and row["status"] == "ACCEPTED"
                    and row["expires"] > now
                    and all(live(p, seen | {mid}) for p in json.loads(row["parents"])))
        rows = [r for r in rows if live(r["id"], set())]
        values = {r["value"] for r in rows}
        return {"status": "CONFLICT" if len(values) > 1 else "FOUND" if rows else "MISSING",
                "records": rows}
