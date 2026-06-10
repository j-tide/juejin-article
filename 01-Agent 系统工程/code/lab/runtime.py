"""Episode 02: local transactional checkpoints with optimistic version checks."""
import hashlib
import json
import os
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    data = value if isinstance(value, bytes) else canonical(value).encode()
    return hashlib.sha256(data).hexdigest()


class VersionConflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(
          task_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
          state TEXT NOT NULL, contract_sha TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          task_id TEXT, version INTEGER, kind TEXT, data TEXT,
          PRIMARY KEY(task_id, version));
        CREATE TABLE IF NOT EXISTS artifacts(
          task_id TEXT, name TEXT, content BLOB, sha TEXT,
          PRIMARY KEY(task_id, name));
        """)

    def create(self, task_id, contract):
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES(?,0,?,?)",
                            (task_id, canonical({"phase": "RUNNING"}), digest(contract)))

    def load(self, task_id):
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return {"task_id": task_id, "version": row["version"],
                "state": json.loads(row["state"]), "contract_sha": row["contract_sha"]}

    def checkpoint(self, task_id, expected_version, state, kind, data,
                   artifact=None, crash=None):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.load(task_id)
            if row["version"] != expected_version:
                raise VersionConflict((expected_version, row["version"]))
            version = expected_version + 1
            if artifact is not None:
                name, content = artifact
                self.db.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?)",
                                (task_id, name, content, digest(content)))
            self.db.execute("INSERT INTO events VALUES(?,?,?,?)",
                            (task_id, version, kind, canonical(data)))
            self.db.execute("UPDATE tasks SET version=?,state=? WHERE task_id=?",
                            (version, canonical(state), task_id))
            if crash == "before_commit":
                os._exit(71)  # deliberately abrupt child-process termination
            self.db.commit()
            if crash == "after_commit":
                os._exit(72)
            return version
        except BaseException:
            self.db.rollback()
            raise

    def artifact(self, task_id, name):
        row = self.db.execute("SELECT content,sha FROM artifacts WHERE task_id=? AND name=?",
                              (task_id, name)).fetchone()
        if row is None:
            return None
        content = bytes(row["content"])
        if digest(content) != row["sha"]:
            raise ValueError("artifact_hash_mismatch")
        return content

    def events(self, task_id):
        rows = self.db.execute("SELECT * FROM events WHERE task_id=? ORDER BY version", (task_id,))
        return [dict(row) | {"data": json.loads(row["data"])} for row in rows]

    def close(self):
        self.db.close()
