"""Reviewed, scoped experience. No automatic learning or generated code execution."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from .knowledge import timestamp, rank


class MemoryError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


CONDITIONS = {"tenant", "brand", "store", "campaign", "deployment"}


class ExperienceStore:
    """Trusted application API: authenticated operator IDs must be supplied by host."""
    def __init__(self, path, reviewers):
        self.path = Path(path)
        self.reviewers = frozenset(reviewers)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS memories(
                id TEXT PRIMARY KEY, kind TEXT, body TEXT, status TEXT,
                created TEXT, approved_at TEXT, reviewer TEXT);
            CREATE TABLE IF NOT EXISTS uses(
                run_id TEXT, memory_id TEXT, used_at TEXT,
                PRIMARY KEY(run_id, memory_id));
            CREATE TABLE IF NOT EXISTS memory_events(
                n INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT,
                action TEXT, actor TEXT, at TEXT, reason TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _operator(self, actor):
        if actor not in self.reviewers:
            raise MemoryError("reviewer_required")

    def _body(self, value):
        keys = {"title", "conditions", "valid_from", "valid_to", "observations",
                "diagnosis", "counterevidence", "outcome", "sources", "incident", "parents", "steps"}
        if not isinstance(value, dict) or set(value) != keys:
            raise MemoryError("invalid_memory_fields")
        if not isinstance(value["conditions"], dict) or set(value["conditions"]) != CONDITIONS:
            raise MemoryError("explicit_conditions_required")
        if any(not isinstance(v, str) or not v.strip() or v == "unknown" for v in value["conditions"].values()):
            raise MemoryError("unknown_condition")
        if timestamp(value["valid_from"]) >= timestamp(value["valid_to"]):
            raise MemoryError("invalid_validity")
        for key in ("title", "observations", "diagnosis", "counterevidence", "outcome", "incident"):
            if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 1500:
                raise MemoryError("invalid_text")
        if not isinstance(value["sources"], list) or not 1 <= len(value["sources"]) <= 12:
            raise MemoryError("sources_required")
        ids = set()
        for source in value["sources"]:
            if not isinstance(source, dict) or set(source) != {"id", "quote", "role", "verified"}:
                raise MemoryError("invalid_source")
            if not all(isinstance(source[k], str) and 0 < len(source[k]) <= 1500 for k in ("id", "quote")):
                raise MemoryError("invalid_source_text")
            if source["id"] in ids or source["role"] not in ("diagnosis", "outcome", "counterevidence") or type(source["verified"]) is not bool:
                raise MemoryError("invalid_source_role")
            ids.add(source["id"])
        if not isinstance(value["parents"], list) or not all(isinstance(x, str) for x in value["parents"]) or len(value["parents"]) != len(set(value["parents"])) or len(value["parents"]) > 8:
            raise MemoryError("invalid_parents")
        if not all(isinstance(x, str) for x in value["parents"]):
            raise MemoryError("invalid_parent_id")
        if not isinstance(value["steps"], list) or len(value["steps"]) > 3:
            raise MemoryError("invalid_steps")
        for step in value["steps"]:
            # A deliberately small interpreted recipe, not shell/Python/SQL from memory.
            if not isinstance(step, dict) or set(step) != {"tool", "arguments"} or step["tool"] != "search_knowledge":
                raise MemoryError("read_only_recipe_required")
            args = step["arguments"]
            if not isinstance(args, dict) or set(args) != {"query"} or not isinstance(args["query"], str) or not 0 < len(args["query"].strip()) <= 200:
                raise MemoryError("invalid_recipe_arguments")
        if len(canonical(value)) > 16000:
            raise MemoryError("memory_too_large")

    def propose(self, kind, body, actor, now):
        self._operator(actor)
        self._body(body)
        timestamp(now)
        if kind not in ("case", "skill") or (kind == "case" and (body["parents"] or body["steps"])) or (kind == "skill" and (not body["parents"] or not body["steps"])):
            raise MemoryError("invalid_kind_contract")
        raw = canonical(body)
        mid = hashlib.sha256((kind + raw).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM memories WHERE id=?", (mid,)).fetchone()
            if existing:
                return mid
            for parent in body["parents"]:
                row = db.execute("SELECT kind,body FROM memories WHERE id=?", (parent,)).fetchone()
                if not row or row["kind"] != "case" or json.loads(row["body"])["conditions"] != body["conditions"]:
                    raise MemoryError("parent_scope_or_kind_mismatch")
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?)", (mid, kind, raw, "candidate", now, None, None))
            db.execute("INSERT INTO memory_events(memory_id,action,actor,at,reason) VALUES(?,?,?,?,?)", (mid, "proposed", actor, now, "candidate only"))
        return mid

    def _eligible(self, db, mid, context, now):
        row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        if not row or row["status"] != "approved":
            return None
        body = json.loads(row["body"])
        if body["conditions"] != context or timestamp(row["approved_at"]) > timestamp(now):
            return None
        if not timestamp(body["valid_from"]) <= timestamp(now) < timestamp(body["valid_to"]):
            return None
        # Only cases may be parents, so dependency traversal is bounded to one level.
        if any(not self._eligible(db, parent, context, now) for parent in body["parents"]):
            return None
        return {**dict(row), "body": body}

    def approve(self, mid, actor, now, reason):
        self._operator(actor)
        if not isinstance(reason, str) or not reason.strip():
            raise MemoryError("review_reason_required")
        timestamp(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
            if not row or row["status"] != "candidate":
                raise MemoryError("candidate_required")
            body = json.loads(row["body"])
            if timestamp(now) < timestamp(row["created"]) or not timestamp(body["valid_from"]) <= timestamp(now) < timestamp(body["valid_to"]):
                raise MemoryError("review_time_out_of_range")
            verified = {s["role"] for s in body["sources"] if s["verified"]}
            if not {"diagnosis", "counterevidence"} <= verified:
                raise MemoryError("diagnosis_and_counterevidence_required")
            if row["kind"] == "skill":
                parents = [self._eligible(db, p, body["conditions"], now) for p in body["parents"]]
                if any(p is None for p in parents) or len({p["body"]["incident"] for p in parents}) < 2:
                    raise MemoryError("two_distinct_reviewed_incidents_required")
            db.execute("UPDATE memories SET status='approved',approved_at=?,reviewer=? WHERE id=?", (now, actor, mid))
            db.execute("INSERT INTO memory_events(memory_id,action,actor,at,reason) VALUES(?,?,?,?,?)", (mid, "approved", actor, now, reason))

    def find(self, query, context, now, limit=3):
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 200 or type(limit) != int or not 1 <= limit <= 5:
            raise MemoryError("invalid_search")
        with self.connect() as db:
            rows = [self._eligible(db, x[0], context, now) for x in db.execute("SELECT id FROM memories")]
            visible = {r["id"]: r for r in rows if r}
        docs = [{"id": r["id"], "title": r["body"]["title"], "text": r["body"]["observations"]} for r in visible.values()]
        return [{"id": mid, "kind": visible[mid]["kind"], "title": visible[mid]["body"]["title"]}
                for mid in rank(docs, query, True)[:limit]]

    def acquire(self, mid, context, now, run_id):
        if not isinstance(run_id, str) or not run_id:
            raise MemoryError("run_id_required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._eligible(db, mid, context, now)
            if row is None:
                return None
            db.execute("INSERT OR IGNORE INTO uses VALUES(?,?,?)", (run_id, mid, now))
            return row

    def revoke(self, mid, actor, now, reason):
        self._operator(actor)
        timestamp(now)
        if not isinstance(reason, str) or not reason.strip():
            raise MemoryError("revocation_reason_required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT created,approved_at FROM memories WHERE id=?", (mid,)).fetchone()
            if not row or timestamp(now) < timestamp(row["approved_at"] or row["created"]):
                raise MemoryError("invalid_revocation")
            affected = [mid]
            for child in db.execute("SELECT id,body FROM memories WHERE kind='skill'"):
                if mid in json.loads(child["body"])["parents"]:
                    affected.append(child["id"])
            db.execute("UPDATE memories SET status='revoked' WHERE id=?", (mid,))
            db.execute("INSERT INTO memory_events(memory_id,action,actor,at,reason) VALUES(?,?,?,?,?)", (mid, "revoked", actor, now, reason))
            marks = ",".join("?" for _ in affected)
            runs = [x[0] for x in db.execute(f"SELECT DISTINCT run_id FROM uses WHERE memory_id IN ({marks}) ORDER BY run_id", affected)]
            return {"affected_memories": affected, "review_runs": runs}

    def execute(self, mid, context, now, run_id, handlers, scope):
        if (context.get("brand"), context.get("store")) != (scope.brand, scope.store):
            raise MemoryError("execution_scope_mismatch")
        memory = self.acquire(mid, context, now, run_id)
        if not memory or memory["kind"] != "skill":
            return {"status": "fallback", "reason": "memory_unavailable", "evidence": []}
        observations = []
        for step in memory["body"]["steps"]:
            if self.acquire(mid, context, now, run_id) is None:
                return {"status": "fallback", "reason": "memory_revoked", "evidence": observations}
            handler = handlers.get(step["tool"])
            if handler is None:
                return {"status": "fallback", "reason": "tool_unavailable", "evidence": observations}
            try:
                result = handler(step["arguments"], scope)
            except Exception:
                return {"status": "fallback", "reason": "tool_failed", "evidence": observations}
            evidence = result.get("evidence", [])
            observations.extend(evidence)
            if result.get("status") != "retrieved" or not evidence or any(e.get("conditions", {}).get("status") != "match" or e.get("conflict") for e in evidence):
                return {"status": "fallback", "reason": "fresh_evidence_incomplete", "evidence": observations}
        if self.acquire(mid, context, now, run_id) is None:
            return {"status": "fallback", "reason": "memory_revoked", "evidence": observations}
        return {"status": "needs_review", "memory_id": mid, "evidence": observations,
                "note": "复用的是检查步骤，当前根因仍需核验"}
