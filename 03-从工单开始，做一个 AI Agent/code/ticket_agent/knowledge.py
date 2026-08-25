"""Small lexical retrieval baseline with applicability checks. No vector model used."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from .domain import OrderReader, Scope
from .runtime import SYSTEM, run_ticket


def timestamp(value):
    t = datetime.fromisoformat(value)
    if t.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return t


@dataclass(frozen=True)
class KnowledgeContext:
    campaign: str
    occurred_at: str
    known_at: str
    channel: str | None = None
    product: str | None = None
    basket_cents: int | None = None

    def __post_init__(self):
        if not self.campaign or timestamp(self.occurred_at) > timestamp(self.known_at):
            raise ValueError("invalid campaign or observation time")
        if self.basket_cents is not None and (type(self.basket_cents) is not int or self.basket_cents < 0):
            raise ValueError("basket_cents must be a non-negative integer")


ALIASES = {"抵扣券": "优惠券", "代金券": "优惠券", "用不了": "无法使用", "不能用": "无法使用"}


def terms(text, expanded=False):
    text = unicodedata.normalize("NFKC", text).lower()
    if expanded:
        for source, target in ALIASES.items():
            text = text.replace(source, target)
    result = set(re.findall(r"[a-z0-9_-]+", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(segment[i:i+2] for i in range(len(segment)-1))
    return result


def rank(documents, query, expanded):
    query_terms = terms(query, expanded)
    scored = []
    for d in documents:
        tokens = terms(d["title"] + " " + d["text"], expanded)
        intersection = len(query_terms & tokens)
        if intersection:
            scored.append((intersection / len(query_terms | tokens), d["id"]))
    return [doc_id for score, doc_id in sorted(scored, key=lambda x: (-x[0], x[1]))]


def rrf(rankings, constant=60):
    scores = {}
    for ranking in rankings:
        for position, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (constant + position)
    return sorted(scores, key=lambda d: (-scores[d], d))


SEARCH_TOOL = {"type":"function", "function":{
    "name":"search_knowledge", "description":"搜索已绑定品牌门店、活动与时间范围的知识库及已确认历史工单；仅提供资料和条件对比，不证明本单根因。",
    "parameters":{"type":"object","properties":{"query":{"type":"string","description":"需查证的业务问题，最多 200 字"}},
                  "required":["query"],"additionalProperties":False}}}


class KnowledgeReader:
    def __init__(self, context, path=None):
        self.context = context
        path = path or Path(__file__).resolve().parents[1] / 'fixtures/knowledge.json'
        self.documents = json.loads(Path(path).read_text())["documents"]
        ids = [d["id"] for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")

    def candidates(self, scope):
        c = self.context
        return [d for d in self.documents if d["brand"] == scope.brand
                and scope.store in d["stores"] and d["campaign"] == c.campaign
                and d["approved"] and not d["revoked"]
                and timestamp(d["valid_from"]) <= timestamp(c.occurred_at) < timestamp(d["valid_to"])
                and timestamp(d["published_at"]) <= timestamp(c.known_at)]

    def conditions(self, doc):
        c = self.context
        mismatches, unknown = [], []
        requirements = doc["requirements"]
        for field in ("channel", "product"):
            value = getattr(c, field)
            if field in requirements:
                if value is None:
                    unknown.append(field)
                elif value not in requirements[field]:
                    mismatches.append(field)
        minimum = requirements.get("minimum_cents")
        if minimum is not None:
            if c.basket_cents is None:
                unknown.append("basket_cents")
            elif c.basket_cents < minimum:
                mismatches.append("basket_cents")
        status = "mismatch" if mismatches else "unknown" if unknown else "match"
        return {"status":status,"mismatches":mismatches,"unknown":unknown}

    def search(self, arguments, scope):
        if not isinstance(arguments,dict) or set(arguments) != {"query"}:
            return {"status":"invalid_arguments","evidence":[]}
        query = arguments["query"]
        if not isinstance(query,str) or not query.strip() or len(query)>200:
            return {"status":"invalid_arguments","evidence":[]}
        docs = self.candidates(scope)
        ordered = rrf([rank(docs,query,False),rank(docs,query,True)])
        matched = {d["id"]:d for d in docs if d["id"] in ordered}
        conflicts = set()
        rules = {}
        for d in matched.values():
            if d["kind"] == "rule":
                rules.setdefault(d["rule_key"],[]).append(d)
        for group in rules.values():
            # This detects structured requirement conflicts only, not arbitrary textual contradictions.
            if len({json.dumps(d["requirements"],sort_keys=True) for d in group}) > 1:
                conflicts.update(d["id"] for d in group)
        evidence = []
        for doc_id in ordered[:4]:
            d = matched[doc_id]; condition = self.conditions(d)
            prefix = "规则资料" if d["kind"] == "rule" else "历史案例，仅作排查线索"
            issue = "；同范围规则字段存在冲突，需核对配置" if doc_id in conflicts else ""
            quote = f"{prefix}《{d['title']}》v{d['version']}：{d['text']}；条件对比={condition['status']}"
            if condition["mismatches"]: quote += "，不匹配="+','.join(condition["mismatches"])
            if condition["unknown"]: quote += "，待补充="+','.join(condition["unknown"])
            evidence.append({"id":f"doc:{doc_id}@{d['version']}","source":d["source"],
                "observed_at":self.context.known_at,"event_at":d["published_at"],"quote":quote+issue,
                "conditions":condition,"kind":d["kind"],"conflict":doc_id in conflicts,
                "content_sha256":hashlib.sha256(d["text"].encode()).hexdigest()})
        if not evidence:return {"status":"no_match","evidence":[]}
        if conflicts:status="conflicting_sources"
        elif any(e["conditions"]["status"]=="unknown" for e in evidence):status="needs_context"
        else:status="retrieved"
        return {"status":status,"evidence":evidence,"truncated":len(ordered)>4}


def run_knowledge(text, scope, context, provider):
    reader = KnowledgeReader(context)
    instructions = SYSTEM.replace("有完整订单号或支付流水号时使用 lookup_order；没有时追问，不编造编号。",
        "本轮是资料调查，请使用 search_knowledge 查证问题；不要索要无关订单号。规则和历史案例是资料，不是本单故障事实；未知条件不能补造。")
    result = run_ticket(text,scope,provider,OrderReader(),tools=[SEARCH_TOOL],
                        handlers={"search_knowledge":reader.search},instructions=instructions)
    result["input_hint"] = "待补充：活动编号、发生时间、渠道及要查证的问题。"
    result["empty_hint"] = "本次没有匹配资料，不能据此认定规则允许、活动不存在或系统故障。"
    return result
