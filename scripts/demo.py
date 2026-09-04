"""Run a hero scenario against the real engine.

    python scripts/demo.py                 # list them
    python scripts/demo.py arbitration     # run one
    python scripts/demo.py --all           # run all of them

Nothing here is scripted. Each scenario drives the same decision engine,
guardrail layer and simulator the evaluation uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from recoveryos.demo import SCENARIOS, run_scenario  # noqa: E402

MARK = {
    "info": "  ",
    "decision": "->",
    "block": "!!",
    "money": "**",
    "stop": "##",
}


def show(key: str) -> None:
    result = run_scenario(key)
    line = "=" * 78
    print(f"\n{line}\n{result['title'].upper()}\n{line}")
    print(result["question"])
    print("-" * 78)
    for step in result["steps"]:
        at = f"{step['at']}  " if step.get("at") else ""
        print(f"{MARK.get(step['kind'], '  ')} {at}{step['label']}")
        if step["detail"]:
            for chunk in _wrap(step["detail"], 72):
                print(f"      {chunk}")
    print("-" * 78)
    for para in result["verdict"].split("\n"):
        for chunk in _wrap(para, 78):
            print(chunk)
    print()
    print(result["disclaimer"])


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if not args:
        print("\nRecoveryOS demo scenarios\n")
        for key, scenario in SCENARIOS.items():
            print(f"  {key:<16} {scenario.title}")
            print(f"  {'':<16} {scenario.question}")
        print("\n  python scripts/demo.py <key>    |    python scripts/demo.py --all\n")
        return 0
    keys = list(SCENARIOS) if args[0] == "--all" else args
    for key in keys:
        if key not in SCENARIOS:
            print(f"unknown scenario {key!r}; run with no arguments to list them")
            return 1
        show(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
