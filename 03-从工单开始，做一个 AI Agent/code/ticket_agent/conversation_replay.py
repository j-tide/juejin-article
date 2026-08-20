"""Replay a correction arriving during a query; no sleeps or platform calls."""
import json
from pathlib import Path
from .conversation import ConversationInbox
from .domain import OrderReader
from .providers import Replay
from .runtime import run_ticket


def replay_conversation(db_path):
    folder = Path(__file__).resolve().parents[1] / "fixtures"
    events = json.loads((folder/"conversation-events.json").read_text())
    bindings = json.loads((folder/"conversation-bindings.example.json").read_text())
    inbox = ConversationInbox(db_path, bindings)
    inbox.recover_single_worker()
    deliveries, stages = [], []

    def add(n):
        return inbox.accept(events[n], bindings, "app-demo")

    def investigate(text,scope):
        return run_ticket(text,scope,Replay(),OrderReader())

    def send(root,body,uid):
        deliveries.append({"root_id":root,"body":body})
        return f"local-conversation-reply-{len(deliveries)}"

    def record(stage):
        with inbox.connect() as db:
            t=db.execute("SELECT version,reference,status,owner FROM topics").fetchone()
            stages.append({"stage":stage,**dict(t)})

    if add(0) == "duplicate":
        return {"mode":"local_conversation_replay","status":"already_replayed","state":inbox.summary()}
    inbox.investigate_one(investigate);inbox.send_one(send);record("缺少对象，追问一次")
    add(1)
    def interrupted(text,scope):
        add(2)
        return investigate(text,scope)
    inbox.investigate_one(interrupted);inbox.send_one(send);record("查询中更正编号，旧结果不发")
    add(3);inbox.investigate_one(investigate);record("后端接手，暂停自动调查")
    add(4);inbox.investigate_one(investigate);inbox.send_one(send);record("认领人放行，调查当前订单")
    with inbox.connect() as db:
        runs=[dict(r) for r in db.execute('SELECT version,disposition FROM topic_runs ORDER BY rowid')]
    return {"mode":"local_conversation_replay","stages":stages,"runs":runs,
            "deliveries":deliveries,"state":inbox.summary()}
