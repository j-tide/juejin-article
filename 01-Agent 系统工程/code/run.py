import argparse
import importlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
available = sorted(path.stem.rsplit("_", 1)[1] for path in (Path(__file__).parent / "lab").glob("episode_*.py"))
parser.add_argument("episode", choices=available)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
result = importlib.import_module(f"lab.episode_{args.episode}").experiment()
result["scope"] = "Local deterministic experiment; no live model or network calls."
text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
print(text, end="")
