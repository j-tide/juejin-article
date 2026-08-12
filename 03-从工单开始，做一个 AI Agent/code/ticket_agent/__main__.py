import argparse
import json
import os
from .domain import OrderReader, Scope
from .providers import DeepSeek, Replay
from .runtime import render, run_ticket


def main():
    parser = argparse.ArgumentParser(description="茶饮工单只读 Demo；默认只回放合成数据")
    parser.add_argument("text")
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--brand", default="demo-brand")
    parser.add_argument("--store", default="store-001")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        provider = Replay() if args.mode == "replay" else DeepSeek(
            os.getenv("DEEPSEEK_API_KEY"), os.getenv("DEEPSEEK_MODEL", "deepseek-flash"))
    except ValueError as exc:
        parser.error(str(exc))
    result = run_ticket(args.text, Scope(args.brand, args.store), provider, OrderReader())
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0 if result["answer"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
