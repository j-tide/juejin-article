"""Chapter 03: persistent topic state and versioned result delivery.

Explicit commands represent confirmed human actions, not inferred intent.
"""
import json
import re
import uuid
from .domain import Scope
from .inbox import Inbox
from .runtime import render

REFERENCE = re.compile(r"\b[OP][0-9]{4,12}\b")
QUESTION = "请补充正在操作的功能、预期和实际表现；如涉及支付或出单，请确认完整订单号或支付流水号。"


class ConversationInbox(Inbox):
    def __init__(self, path, bindings):
        super().__init__(path)
        self.bindings = bindings
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS topics(
              tenant TEXT, root_id TEXT, version INTEGER, last_event TEXT,
              problem TEXT, reference TEXT, status TEXT, owner TEXT,
              asked INTEGER, event_time INTEGER, brand TEXT, store TEXT,
              PRIMARY KEY(tenant,root_id));
            CREATE TABLE IF NOT EXISTS topic_runs(
              run_id TEXT PRIMARY KEY, tenant TEXT, root_id TEXT, version INTEGER,
              result TEXT, disposition TEXT);
            CREATE TABLE IF NOT EXISTS topic_outbox(
              tenant TEXT, root_id TEXT, version INTEGER, body TEXT, send_uuid TEXT,
              status TEXT, platform_message_id TEXT,
              PRIMARY KEY(tenant,root_id,version));
            ''')

    def accept(self, payload, bindings, app_id):
        status = super().accept(payload, bindings, app_id)
        # If the process dies after intake, all later entry points replay unapplied rows.
        self.sync()
        return status

    def _apply_pending(self, db):
        rows = db.execute("SELECT * FROM inbox WHERE status='pending' ORDER BY rowid").fetchall()
        for row in rows:
            key = (row["tenant"], row["root_id"])
            payload = json.loads(row["payload"])
            old = db.execute("SELECT * FROM topics WHERE tenant=? AND root_id=?", key).fetchone()
            stamp = int(row["created_at"])
            if old and stamp < old["event_time"]:
                # Keep late evidence, but do not silently replace a newer confirmed object.
                db.execute("UPDATE inbox SET status='late_needs_review' WHERE event_id=?", (row["event_id"],))
                continue
            t = dict(old) if old else dict(tenant=key[0], root_id=key[1], version=0,
                last_event="", problem="", reference=None, status="waiting_input", owner=None,
                asked=0, event_time=stamp, brand=row["brand"], store=row["store"])
            t.update(version=t["version"]+1, last_event=row["event_id"], event_time=stamp)
            if payload["message_type"] != "text":
                text = ""
            else:
                text = payload["content"]["text"].strip()
            if not t["problem"] and text and not text.startswith("/"):
                t["problem"] = REFERENCE.sub("[编号另行确认]", text)
            operators = self.bindings.get(row["tenant"]+":"+payload["chat_id"], {}).get("operators", [])
            confirmed = re.fullmatch(r"/订单 ([OP][0-9]{4,12})", text)
            if text == "/接手" and row["sender"] in operators:
                t.update(owner=row["sender"], status="human_owned")
            elif text == "/继续" and row["sender"] == t["owner"]:
                t.update(owner=None, status="ready" if t["reference"] else "waiting_input")
            elif text == "/清除订单" or "不是这个单" in text or "单号错了" in text:
                t.update(reference=None, status="human_owned" if t["owner"] else "waiting_confirmation", asked=0)
            elif confirmed:
                t.update(reference=confirmed.group(1), status="human_owned" if t["owner"] else "ready", asked=0)
            elif not text.startswith("/"):
                refs = set(REFERENCE.findall(text))
                if refs and (len(refs) > 1 or t["reference"] not in (None, next(iter(refs)))):
                    # A conflicting mention is not sufficient to select a replacement.
                    t.update(reference=None, status="human_owned" if t["owner"] else "waiting_confirmation", asked=0)
                elif refs and t["reference"] is None and t["status"] != "waiting_confirmation":
                    t.update(reference=next(iter(refs)), status="human_owned" if t["owner"] else "ready")
                elif t["status"] in ("investigating", "needs_human"):
                    t["status"] = "human_owned" if t["owner"] else "ready"
            if t["status"] in ("investigating", "needs_human"):
                # Even an unsupported control message changes the input version. A stale
                # completion must not leave the new version stuck as "investigating".
                t["status"] = "ready" if t["reference"] else "waiting_input"
            if t["status"] in ("waiting_input", "waiting_confirmation") and db.execute(
                    "SELECT 1 FROM topic_outbox WHERE tenant=? AND root_id=? AND status='pending'", key).fetchone():
                t["asked"] = 0  # An unsent prompt invalidated by a new message still needs delivery.
            db.execute('INSERT OR REPLACE INTO topics VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       tuple(t[k] for k in ("tenant","root_id","version","last_event","problem","reference",
                                             "status","owner","asked","event_time","brand","store")))
            db.execute("UPDATE topic_outbox SET status='superseded' WHERE tenant=? AND root_id=? AND status='pending'", key)
            db.execute("UPDATE inbox SET status='applied' WHERE event_id=?", (row["event_id"],))

    def sync(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._apply_pending(db)

    def recover_single_worker(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._apply_pending(db)
            db.execute("UPDATE topics SET status='ready' WHERE status='investigating'")
            db.execute("UPDATE topic_outbox SET status='uncertain' WHERE status='sending'")

    def _enqueue(self, db, topic, body):
        identity = f"{topic['tenant']}:{topic['root_id']}:{topic['version']}"
        db.execute('INSERT INTO topic_outbox VALUES(?,?,?,?,?,\'pending\',NULL)',
                   (topic["tenant"],topic["root_id"],topic["version"],body,
                    str(uuid.uuid5(uuid.NAMESPACE_URL, "topic:"+identity))))

    def investigate_one(self, investigate):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._apply_pending(db)
            t = db.execute("SELECT * FROM topics WHERE status='ready' OR (status IN ('waiting_input','waiting_confirmation') AND asked=0) ORDER BY rowid LIMIT 1").fetchone()
            if t is None:
                return False
            if t["status"] in ("waiting_input", "waiting_confirmation"):
                self._enqueue(db, t, QUESTION + " 可用 /订单 P1001 明确选择，编号仅为格式示例。")
                db.execute("UPDATE topics SET asked=1 WHERE tenant=? AND root_id=?", (t["tenant"],t["root_id"]))
                return True
            db.execute("UPDATE topics SET status='investigating' WHERE tenant=? AND root_id=?", (t["tenant"],t["root_id"]))
        try:
            text = f"本轮已确认编号 {t['reference']}。工单原始现象：{t['problem']}"
            result = investigate(text, Scope(t["brand"], t["store"]))
            # A model querying a different business object must not publish a result for this topic.
            if any(q.get("reference") not in (None, t["reference"]) for q in result.get("queries", [])):
                result = {"status": "wrong_reference", "answer": None, "mode": "guard", "evidence": []}
            body = render(result)
        except Exception:
            result = {"status": "investigation_failed"}
            body = "本轮调查未完成，请值班人员接手。"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._apply_pending(db)
            current = db.execute("SELECT * FROM topics WHERE tenant=? AND root_id=?", (t["tenant"],t["root_id"])).fetchone()
            valid = current["version"] == t["version"] and current["status"] == "investigating"
            db.execute("INSERT INTO topic_runs VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()),t["tenant"],t["root_id"],t["version"],
                json.dumps(result,ensure_ascii=False),"current" if valid else "stale"))
            if valid:
                self._enqueue(db, t, f"依据话题输入版本 v{t['version']}；确认编号 {t['reference']}。\n"+body)
                db.execute("UPDATE topics SET status='needs_human' WHERE tenant=? AND root_id=?", (t["tenant"],t["root_id"]))
        return True

    def send_one(self, sender):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._apply_pending(db)
            row = db.execute("SELECT o.* FROM topic_outbox o JOIN topics t ON o.tenant=t.tenant AND o.root_id=t.root_id AND o.version=t.version WHERE o.status='pending' ORDER BY o.rowid LIMIT 1").fetchone()
            if row is None:
                return False
            key = (row["tenant"],row["root_id"],row["version"])
            db.execute("UPDATE topic_outbox SET status='sending' WHERE tenant=? AND root_id=? AND version=?", key)
        try:
            message_id = sender(row["root_id"],row["body"],row["send_uuid"])
            status = "sent" if isinstance(message_id,str) and message_id else "uncertain"
        except Exception:
            status,message_id = "uncertain",None
        with self.connect() as db:
            db.execute("UPDATE topic_outbox SET status=?,platform_message_id=? WHERE tenant=? AND root_id=? AND version=?", (status,message_id,*key))
        return True

    def summary(self):
        with self.connect() as db:
            return {table: [dict(r) for r in db.execute(f"SELECT status,COUNT(*) AS count FROM {table} GROUP BY status")]
                    for table in ("inbox","topics","topic_outbox")}
