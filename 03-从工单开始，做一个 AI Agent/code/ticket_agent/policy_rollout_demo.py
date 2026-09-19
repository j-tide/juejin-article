"""Run the chapter-14 local version-release and rollback scenario."""
import argparse
import json
from pathlib import Path
import tempfile

from .policy_rollout import RolloutRegistry


SCOPE = {"tenant": "demo-tenant", "brand": "demo-brand", "store": "store-002"}

STABLE_BUNDLE = {
    "id": "bundle-r1",
    "revision": "coupon-intake-r1",
    "experience_ids": [],
    "skill_ids": [],
    "retrieval_config_id": "retrieval-coupon-r1",
    "swarm_plan_id": "swarm-single-agent-r1",
}

CANDIDATE_BUNDLE = {
    "id": "bundle-r2",
    "revision": "coupon-intake-r2",
    "experience_ids": ["experience-coupon-missing-campaign-r1"],
    "skill_ids": ["skill-coupon-intake-r1"],
    "retrieval_config_id": "retrieval-coupon-r1",
    "swarm_plan_id": "swarm-single-agent-r1",
}


def run(db_path=None):
    if db_path is None:
        with tempfile.TemporaryDirectory() as directory:
            return _run(Path(directory) / "policy-rollout.sqlite3")
    return _run(Path(db_path))


def _run(db_path):
    registry = RolloutRegistry(db_path, reviewers={"ops-zhou"})
    registry.register_bundle(STABLE_BUNDLE, "2026-09-19T10:00:00+08:00")
    registry.register_bundle(CANDIDATE_BUNDLE, "2026-09-19T10:00:01+08:00")
    registry.set_stable_bundle("bundle-r1", "ops-zhou", "2026-09-19T10:00:02+08:00", "initial reviewed bundle")
    rollout = registry.create_shadow(
        "rollout-coupon-r2",
        "bundle-r2",
        SCOPE,
        shadow_limit=1,
        evaluation_reference="policy-update-fixture-r1",
        actor="ops-zhou",
        now="2026-09-19T10:01:00+08:00",
    )
    shadow = registry.route_ticket("topic-shadow-001", SCOPE, "2026-09-19T10:02:00+08:00")
    shadow_delivery = registry.mark_delivered("topic-shadow-001", "2026-09-19T10:02:01+08:00")
    registry.record_shadow(
        "topic-shadow-001",
        {"action": "ask", "questions": ["channel", "product"], "note": "旧规则仍把‘没有提供活动编号’当作已有编号。"},
        {"action": "ask", "questions": ["campaign"], "note": "候选规则要求补充活动编号。"},
        "2026-09-19T10:02:02+08:00",
    )
    registry.approve_canary(
        "rollout-coupon-r2",
        canary_limit=1,
        actor="ops-zhou",
        now="2026-09-19T10:03:00+08:00",
        reason="已核对影子记录，限制为一个门店的一张新话题。",
    )
    canary = registry.route_ticket("topic-canary-001", SCOPE, "2026-09-19T10:04:00+08:00")
    canary_delivery = registry.mark_delivered("topic-canary-001", "2026-09-19T10:04:01+08:00")
    registry.record_feedback(
        "feedback-client-version-report",
        "topic-canary-001",
        "reported_issue",
        "门店补充：客户端版本和活动配置刚刚切换，旧经验可能不再适用。",
        "support-lin",
        "2026-09-19T10:05:00+08:00",
    )
    registry.record_feedback(
        "feedback-client-version-verified",
        "topic-canary-001",
        "verified_regression",
        "值班人员核对配置版本后确认：候选经验的适用条件已失效。",
        "ops-zhou",
        "2026-09-19T10:06:00+08:00",
    )
    rollback = registry.rollback(
        "rollout-coupon-r2",
        "feedback-client-version-verified",
        "ops-zhou",
        "2026-09-19T10:06:01+08:00",
        "停止候选策略；配置版本差异交由人工沿当前证据继续调查。",
    )
    after_rollback = registry.route_ticket("topic-after-rollback", SCOPE, "2026-09-19T10:07:00+08:00")
    frozen_canary = registry.route_ticket("topic-canary-001", SCOPE, "2026-09-19T10:07:01+08:00")
    return {
        "mode": "synthetic_local_policy_rollout",
        "synthetic": True,
        "rollout": rollout,
        "shadow": {"assignment": shadow, "delivery": shadow_delivery, "trace": registry.trace("topic-shadow-001")},
        "canary": {"assignment": canary, "delivery": canary_delivery, "trace": registry.trace("topic-canary-001")},
        "rollback": rollback,
        "after_rollback": after_rollback,
        "frozen_canary_assignment": frozen_canary,
        "candidate_component_states": registry.component_states("bundle-r2"),
        "limitations": [
            "全部话题、策略输出和反馈均为合成数据；没有模型、飞书、数据库或活动配置调用。",
            "shadow、canary 和 rollback 只管理调查策略版本；不修改门店活动，也不表示业务故障已恢复。",
            "反馈分类由受信任宿主和人工审核提供；本地代码不鉴别真实身份或自动确认回归。",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="本地策略版本、影子运行与回滚演示")
    parser.add_argument("--db")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(args.db)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(raw)
    print(raw, end="")


if __name__ == "__main__":
    main()
