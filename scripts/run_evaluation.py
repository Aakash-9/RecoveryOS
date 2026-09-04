"""Run the controlled policy comparison.

    python scripts/run_evaluation.py --cases 60 --seed 42
    python scripts/run_evaluation.py --sweep 42,43,44,45,46

Results are a controlled simulation on synthetic cases. They are not evidence
about real merchants, customers or recovered revenue.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from recoveryos.evaluation.harness import DISCLAIMER, EVAL_DIR, evaluate_all, sweep_seeds  # noqa: E402
from recoveryos.evaluation.oracle import capture_rate  # noqa: E402
from recoveryos.schemas import rupees  # noqa: E402

ROWS = [
    ("Revenue at risk", lambda m, o: f"INR {rupees(m.revenue_at_risk_paise)}"),
    ("Gross recovered (what a dashboard shows)", lambda m, o: f"INR {rupees(m.recovered_paise)}"),
    ("  would have arrived untouched (exact)", lambda m, o: f"INR {rupees(m.true_counterfactual_paise)}"),
    ("INCREMENTAL RECOVERED (exact)", lambda m, o: f"INR {rupees(m.true_incremental_paise)}"),
    ("  same, estimated from the control arm", lambda m, o: f"INR {rupees(m.incremental_paise)}"),
    ("Capture rate vs lawful oracle", lambda m, o: f"{capture_rate(m.recovered_paise, o):.1%}"),
    ("Cases recovered", lambda m, o: f"{m.cases_recovered}/{m.cases}  ({m.case_recovery_rate:.0%})"),
    ("Interventions executed", lambda m, o: str(m.interventions)),
    ("Customer contacts sent", lambda m, o: str(m.customer_contacts)),
    ("Interventions on self-curers", lambda m, o: str(m.interventions_on_self_curers)),
    ("Human escalations", lambda m, o: str(m.human_escalations)),
    ("Guardrail violations", lambda m, o: str(m.guardrail_violations)),
    ("Customers who opted out", lambda m, o: str(m.customers_opted_out)),
    ("Spend on interventions", lambda m, o: f"INR {rupees(m.spend_paise)}"),
    ("Incremental per contact", lambda m, o: f"INR {rupees(m.true_incremental_per_contact_paise)}"),
    ("Return on intervention spend", lambda m, o: f"{m.return_on_spend:.1f}x"),
    ("Actions per treated case", lambda m, o: f"{m.actions_per_case:.2f}"),
]


def print_table(run) -> None:
    names = list(run.results)
    width = 40
    col = 18
    print()
    print(f"{'':<{width}}" + "".join(f"{n:>{col}}" for n in names))
    print("-" * (width + col * len(names)))
    for label, fn in ROWS:
        cells = "".join(f"{fn(run.results[n], run.oracle):>{col}}" for n in names)
        print(f"{label:<{width}}{cells}")
    print()
    o = run.oracle
    print(f"Lawful oracle ceiling: INR {rupees(o.recoverable_paise)} "
          f"({o.recoverable_cases}/{run.n_cases} cases), of which "
          f"INR {rupees(o.self_cure_paise)} would have arrived untouched.")
    print(f"Genuinely winnable by intervening: INR {rupees(o.winnable_paise)}")
    print()
    any_imprecise = [n for n, m in run.results.items() if not m.counterfactual_is_precise]
    holdout = next(iter(run.results.values()))
    print(f"Control arm: {holdout.holdout_cases} cases held out and never touched, "
          f"INR {rupees(holdout.holdout_at_risk_paise)} at risk.")
    print("Two counterfactuals are shown deliberately. The exact one is knowable only inside a")
    print("simulation. The control-arm estimate is what a real deployment could actually measure.")
    if any_imprecise:
        print("CAVEAT: at this control-arm size the estimate is indicative, not conclusive.")
    print()
    print(DISCLAIMER)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sweep", type=str, default=None, help="comma-separated seeds for a sensitivity run")
    ap.add_argument("--json", type=str, default=None, help="write the full result to this path")
    args = ap.parse_args()

    if args.sweep:
        seeds = [int(s) for s in args.sweep.split(",")]
        data = sweep_seeds(seeds, n_cases=args.cases)
        print(f"\nSensitivity across {len(seeds)} seeds ({args.cases} cases each)")
        print(f"{'policy':<16}{'incremental INR (exact)':>34}{'contacts':>10}{'violations':>12}{'opt-outs':>10}")
        print("-" * 82)
        for name, rows in data.items():
            inc = [r["true_incremental_paise"] for r in rows]
            contacts = [r["customer_contacts"] for r in rows]
            viol = [r["guardrail_violations"] for r in rows]
            spread = f"{rupees(int(statistics.median(inc)))} [{rupees(min(inc))} .. {rupees(max(inc))}]"
            outs = [r["customers_opted_out"] for r in rows]
            print(f"{name:<16}{spread:>34}{int(statistics.median(contacts)):>10}"
                  f"{int(statistics.median(viol)):>12}{int(statistics.median(outs)):>10}")
        print(f"\nMedian across seeds, range in brackets. {DISCLAIMER}")
        out = EVAL_DIR / "sensitivity.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"written: {out}")
        return 0

    run = evaluate_all(n_cases=args.cases, seed=args.seed)
    print_table(run)
    path = Path(args.json) if args.json else EVAL_DIR / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
