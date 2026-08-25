"""Explicit local knowledge-investigation entry using the same bounded Agent runtime."""
import argparse
import json
import os
from .domain import Scope
from .knowledge import KnowledgeContext,run_knowledge
from .providers import Replay,DeepSeek
from .runtime import render


def main():
    p=argparse.ArgumentParser(description="活动规则与历史工单检索；默认离线回放")
    p.add_argument("question")
    p.add_argument("--campaign",required=True)
    p.add_argument("--at",required=True,help="故障发生时间，包含时区")
    p.add_argument("--known-at",required=True,help="调查时已知资料的时间截点，包含时区")
    p.add_argument("--brand",default="demo-brand");p.add_argument("--store",default="store-001")
    p.add_argument("--channel");p.add_argument("--product");p.add_argument("--basket-cents",type=int)
    p.add_argument("--live",action="store_true");p.add_argument("--json",action="store_true")
    a=p.parse_args()
    try:
        context=KnowledgeContext(a.campaign,a.at,a.known_at,a.channel,a.product,a.basket_cents)
        provider=DeepSeek(os.getenv("DEEPSEEK_API_KEY"),os.getenv("DEEPSEEK_MODEL","deepseek-flash")) if a.live else Replay()
        result=run_knowledge(a.question,Scope(a.brand,a.store),context,provider)
    except ValueError as e:p.error(str(e))
    print(json.dumps(result,ensure_ascii=False,indent=2) if a.json else render(result))
    return 0 if result["answer"] else 1


if __name__=="__main__":raise SystemExit(main())
