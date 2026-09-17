"""Run the chapter-12 holdout with a fixed local policy adapter."""
import argparse
import json
from pathlib import Path
import re
from .domain import OrderReader, Scope
from .evaluation import EvalConfig, EvaluationSuite, run_holdout
from .providers import Replay
from .runtime import run_ticket


CONFIG = EvalConfig("local-ticket-router", "fixture-r1", None, tool_budget=2, max_rounds=4, seconds=45)


def policy(input_data, config):
    text = "\n".join(message.text for message in input_data.messages)
    if "优惠券" in text:
        questions = ["channel", "product"] if "活动编号" in text else ["campaign"]
        return {"action": "ask", "facts": [], "root_cause": None, "questions": questions,
                "tool_calls": 0, "usage": None}
    if re.search(r"\b[OP][0-9]{4,12}\b", text):
        result = run_ticket(text, Scope(input_data.brand, input_data.store), Replay(), OrderReader(),
                            max_tools=config.tool_budget, max_rounds=config.max_rounds, seconds=config.seconds)
        answer = result["answer"] or {"facts": [], "next_step": "ask_reference"}
        return {"action": "handoff" if answer["next_step"] == "handoff" else "ask",
                "facts": answer["facts"], "root_cause": None,
                "questions": [] if answer["next_step"] == "handoff" else ["order_reference"],
                "tool_calls": len(result["queries"]),
                # Replay is fixed local control flow, so its zero placeholder is not token usage.
                "usage": None}
    return {"action": "handoff", "facts": [], "root_cause": None, "questions": [],
            "tool_calls": 0, "usage": None}


def replay(path=None):
    return run_holdout(EvaluationSuite.from_file(path), CONFIG, policy)


def main():
    parser = argparse.ArgumentParser(description="合成工单回放评测；默认固定本地策略，不调用模型或飞书")
    parser.add_argument("--suite")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = replay(args.suite)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(raw)
    print(raw, end="")


if __name__ == "__main__":
    main()
