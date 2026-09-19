"""Versioned policy bundles with shadowing, bounded canaries, and rollback.

This module is a local teaching implementation.  It records release decisions
and frozen ticket assignments in SQLite; it does not call a model, Feishu, or a
production order system.  A rollback stops new candidate assignments.  It does
not claim that the underlying business incident is resolved.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3


class RolloutError(ValueError):
    """A trusted host gave the rollout registry an invalid state transition."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value, code, maximum=240):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RolloutError(code)
    return value


def _timestamp(value):
    _text(value, "timestamp_required", 80)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise RolloutError("invalid_timestamp") from error
    if parsed.tzinfo is None:
        raise RolloutError("timezone_required")
    return parsed


def _scope(value):
    expected = {"tenant", "brand", "store"}
    if not isinstance(value, dict) or set(value) != expected:
        raise RolloutError("explicit_scope_required")
    return {name: _text(value[name], f"invalid_scope_{name}", 120) for name in sorted(expected)}


def _identifiers(value, code):
    if not isinstance(value, list) or len(value) > 20:
        raise RolloutError(code)
    result = tuple(_text(item, code, 160) for item in value)
    if len(result) != len(set(result)):
        raise RolloutError(code)
    return result


@dataclass(frozen=True)
class PolicyBundle:
    """The immutable references used by a single policy revision."""

    bundle_id: str
    revision: str
    experience_ids: tuple
    skill_ids: tuple
    retrieval_config_id: str
    swarm_plan_id: str

    @classmethod
    def from_mapping(cls, value):
        expected = {
            "id",
            "revision",
            "experience_ids",
            "skill_ids",
            "retrieval_config_id",
            "swarm_plan_id",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise RolloutError("invalid_policy_bundle_fields")
        return cls(
            _text(value["id"], "invalid_bundle_id", 120),
            _text(value["revision"], "invalid_bundle_revision", 120),
            _identifiers(value["experience_ids"], "invalid_experience_ids"),
            _identifiers(value["skill_ids"], "invalid_skill_ids"),
            _text(value["retrieval_config_id"], "invalid_retrieval_config_id", 160),
            _text(value["swarm_plan_id"], "invalid_swarm_plan_id", 160),
        )

    @property
    def payload(self):
        return {
            "id": self.bundle_id,
            "revision": self.revision,
            "experience_ids": list(self.experience_ids),
            "skill_ids": list(self.skill_ids),
            "retrieval_config_id": self.retrieval_config_id,
            "swarm_plan_id": self.swarm_plan_id,
        }

    @property
    def digest(self):
        return hashlib.sha256(_canonical(self.payload).encode()).hexdigest()

    @property
    def components(self):
        return (
            *(("experience", item) for item in self.experience_ids),
            *(("skill", item) for item in self.skill_ids),
            ("retrieval", self.retrieval_config_id),
            ("swarm", self.swarm_plan_id),
        )


class RolloutRegistry:
    """Small durable registry; callers must supply authenticated reviewer IDs."""

    def __init__(self, path, reviewers):
        self.path = Path(path)
        self.reviewers = frozenset(reviewers)
        if not self.reviewers:
            raise RolloutError("reviewer_required")
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_bundles(
                    bundle_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stable_policy(
                    slot INTEGER PRIMARY KEY CHECK(slot=1), bundle_id TEXT NOT NULL,
                    set_at TEXT NOT NULL, actor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rollouts(
                    rollout_id TEXT PRIMARY KEY, candidate_bundle_id TEXT NOT NULL,
                    baseline_bundle_id TEXT NOT NULL, scope TEXT NOT NULL,
                    shadow_limit INTEGER NOT NULL, canary_limit INTEGER,
                    phase TEXT NOT NULL, evaluation_reference TEXT NOT NULL,
                    created_at TEXT NOT NULL, approved_at TEXT, approved_by TEXT,
                    stopped_at TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS assignments(
                    ticket_id TEXT PRIMARY KEY, rollout_id TEXT,
                    bundle_id TEXT NOT NULL, shadow_bundle_id TEXT, mode TEXT NOT NULL,
                    scope TEXT NOT NULL, assigned_at TEXT NOT NULL, delivered_at TEXT);
                CREATE TABLE IF NOT EXISTS shadow_records(
                    ticket_id TEXT PRIMARY KEY, rollout_id TEXT NOT NULL,
                    baseline_result TEXT NOT NULL, candidate_result TEXT NOT NULL,
                    recorded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback(
                    feedback_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL,
                    rollout_id TEXT, bundle_id TEXT NOT NULL, classification TEXT NOT NULL,
                    note TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bundle_components(
                    bundle_id TEXT NOT NULL, component_kind TEXT NOT NULL,
                    component_id TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(bundle_id, component_kind, component_id));
                CREATE TABLE IF NOT EXISTS rollout_events(
                    n INTEGER PRIMARY KEY AUTOINCREMENT, rollout_id TEXT,
                    action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
                    detail TEXT NOT NULL);
                """
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _reviewer(self, actor):
        if actor not in self.reviewers:
            raise RolloutError("reviewer_required")

    @staticmethod
    def _bundle_from_row(row):
        return PolicyBundle.from_mapping(json.loads(row["payload"]))

    def _bundle(self, db, bundle_id):
        row = db.execute("SELECT * FROM policy_bundles WHERE bundle_id=?", (bundle_id,)).fetchone()
        if not row:
            raise RolloutError("unknown_policy_bundle")
        return self._bundle_from_row(row)

    def _stable(self, db):
        row = db.execute("SELECT bundle_id FROM stable_policy WHERE slot=1").fetchone()
        if not row:
            raise RolloutError("stable_bundle_required")
        return self._bundle(db, row["bundle_id"])

    @staticmethod
    def _event(db, rollout_id, action, actor, at, detail):
        db.execute(
            "INSERT INTO rollout_events(rollout_id,action,actor,at,detail) VALUES(?,?,?,?,?)",
            (rollout_id, action, actor, at, _canonical(detail)),
        )

    def register_bundle(self, value, now):
        """Record a payload once.  Reusing an ID with changed contents is rejected."""
        bundle = PolicyBundle.from_mapping(value)
        _timestamp(now)
        payload = _canonical(bundle.payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT digest FROM policy_bundles WHERE bundle_id=?", (bundle.bundle_id,)
            ).fetchone()
            if existing:
                if existing["digest"] != bundle.digest:
                    raise RolloutError("bundle_id_content_mismatch")
                return {**bundle.payload, "digest": bundle.digest, "registered": False}
            db.execute(
                "INSERT INTO policy_bundles VALUES(?,?,?,?)",
                (bundle.bundle_id, payload, bundle.digest, now),
            )
            db.executemany(
                "INSERT INTO bundle_components VALUES(?,?,?,?)",
                [(bundle.bundle_id, kind, component_id, "active") for kind, component_id in bundle.components],
            )
        return {**bundle.payload, "digest": bundle.digest, "registered": True}

    def set_stable_bundle(self, bundle_id, actor, now, reason):
        self._reviewer(actor)
        _timestamp(now)
        _text(reason, "stable_reason_required", 600)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._bundle(db, bundle_id)
            db.execute(
                "INSERT INTO stable_policy(slot,bundle_id,set_at,actor) VALUES(1,?,?,?) "
                "ON CONFLICT(slot) DO UPDATE SET bundle_id=excluded.bundle_id,set_at=excluded.set_at,actor=excluded.actor",
                (bundle_id, now, actor),
            )
            self._event(db, None, "stable_set", actor, now, {"bundle_id": bundle_id, "reason": reason})

    def create_shadow(self, rollout_id, candidate_bundle_id, scope, shadow_limit, evaluation_reference, actor, now):
        """Start a bounded, reviewer-approved comparison where stable stays outward-facing."""
        self._reviewer(actor)
        _text(rollout_id, "invalid_rollout_id", 120)
        scope = _scope(scope)
        _text(evaluation_reference, "evaluation_reference_required", 240)
        _timestamp(now)
        if type(shadow_limit) is not int or not 1 <= shadow_limit <= 100:
            raise RolloutError("invalid_shadow_limit")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = self._bundle(db, candidate_bundle_id)
            stable = self._stable(db)
            if candidate.bundle_id == stable.bundle_id:
                raise RolloutError("candidate_must_differ_from_stable")
            existing = db.execute("SELECT rollout_id FROM rollouts WHERE rollout_id=?", (rollout_id,)).fetchone()
            if existing:
                raise RolloutError("rollout_id_exists")
            db.execute(
                "INSERT INTO rollouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rollout_id,
                    candidate.bundle_id,
                    stable.bundle_id,
                    _canonical(scope),
                    shadow_limit,
                    None,
                    "shadow",
                    evaluation_reference,
                    now,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            self._event(
                db,
                rollout_id,
                "shadow_started",
                actor,
                now,
                {"candidate_bundle_id": candidate.bundle_id, "baseline_bundle_id": stable.bundle_id, "scope": scope},
            )
        return {"rollout_id": rollout_id, "phase": "shadow", "baseline_bundle_id": stable.bundle_id,
                "candidate_bundle_id": candidate.bundle_id, "scope": scope, "shadow_limit": shadow_limit}

    @staticmethod
    def _assignment_view(row):
        return {
            "ticket_id": row["ticket_id"],
            "rollout_id": row["rollout_id"],
            "bundle_id": row["bundle_id"],
            "shadow_bundle_id": row["shadow_bundle_id"],
            "mode": row["mode"],
            "scope": json.loads(row["scope"]),
            "assigned_at": row["assigned_at"],
            "delivered_at": row["delivered_at"],
        }

    @staticmethod
    def _count_assignments(db, rollout_id, mode):
        return db.execute(
            "SELECT COUNT(*) AS n FROM assignments WHERE rollout_id=? AND mode=?",
            (rollout_id, mode),
        ).fetchone()["n"]

    def _matching_rollout(self, db, scope):
        rows = db.execute(
            "SELECT * FROM rollouts WHERE phase IN ('shadow','canary') ORDER BY created_at DESC,rollout_id DESC"
        ).fetchall()
        for row in rows:
            if json.loads(row["scope"]) == scope:
                return row
        return None

    def route_ticket(self, ticket_id, scope, now):
        """Freeze a bundle at the first route so later changes cannot switch a live ticket."""
        _text(ticket_id, "invalid_ticket_id", 160)
        scope = _scope(scope)
        _timestamp(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            if existing:
                assignment = self._assignment_view(existing)
                if assignment["scope"] != scope:
                    raise RolloutError("ticket_scope_changed")
                return {**assignment, "reused_assignment": True}
            stable = self._stable(db)
            rollout = self._matching_rollout(db, scope)
            rollout_id, bundle_id, shadow_bundle_id, mode = None, stable.bundle_id, None, "stable"
            if rollout and rollout["phase"] == "shadow":
                if self._count_assignments(db, rollout["rollout_id"], "shadow") < rollout["shadow_limit"]:
                    rollout_id = rollout["rollout_id"]
                    shadow_bundle_id = rollout["candidate_bundle_id"]
                    mode = "shadow"
            elif rollout and rollout["phase"] == "canary":
                if self._count_assignments(db, rollout["rollout_id"], "canary") < rollout["canary_limit"]:
                    rollout_id = rollout["rollout_id"]
                    bundle_id = rollout["candidate_bundle_id"]
                    mode = "canary"
            db.execute(
                "INSERT INTO assignments(ticket_id,rollout_id,bundle_id,shadow_bundle_id,mode,scope,assigned_at,delivered_at) "
                "VALUES(?,?,?,?,?,?,?,NULL)",
                (ticket_id, rollout_id, bundle_id, shadow_bundle_id, mode, _canonical(scope), now),
            )
            row = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            return {**self._assignment_view(row), "reused_assignment": False}

    @staticmethod
    def _result(value):
        expected = {"action", "questions", "note"}
        if not isinstance(value, dict) or set(value) != expected:
            raise RolloutError("invalid_policy_result")
        action = _text(value["action"], "invalid_policy_action", 80)
        if not isinstance(value["questions"], list) or len(value["questions"]) > 5:
            raise RolloutError("invalid_policy_questions")
        questions = [_text(question, "invalid_policy_question", 100) for question in value["questions"]]
        return {"action": action, "questions": questions, "note": _text(value["note"], "invalid_policy_note", 600)}

    def record_shadow(self, ticket_id, baseline_result, candidate_result, now):
        """Persist a dual-run observation; the recorded candidate response is never delivered here."""
        _timestamp(now)
        baseline_result, candidate_result = self._result(baseline_result), self._result(candidate_result)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assignment = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not assignment or assignment["mode"] != "shadow":
                raise RolloutError("shadow_assignment_required")
            if db.execute("SELECT ticket_id FROM shadow_records WHERE ticket_id=?", (ticket_id,)).fetchone():
                raise RolloutError("shadow_record_exists")
            db.execute(
                "INSERT INTO shadow_records VALUES(?,?,?,?,?)",
                (ticket_id, assignment["rollout_id"], _canonical(baseline_result), _canonical(candidate_result), now),
            )
            self._event(
                db,
                assignment["rollout_id"],
                "shadow_recorded",
                "system",
                now,
                {"ticket_id": ticket_id, "delivered_bundle_id": assignment["bundle_id"],
                 "candidate_bundle_id": assignment["shadow_bundle_id"]},
            )

    def approve_canary(self, rollout_id, canary_limit, actor, now, reason):
        """A reviewer, not the candidate score, authorizes the move out of shadow mode."""
        self._reviewer(actor)
        _timestamp(now)
        _text(reason, "canary_reason_required", 600)
        if type(canary_limit) is not int or not 1 <= canary_limit <= 100:
            raise RolloutError("invalid_canary_limit")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rollout = db.execute("SELECT * FROM rollouts WHERE rollout_id=?", (rollout_id,)).fetchone()
            if not rollout or rollout["phase"] != "shadow":
                raise RolloutError("shadow_rollout_required")
            observed = db.execute(
                "SELECT COUNT(*) AS n FROM shadow_records WHERE rollout_id=?", (rollout_id,)
            ).fetchone()["n"]
            if observed < 1:
                raise RolloutError("shadow_evidence_required")
            db.execute(
                "UPDATE rollouts SET phase='canary',canary_limit=?,approved_at=?,approved_by=?,reason=? WHERE rollout_id=?",
                (canary_limit, now, actor, reason, rollout_id),
            )
            self._event(
                db,
                rollout_id,
                "canary_approved",
                actor,
                now,
                {"shadow_records": observed, "canary_limit": canary_limit, "reason": reason},
            )

    def mark_delivered(self, ticket_id, now):
        _timestamp(now)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assignment = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not assignment:
                raise RolloutError("ticket_assignment_required")
            if assignment["delivered_at"] is None:
                db.execute("UPDATE assignments SET delivered_at=? WHERE ticket_id=?", (now, ticket_id))
            return {"ticket_id": ticket_id, "delivered_bundle_id": assignment["bundle_id"],
                    "mode": assignment["mode"], "delivered_at": now}

    def record_feedback(self, feedback_id, ticket_id, classification, note, actor, now):
        """Keep a report separate from verification.  This method never changes rollout state."""
        _text(feedback_id, "invalid_feedback_id", 160)
        _text(note, "feedback_note_required", 1000)
        _timestamp(now)
        if classification not in {"reported_issue", "verified_regression"}:
            raise RolloutError("invalid_feedback_classification")
        if classification == "verified_regression":
            self._reviewer(actor)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            assignment = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not assignment:
                raise RolloutError("ticket_assignment_required")
            if db.execute("SELECT feedback_id FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchone():
                raise RolloutError("feedback_id_exists")
            db.execute(
                "INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?)",
                (feedback_id, ticket_id, assignment["rollout_id"], assignment["bundle_id"], classification, note, actor, now),
            )
            if assignment["rollout_id"]:
                self._event(
                    db,
                    assignment["rollout_id"],
                    "feedback_recorded",
                    actor,
                    now,
                    {"feedback_id": feedback_id, "ticket_id": ticket_id, "classification": classification},
                )

    def _candidate_only_components(self, db, candidate_bundle_id, stable_bundle_id):
        candidate = self._bundle(db, candidate_bundle_id)
        stable = self._bundle(db, stable_bundle_id)
        stable_components = set(stable.components)
        return [component for component in candidate.components if component not in stable_components]

    def rollback(self, rollout_id, feedback_id, actor, now, reason):
        """Stop future candidate assignments and mark candidate-only inputs for review."""
        self._reviewer(actor)
        _timestamp(now)
        _text(reason, "rollback_reason_required", 1000)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rollout = db.execute("SELECT * FROM rollouts WHERE rollout_id=?", (rollout_id,)).fetchone()
            if not rollout or rollout["phase"] != "canary":
                raise RolloutError("canary_rollout_required")
            feedback = db.execute("SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchone()
            if not feedback or feedback["rollout_id"] != rollout_id:
                raise RolloutError("rollout_feedback_required")
            if feedback["classification"] != "verified_regression":
                raise RolloutError("verified_regression_required")
            if feedback["bundle_id"] != rollout["candidate_bundle_id"]:
                raise RolloutError("feedback_must_target_candidate")
            components = self._candidate_only_components(
                db, rollout["candidate_bundle_id"], rollout["baseline_bundle_id"]
            )
            db.execute(
                "UPDATE rollouts SET phase='rolled_back',stopped_at=?,reason=? WHERE rollout_id=?",
                (now, reason, rollout_id),
            )
            for kind, component_id in components:
                db.execute(
                    "UPDATE bundle_components SET state='needs_review' "
                    "WHERE bundle_id=? AND component_kind=? AND component_id=?",
                    (rollout["candidate_bundle_id"], kind, component_id),
                )
            self._event(
                db,
                rollout_id,
                "rolled_back",
                actor,
                now,
                {"feedback_id": feedback_id, "baseline_bundle_id": rollout["baseline_bundle_id"],
                 "candidate_components_needing_review": components, "reason": reason},
            )
            return {"rollout_id": rollout_id, "phase": "rolled_back",
                    "new_ticket_bundle_id": rollout["baseline_bundle_id"],
                    "candidate_components_needing_review": [
                        {"kind": kind, "id": component_id} for kind, component_id in components
                    ]}

    def component_states(self, bundle_id):
        with self.connect() as db:
            self._bundle(db, bundle_id)
            rows = db.execute(
                "SELECT component_kind,component_id,state FROM bundle_components "
                "WHERE bundle_id=? ORDER BY component_kind,component_id",
                (bundle_id,),
            ).fetchall()
            return [
                {"kind": row["component_kind"], "id": row["component_id"], "state": row["state"]}
                for row in rows
            ]

    def trace(self, ticket_id):
        """Return the frozen assignment plus its shadow, feedback, and rollout event evidence."""
        with self.connect() as db:
            assignment = db.execute("SELECT * FROM assignments WHERE ticket_id=?", (ticket_id,)).fetchone()
            if not assignment:
                raise RolloutError("ticket_assignment_required")
            rollout = None
            events, feedback, shadow = [], [], None
            if assignment["rollout_id"]:
                rollout = db.execute("SELECT * FROM rollouts WHERE rollout_id=?", (assignment["rollout_id"],)).fetchone()
                events = [
                    {"action": row["action"], "actor": row["actor"], "at": row["at"], "detail": json.loads(row["detail"])}
                    for row in db.execute(
                        "SELECT action,actor,at,detail FROM rollout_events WHERE rollout_id=? ORDER BY n",
                        (assignment["rollout_id"],),
                    )
                ]
                feedback = [
                    {"feedback_id": row["feedback_id"], "classification": row["classification"],
                     "actor": row["actor"], "note": row["note"], "created_at": row["created_at"]}
                    for row in db.execute(
                        "SELECT feedback_id,classification,actor,note,created_at FROM feedback "
                        "WHERE ticket_id=? ORDER BY created_at,feedback_id",
                        (ticket_id,),
                    )
                ]
                row = db.execute("SELECT * FROM shadow_records WHERE ticket_id=?", (ticket_id,)).fetchone()
                if row:
                    shadow = {
                        "baseline_result": json.loads(row["baseline_result"]),
                        "candidate_result": json.loads(row["candidate_result"]),
                        "recorded_at": row["recorded_at"],
                    }
            rollout_view = None if rollout is None else {
                "rollout_id": rollout["rollout_id"],
                "phase": rollout["phase"],
                "baseline_bundle_id": rollout["baseline_bundle_id"],
                "candidate_bundle_id": rollout["candidate_bundle_id"],
                "scope": json.loads(rollout["scope"]),
                "evaluation_reference": rollout["evaluation_reference"],
                "approved_by": rollout["approved_by"],
                "stopped_at": rollout["stopped_at"],
            }
            return {"assignment": self._assignment_view(assignment), "rollout": rollout_view,
                    "shadow": shadow, "feedback": feedback, "events": events}
