"""Optional official SDK adapter; no webhook-only bot can receive these events."""
import argparse
import json
import os
import threading
from pathlib import Path
from .domain import OrderReader
from .inbox import Inbox
from .providers import DeepSeek, Replay
from .runtime import run_ticket


def reply_request(lark, root_id, text, send_uuid):
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
    body = (ReplyMessageRequestBody.builder().content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text").reply_in_thread(True).uuid(send_uuid).build())
    return ReplyMessageRequest.builder().message_id(root_id).request_body(body).build()


def live(db_path, bindings_path, conversation=False):
    import lark_oapi as lark
    app_id, secret = os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    provider = DeepSeek(os.environ["DEEPSEEK_API_KEY"], os.getenv("DEEPSEEK_MODEL", "deepseek-flash"))
    bindings = json.loads(Path(bindings_path).read_text())
    if conversation:
        from .conversation import ConversationInbox
        inbox = ConversationInbox(db_path, bindings)
    else:
        inbox = Inbox(db_path)
    reader = OrderReader()
    client = lark.Client.builder().app_id(app_id).app_secret(secret).log_level(lark.LogLevel.ERROR).build()

    def receive(data):
        payload = json.loads(lark.JSON.marshal(data))
        # The callback returns after durable intake, never after a model request.
        status = inbox.accept(payload, bindings, app_id)
        print("intake:", status, flush=True)

    def send(root, body, send_uuid):
        response = client.im.v1.message.reply(reply_request(lark, root, body, send_uuid))
        if not response.success():
            raise RuntimeError("platform_send_failed")
        return response.data.message_id

    stop = threading.Event()
    def work():
        inbox.recover_single_worker()
        while not stop.is_set():
            inbox.investigate_one(lambda text, scope: run_ticket(text, scope, provider, reader))
            inbox.send_one(send)
            stop.wait(0.2)

    worker = threading.Thread(target=work, name="single-ticket-worker", daemon=True)
    worker.start()
    dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(receive).build()
    try:
        lark.ws.Client(app_id, secret, event_handler=dispatcher, log_level=lark.LogLevel.ERROR).start()
    finally:
        stop.set()


def replay(db_path):
    inbox = Inbox(db_path)
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "feishu-events.json"
    events = json.loads(fixture.read_text())
    binding = {"tenant-demo:chat-demo": {"brand": "demo-brand", "store": "store-001"}}
    statuses = [inbox.accept(event, binding, "app-demo") for event in events]
    inbox.recover_single_worker()
    while inbox.investigate_one(lambda text, scope: run_ticket(text, scope, Replay(), OrderReader())):
        pass
    sent = []
    def record(root, text, uid):
        sent.append({"root_id": root, "body": text, "send_uuid": uid})
        return "replay-out-" + str(len(sent))
    while inbox.send_one(record):
        pass
    return {"mode": "local_event_replay", "intake": statuses, "deliveries": sent, "state": inbox.summary()}


def main():
    p = argparse.ArgumentParser(description="飞书接入：默认本地事件回放，不联网发送消息")
    p.add_argument("--db", required=True, help="运行数据库路径，建议放在临时或专用运行目录")
    p.add_argument("--live", action="store_true")
    p.add_argument("--bindings", help="租户和群到品牌门店的受信任绑定文件")
    p.add_argument("--conversation", action="store_true", help="使用第 03 篇话题状态；请使用独立运行数据库")
    a = p.parse_args()
    if a.live:
        if not a.bindings:
            p.error("--live 需要 --bindings")
        live(a.db, a.bindings, a.conversation)
    else:
        if a.conversation:
            from .conversation_replay import replay_conversation
            result = replay_conversation(a.db)
        else:
            result = replay(a.db)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
