"""Run the chapter-13 synthetic policy-update comparison."""
import argparse
import json
from pathlib import Path

from .policy_update import PolicyUpdateSuite, compare_candidate


def run(path=None):
    return compare_candidate(PolicyUpdateSuite.from_file(path))


def main():
    parser = argparse.ArgumentParser(description="合成候选策略比较；不调用模型、飞书或真实活动配置")
    parser.add_argument("--suite")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(args.suite)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(raw)
    print(raw, end="")


if __name__ == "__main__":
    main()
