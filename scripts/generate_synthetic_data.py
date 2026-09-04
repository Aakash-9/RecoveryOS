"""Build the synthetic recovery environment.

    python scripts/generate_synthetic_data.py --cases 60 --seed 42

Deterministic: the same seed produces an identical database every time. The
data is fabricated end to end -- no real customer, merchant, payment or
recovery outcome appears anywhere in it.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from recoveryos import db  # noqa: E402
from recoveryos.policy.guardrails import load_policy  # noqa: E402
from recoveryos.schemas import PromiseState, rupees  # noqa: E402
from recoveryos.synthetic import BASE_NOW, assign_holdout, generate  # noqa: E402


def main() -> int:
    policy = load_policy()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", type=str, default=None)
    ap.add_argument("--holdout", type=float, default=policy.holdout_fraction,
                    help="fraction of non-canonical cases kept as a silent control arm")
    args = ap.parse_args()

    cases = generate(n_cases=args.cases, seed=args.seed, now=BASE_NOW)
    assign_holdout(cases, args.holdout, args.seed)

    conn = db.connect(args.db)
    db.init_db(conn, reset=True)
    seen_customers: set[str] = set()

    conn.execute("BEGIN")
    for gc in cases:
        if gc.customer.customer_id not in seen_customers:
            db.insert_customer(conn, gc.customer)
            seen_customers.add(gc.customer.customer_id)
        db.insert_case(conn, gc.case)
        db.insert_truth(conn, gc.case.case_id, gc.truth.model_dump())
        if gc.promise.state is not PromiseState.NONE:
            db.insert_promise(conn, gc.promise)
        for at in gc.prior_contacts:
            db.insert_contact(
                conn, gc.customer.customer_id, gc.case.case_id, at,
                gc.customer.preferred_channel, "SEEDED_HISTORY",
            )
    conn.execute("COMMIT")

    at_risk = sum(gc.case.amount_paise for gc in cases)
    held = sum(1 for gc in cases if gc.case.is_holdout)
    by_arch = Counter(gc.case.archetype for gc in cases)

    print(f"database        {db.Path(args.db or db.settings.db_path)}")
    print(f"seed            {args.seed}")
    print(f"clock           {BASE_NOW:%Y-%m-%d %H:%M} (Asia/Kolkata, naive)")
    print(f"customers       {len(seen_customers)}")
    print(f"cases           {len(cases)}  ({held} held out as silent control)")
    print(f"revenue at risk INR {rupees(at_risk)}")
    print()
    print(f"{'archetype':<24} {'n':>3}  lesson")
    print("-" * 100)
    from recoveryos.synthetic import BY_NAME

    for name, n in sorted(by_arch.items()):
        print(f"{name:<24} {n:>3}  {BY_NAME[name].lesson[:70]}")
    print()
    print("Synthetic data. Not real merchant, customer or payment data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
