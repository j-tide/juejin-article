"""Run a reviewed-memory example using actual local knowledge retrieval."""
import copy
import json
from pathlib import Path
import tempfile
from .experience import ExperienceStore
from .domain import Scope
from .knowledge import KnowledgeReader, KnowledgeContext

NOW = "2026-09-18T15:00:00+08:00"

def seed(store):
    cases = json.loads((Path(__file__).resolve().parents[1] / "fixtures/experience.json").read_text())["cases"]
    ids = []
    for body in cases:
        mid = store.propose("case", body, "reviewer", NOW)
        store.approve(mid, "reviewer", NOW, "核对教学记录中的配置摘录与失败重试记录")
        ids.append(mid)
    skill = copy.deepcopy(cases[0])
    skill.update(title="优惠券范围核对步骤", incident="recipe-1", parents=ids,
                 steps=[{"tool":"search_knowledge", "arguments":{"query":"优惠券无法使用的适用条件"}}])
    sid = store.propose("skill", skill, "reviewer", NOW)
    store.approve(sid, "reviewer", NOW, "仅复用资料核对步骤，不复用历史结论")
    return ids, sid, cases[0]["conditions"]


def replay():
    with tempfile.TemporaryDirectory() as directory:
        store = ExperienceStore(Path(directory)/"memory.sqlite3", {"reviewer"})
        ids, sid, context = seed(store)
        scope = Scope(context["brand"], context["store"])
        reader = KnowledgeReader(KnowledgeContext("campaign-a", NOW, NOW, "miniapp", "milk-tea", 3200))
        handlers = {"search_knowledge":reader.search}
        matched = store.execute(sid, context, NOW, "run-match", handlers, scope)
        changed = store.execute(sid, {**context, "deployment":"release-2"}, NOW, "run-new-version", handlers, scope)
        reader_unknown = KnowledgeReader(KnowledgeContext("campaign-a", NOW, NOW))
        unknown = store.execute(sid, context, NOW, "run-unknown", {"search_knowledge":reader_unknown.search}, scope)
        revoked = store.revoke(ids[0], "reviewer", NOW, "示例：配置摘录后来被发现取错时间点，停止复用")
        after = store.execute(sid, context, NOW, "run-after-revoke", handlers, scope)
        return {"mode":"deterministic_reviewed_memory_with_local_tools", "synthetic":True,
                "matching_context":matched, "changed_deployment":changed,
                "missing_conditions":unknown, "revocation":revoked, "after_revocation":after,
                "limitation":"不含模型调用、自动经验提炼或根因效果评测"}


if __name__ == "__main__":
    print(json.dumps(replay(), ensure_ascii=False, indent=2))
