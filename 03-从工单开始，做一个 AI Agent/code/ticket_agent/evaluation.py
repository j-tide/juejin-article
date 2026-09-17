"""Time-aware ticket replay evaluation with separated labels and agent inputs.

This module is intentionally a small local evaluator.  It evaluates a policy
adapter, not a model in a security sandbox, and does not make production claims.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time


class EvaluationError(ValueError):
    pass


def _timestamp(value):
    if not isinstance(value, str):
        raise EvaluationError("timestamp_required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvaluationError("invalid_timestamp") from error
    if parsed.tzinfo is None:
        raise EvaluationError("timezone_required")
    return parsed


def _text(value, code="invalid_text", limit=1500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EvaluationError(code)
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    text: str
    occurred_at: str
    visible_at: str
    audience: str


@dataclass(frozen=True)
class ReplayInput:
    """The policy API deliberately has no labels, future events, or score catalog."""
    ticket_id: str
    incident_id: str
    as_of: str
    tenant: str
    brand: str
    store: str
    messages: tuple[ReplayEvent, ...]


@dataclass(frozen=True)
class EvalConfig:
    policy_id: str
    policy_revision: str
    model_id: str | None
    tool_budget: int
    max_rounds: int
    seconds: int

    def __post_init__(self):
        _text(self.policy_id, "policy_id_required", 120)
        _text(self.policy_revision, "policy_revision_required", 120)
        if self.model_id is not None:
            _text(self.model_id, "invalid_model_id", 120)
        if any(type(value) is not int or value < 1 for value in (self.tool_budget, self.max_rounds, self.seconds)):
            raise EvaluationError("invalid_budget")

    @property
    def digest(self):
        return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationCase:
    ticket_id: str
    incident_id: str
    created_at: str
    as_of: str
    scope: dict
    events: tuple[ReplayEvent, ...]
    labels: dict


def _event(value):
    if not isinstance(value, dict) or set(value) != {"id", "text", "occurred_at", "visible_at", "audience"}:
        raise EvaluationError("invalid_event")
    event_id = _text(value["id"], "event_id_required", 120)
    text = _text(value["text"], "event_text_required", 4000)
    occurred_at = _timestamp(value["occurred_at"])
    visible_at = _timestamp(value["visible_at"])
    if occurred_at > visible_at:
        raise EvaluationError("event_visible_before_occurred")
    if value["audience"] not in {"agent", "scoring_only"}:
        raise EvaluationError("invalid_event_audience")
    return ReplayEvent(event_id, text, value["occurred_at"], value["visible_at"], value["audience"])


def _labels(value):
    expected = {"sample_type", "truth_status", "root_cause", "allowed_actions", "required_questions", "citation_catalog", "required_evidence_ids"}
    if not isinstance(value, dict) or set(value) != expected:
        raise EvaluationError("invalid_labels")
    _text(value["sample_type"], "sample_type_required", 100)
    if value["truth_status"] not in {"confirmed", "unknown"}:
        raise EvaluationError("invalid_truth_status")
    if value["truth_status"] == "confirmed":
        _text(value["root_cause"], "confirmed_root_required", 100)
    elif value["root_cause"] is not None:
        raise EvaluationError("unknown_case_cannot_have_root")
    if not isinstance(value["allowed_actions"], list) or not value["allowed_actions"] or set(value["allowed_actions"]) - {"ask", "handoff"}:
        raise EvaluationError("invalid_allowed_actions")
    if not isinstance(value["required_questions"], list) or len(value["required_questions"]) > 5:
        raise EvaluationError("invalid_required_questions")
    for question in value["required_questions"]:
        _text(question, "invalid_required_question", 100)
    if not isinstance(value["citation_catalog"], list) or len(value["citation_catalog"]) > 20:
        raise EvaluationError("invalid_citation_catalog")
    catalog = {}
    for source in value["citation_catalog"]:
        if not isinstance(source, dict) or set(source) != {"id", "quote"}:
            raise EvaluationError("invalid_citation")
        source_id = _text(source["id"], "citation_id_required", 160)
        quote = _text(source["quote"], "citation_quote_required", 1600)
        if source_id in catalog:
            raise EvaluationError("duplicate_citation_id")
        catalog[source_id] = quote
    if not isinstance(value["required_evidence_ids"], list) or len(value["required_evidence_ids"]) != len(set(value["required_evidence_ids"])):
        raise EvaluationError("invalid_required_evidence")
    if not set(value["required_evidence_ids"]).issubset(catalog):
        raise EvaluationError("required_evidence_not_in_catalog")
    return value


class EvaluationSuite:
    def __init__(self, raw):
        required = {"schema_version", "synthetic", "development_before", "cases"}
        if not isinstance(raw, dict) or set(raw) != required or raw["schema_version"] != "ticket-eval-v1" or type(raw["synthetic"]) is not bool:
            raise EvaluationError("invalid_suite")
        self.synthetic = raw["synthetic"]
        self.development_before = raw["development_before"]
        _timestamp(self.development_before)
        if not isinstance(raw["cases"], list) or len(raw["cases"]) < 2:
            raise EvaluationError("cases_required")
        cases = []
        for item in raw["cases"]:
            expected = {"id", "incident_id", "created_at", "as_of", "scope", "events", "labels"}
            if not isinstance(item, dict) or set(item) != expected:
                raise EvaluationError("invalid_case")
            ticket_id = _text(item["id"], "ticket_id_required", 120)
            incident_id = _text(item["incident_id"], "incident_id_required", 120)
            created_at = _timestamp(item["created_at"])
            as_of = _timestamp(item["as_of"])
            if created_at > as_of:
                raise EvaluationError("case_created_after_as_of")
            scope = item["scope"]
            if not isinstance(scope, dict) or set(scope) != {"tenant", "brand", "store"}:
                raise EvaluationError("invalid_case_scope")
            for value in scope.values():
                _text(value, "invalid_case_scope_value", 120)
            if not isinstance(item["events"], list) or not item["events"]:
                raise EvaluationError("events_required")
            events = tuple(_event(event) for event in item["events"])
            if len({event.event_id for event in events}) != len(events):
                raise EvaluationError("duplicate_event_id")
            labels = _labels(item["labels"])
            cases.append(EvaluationCase(ticket_id, incident_id, item["created_at"], item["as_of"], scope, events, labels))
        if len({case.ticket_id for case in cases}) != len(cases):
            raise EvaluationError("duplicate_ticket_id")
        self.cases = tuple(cases)
        self.digest = hashlib.sha256(canonical(raw).encode()).hexdigest()

    @classmethod
    def from_file(cls, path=None):
        path = Path(path or Path(__file__).resolve().parents[1] / "fixtures" / "evaluation-cases.json")
        return cls(json.loads(path.read_text()))

    def split(self):
        """Put an entire incident in holdout if any of its tickets reaches the cutoff."""
        cutoff = _timestamp(self.development_before)
        grouped = {}
        for case in self.cases:
            grouped.setdefault(case.incident_id, []).append(case)
        development, holdout, allocation = [], [], {}
        for incident_id, cases in grouped.items():
            target = "holdout" if max(_timestamp(case.created_at) for case in cases) >= cutoff else "development"
            allocation[incident_id] = target
            (holdout if target == "holdout" else development).extend(cases)
        if not development or not holdout:
            raise EvaluationError("both_partitions_required")
        return {"development": tuple(sorted(development, key=lambda c: c.ticket_id)),
                "holdout": tuple(sorted(holdout, key=lambda c: c.ticket_id)),
                "allocation": allocation}


def replay_input(case):
    as_of = _timestamp(case.as_of)
    visible = tuple(sorted((event for event in case.events
                            if event.audience == "agent" and _timestamp(event.visible_at) <= as_of),
                           key=lambda event: (_timestamp(event.visible_at), event.event_id)))
    if not visible:
        raise EvaluationError("no_visible_agent_events")
    return ReplayInput(case.ticket_id, case.incident_id, case.as_of, case.scope["tenant"],
                       case.scope["brand"], case.scope["store"], visible)


def normalize_output(value):
    required = {"action", "facts", "root_cause", "questions", "tool_calls", "usage"}
    if not isinstance(value, dict) or set(value) != required:
        raise EvaluationError("invalid_policy_output")
    if value["action"] not in {"ask", "handoff"}:
        raise EvaluationError("invalid_policy_action")
    if not isinstance(value["facts"], list) or len(value["facts"]) > 20:
        raise EvaluationError("invalid_policy_facts")
    facts, ids = [], set()
    for fact in value["facts"]:
        if not isinstance(fact, dict) or set(fact) != {"evidence_id", "quote"}:
            raise EvaluationError("invalid_policy_fact")
        evidence_id = _text(fact["evidence_id"], "policy_evidence_id_required", 160)
        quote = _text(fact["quote"], "policy_quote_required", 1600)
        if evidence_id in ids:
            raise EvaluationError("duplicate_policy_evidence")
        ids.add(evidence_id)
        facts.append({"evidence_id": evidence_id, "quote": quote})
    if value["root_cause"] is not None:
        _text(value["root_cause"], "invalid_policy_root", 100)
    if not isinstance(value["questions"], list) or len(value["questions"]) > 5:
        raise EvaluationError("invalid_policy_questions")
    questions = []
    for question in value["questions"]:
        questions.append(_text(question, "invalid_policy_question", 100))
    if type(value["tool_calls"]) is not int or not 0 <= value["tool_calls"] <= 100:
        raise EvaluationError("invalid_tool_calls")
    usage = value["usage"]
    if usage is not None:
        if not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "completion_tokens"}:
            raise EvaluationError("invalid_usage")
        if any(type(amount) is not int or amount < 0 for amount in usage.values()):
            raise EvaluationError("invalid_usage")
    return {"action": value["action"], "facts": facts, "root_cause": value["root_cause"],
            "questions": questions, "tool_calls": value["tool_calls"], "usage": usage}


def score(case, output, config):
    labels = case.labels
    catalog = {source["id"]: source["quote"] for source in labels["citation_catalog"]}
    cited = {fact["evidence_id"] for fact in output["facts"]}
    citation_valid = "not_applicable" if not catalog else all(catalog.get(fact["evidence_id"]) == fact["quote"] for fact in output["facts"])
    required_evidence = "not_applicable" if not labels["required_evidence_ids"] else set(labels["required_evidence_ids"]).issubset(cited)
    question_coverage = "not_applicable" if not labels["required_questions"] else set(labels["required_questions"]).issubset(output["questions"])
    if labels["truth_status"] == "confirmed":
        root_claim = "not_claimed" if output["root_cause"] is None else "correct" if output["root_cause"] == labels["root_cause"] else "incorrect"
    else:
        root_claim = "not_applicable" if output["root_cause"] is None else "unsupported"
    return {"action_appropriate": output["action"] in labels["allowed_actions"],
            "citation_valid": citation_valid,
            "required_evidence_covered": required_evidence,
            "question_coverage": question_coverage,
            "no_unsupported_cause": root_claim not in {"incorrect", "unsupported"},
            "root_cause_claim": root_claim,
            "tool_budget_ok": output["tool_calls"] <= config.tool_budget}


def _dimension(records, name):
    values = [record["score"][name] for record in records if record["status"] == "completed" and record["score"][name] != "not_applicable"]
    return {"passed": sum(value is True for value in values), "eligible": len(values)}


def summarize(records):
    completed = [record for record in records if record["status"] == "completed"]
    by_type = {}
    for record in completed:
        bucket = by_type.setdefault(record["sample_type"], {"n": 0, "invalid_outputs": 0})
        bucket["n"] += 1
    usage = [record["output"]["usage"] for record in completed]
    known_usage = [item for item in usage if item is not None]
    root = {"confirmed_correct": 0, "confirmed_incorrect": 0, "confirmed_not_claimed": 0,
            "unknown_unsupported": 0, "unknown_no_claim": 0}
    for record in completed:
        claim, truth = record["score"]["root_cause_claim"], record["truth_status"]
        if truth == "confirmed":
            root["confirmed_" + claim] += 1
        elif claim == "unsupported":
            root["unknown_unsupported"] += 1
        else:
            root["unknown_no_claim"] += 1
    dimensions = {name: _dimension(records, name) for name in ("action_appropriate", "citation_valid", "required_evidence_covered", "question_coverage", "no_unsupported_cause", "tool_budget_ok")}
    return {"sample_count": len(records), "completed": len(completed),
            "invalid_or_failed": len(records) - len(completed), "by_sample_type": by_type,
            "dimensions": dimensions, "root_cause_claims": root,
            "usage": {"known_runs": len(known_usage), "unknown_runs": len(usage) - len(known_usage),
                      "prompt_tokens_known_total": sum(item["prompt_tokens"] for item in known_usage),
                      "completion_tokens_known_total": sum(item["completion_tokens"] for item in known_usage)}}


def evaluate(cases, config, policy):
    records = []
    for case in cases:
        input_data = replay_input(case)
        started = time.monotonic()
        try:
            output = normalize_output(policy(input_data, config))
            status, error = "completed", None
        except Exception as exc:  # A failed policy run is recorded, not omitted from the denominator.
            output, status, error = None, "invalid_or_failed", type(exc).__name__
        elapsed_ms = round((time.monotonic() - started) * 1000)
        record = {"ticket_id": case.ticket_id, "incident_id": case.incident_id,
                  "sample_type": case.labels["sample_type"], "truth_status": case.labels["truth_status"],
                  "visible_event_ids": [event.event_id for event in input_data.messages],
                  "elapsed_ms": elapsed_ms, "status": status, "error": error, "output": output}
        if output is not None:
            record["score"] = score(case, output, config)
        records.append(record)
    return records


def run_holdout(suite, config, policy):
    split = suite.split()
    records = evaluate(split["holdout"], config, policy)
    return {"mode": "time_and_incident_isolated_ticket_replay", "synthetic": suite.synthetic,
            "suite_digest": suite.digest, "config": {**asdict(config), "digest": config.digest},
            "development_ticket_ids": [case.ticket_id for case in split["development"]],
            "holdout_ticket_ids": [case.ticket_id for case in split["holdout"]],
            "incident_allocation": split["allocation"], "records": records, "summary": summarize(records),
            "limitations": ["策略回调是同一 Python 进程中的可信代码，不是隔离沙箱。",
                            "样本为合成回放，不能外推成线上模型成功率或成本。",
                            "没有总体质量分；各维度分母、未知原因样本和运行失败单独保留。"]}
