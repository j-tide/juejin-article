"""Durable intake/outbox for one worker. Input must come from a verified transport."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from .domain import Scope
from .runtime import render


class Inbox:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS inbox(
              event_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, message_id TEXT NOT NULL,
              root_id TEXT NOT NULL, sender TEXT NOT NULL, created_at TEXT NOT NULL,
              brand TEXT NOT NULL, store TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', UNIQUE(tenant,message_id));
            CREATE TABLE IF NOT EXISTS outbox(
              event_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, body TEXT NOT NULL,
              send_uuid TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
              platform_message_id TEXT, result TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def accept(self, payload, bindings, app_id):
        """Called by official WS SDK only; this method alone does not authenticate HTTP."""
        try:
            header, event = payload["header"], payload["event"]
            if header["app_id"] != app_id or header["event_type"] != "im.message.receive_v1":
                return "ignored"
            sender, msg = event["sender"], event["message"]
            if sender["sender_type"] != "user":
                return "ignored"
            tenant = header["tenant_key"]
            binding = bindings.get(tenant + ":" + msg["chat_id"])
            if not binding:
                return "unbound_chat"
            root = msg.get("root_id") or msg["message_id"]
            if msg.get("parent_id") and not msg.get("root_id"):
                return "missing_root"
            created = msg["create_time"]
            if type(created) not in (str, int) or not str(created).isdigit():
                return "invalid_event"
            required = (header["event_id"], tenant, msg["message_id"], root,
                        sender["sender_id"]["open_id"], str(created), binding["brand"], binding["store"])
            if any(not isinstance(x, str) or not x for x in required):
                return "invalid_event"
            content = json.loads(msg["content"])
            if not isinstance(content, dict):
                return "invalid_event"
            if msg["message_type"] == "text" and not isinstance(content.get("text"), str):
                return "invalid_event"
            normalized = {"message_type": msg["message_type"], "content": content,
                          "chat_id": msg["chat_id"], "parent_id": msg.get("parent_id"),
                          "thread_id": msg.get("thread_id")}
        except (KeyError, TypeError, ValueError):
            return "invalid_event"
        with self.connect() as db:
            cursor = db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?,?,?,?,?,?,?,?,\'pending\')',
                                (*required, json.dumps(normalized, ensure_ascii=False)))
            return "accepted" if cursor.rowcount else "duplicate"

    def recover_single_worker(self):
        """Run only at startup when no other worker is active."""
        with self.connect() as db:
            db.execute("UPDATE inbox SET status='pending' WHERE status='running'")
            # A send may have reached the platform before a crash. Never blindly replay it.
            db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")

    def investigate_one(self, investigate):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM inbox WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return False
            db.execute("UPDATE inbox SET status='running' WHERE event_id=?", (row["event_id"],))
        payload = json.loads(row["payload"])
        try:
            if payload["message_type"] == "text":
                result = investigate(payload["content"]["text"], Scope(row["brand"], row["store"]))
                body = render(result)
            else:
                result = {"status": "media_pending"}
                body = "已保存附件引用。本版本尚未解析图片或视频，请补充文字和订单号供值班人员核对。"
        except Exception:
            # Keep the input for an operator; do not put arbitrary exception text into a chat.
            with self.connect() as db:
                db.execute("UPDATE inbox SET status='failed' WHERE event_id=?", (row["event_id"],))
            return True
        send_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "ticket-reply:" + row["tenant"] + ":" + row["message_id"]))
        with self.connect() as db:
            db.execute('INSERT INTO outbox(event_id,root_id,body,send_uuid,result) VALUES(?,?,?,?,?)',
                       (row["event_id"], row["root_id"], body, send_uuid, json.dumps(result, ensure_ascii=False)))
            db.execute("UPDATE inbox SET status='done' WHERE event_id=?", (row["event_id"],))
        return True

    def send_one(self, sender):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return False
            db.execute("UPDATE outbox SET status='sending' WHERE event_id=?", (row["event_id"],))
        try:
            message_id = sender(row["root_id"], row["body"], row["send_uuid"])
            status = "sent" if isinstance(message_id, str) and message_id else "uncertain"
        except Exception:
            status, message_id = "uncertain", None
        with self.connect() as db:
            db.execute("UPDATE outbox SET status=?,platform_message_id=? WHERE event_id=?",
                       (status, message_id, row["event_id"]))
        return True

    def summary(self):
        with self.connect() as db:
            return {table: [dict(r) for r in db.execute(f"SELECT status,COUNT(*) AS count FROM {table} GROUP BY status")]
                    for table in ("inbox", "outbox")}
