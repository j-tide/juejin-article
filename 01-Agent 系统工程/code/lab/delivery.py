"""Episode 03: separate local intent store and simulated external service."""
import sqlite3
from .runtime import digest


class DeliveryService:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS deliveries(op_key TEXT PRIMARY KEY, body_sha TEXT, receipt TEXT)")
        self.db.commit()

    def submit(self, key, body, lose_reply=False):
        body_sha = digest(body)
        with self.db:
            row = self.db.execute("SELECT body_sha,receipt FROM deliveries WHERE op_key=?", (key,)).fetchone()
            if row and row[0] != body_sha:
                raise ValueError("same_key_different_payload")
            receipt = row[1] if row else "receipt:" + key
            if not row:
                self.db.execute("INSERT INTO deliveries VALUES(?,?,?)", (key, body_sha, receipt))
        if lose_reply:
            raise TimeoutError("commit completed; reply deliberately lost")
        return receipt

    def lookup(self, key):
        row = self.db.execute("SELECT receipt FROM deliveries WHERE op_key=?", (key,)).fetchone()
        return row[0] if row else None

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]


class DeliveryClient:
    def __init__(self, path, service):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS operations(op_key TEXT PRIMARY KEY, body_sha TEXT, state TEXT, receipt TEXT)")
        self.db.commit()
        self.service = service

    def send(self, key, body, lose_reply=False):
        with self.db:
            row = self.db.execute("SELECT body_sha,state,receipt FROM operations WHERE op_key=?", (key,)).fetchone()
            if row and row[0] != digest(body):
                raise ValueError("intent_payload_changed")
            if row and row[1] == "DELIVERED":
                return row[2]
            self.db.execute("INSERT OR IGNORE INTO operations VALUES(?,?,?,NULL)", (key, digest(body), "PENDING"))
        try:
            receipt = self.service.submit(key, body, lose_reply)
        except TimeoutError:
            with self.db:
                self.db.execute("UPDATE operations SET state='UNKNOWN' WHERE op_key=?", (key,))
            return None
        self.record_receipt(key, receipt)
        return receipt

    def record_receipt(self, key, receipt):
        with self.db:
            cursor = self.db.execute("UPDATE operations SET state='DELIVERED',receipt=? WHERE op_key=?", (receipt, key))
            if cursor.rowcount != 1:
                raise KeyError(key)

    def reconcile(self, key):
        receipt = self.service.lookup(key)
        if receipt:
            self.record_receipt(key, receipt)
        return receipt

    def state(self, key):
        return self.db.execute("SELECT state FROM operations WHERE op_key=?", (key,)).fetchone()[0]
