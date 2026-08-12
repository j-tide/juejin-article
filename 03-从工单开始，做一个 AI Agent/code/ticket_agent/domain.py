"""Trusted scope and synthetic, time-stamped business evidence."""
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scope:
    brand: str
    store: str


TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_order",
        "description": "按订单号 O 开头或支付流水号 P 开头查询当前授权门店。只读；返回分阶段事实，不执行补单或退款。",
        "parameters": {
            "type": "object",
            "properties": {"reference": {"type": "string", "description": "完整订单号或支付流水号，如 P1001"}},
            "required": ["reference"],
            "additionalProperties": False,
        },
    },
}


class OrderReader:
    def __init__(self, path=None):
        path = path or Path(__file__).resolve().parents[1] / "fixtures" / "orders.json"
        self.data = json.loads(Path(path).read_text())

    def lookup(self, arguments, scope):
        if not isinstance(arguments, dict) or set(arguments) != {"reference"}:
            return {"status": "invalid_arguments", "evidence": []}
        ref = arguments["reference"]
        if not isinstance(ref, str) or not re.fullmatch(r"[OP][0-9]{4,12}", ref):
            return {"status": "invalid_arguments", "evidence": []}
        # Scope comes from the application, never from the model's JSON.
        matches = [r for r in self.data["orders"] if
                   r["brand"] == scope.brand and r["store"] == scope.store
                   and ref in (r["order_id"], r["payment_id"])]
        if not matches:
            # Do not disclose whether a matching record exists in another tenant.
            return {"status": "not_found", "reference": ref, "evidence": []}
        row = matches[0]
        oid = row["order_id"]
        labels = {"payment": "支付记录", "order": "订单记录", "dispatch": "出单任务", "receipt": "打印回执"}
        evidence = []
        for key, label in labels.items():
            field = row[key]
            evidence.append({
                "id": f"{oid}:{key}", "source": f"fixture://orders/{oid}/{key}",
                "observed_at": self.data["observed_at"], "event_at": field["event_at"],
                "quote": f"{label}：{field['description']}",
            })
        return {"status": "found", "order_id": oid, "reference": ref, "evidence": evidence}
