"""Build a fresh synthetic database from a seed.

Shared by the generator script and by the evaluation harness, which needs each
policy to face an identical, pristine book of cases. Same seed in, same bytes
out -- `tests/test_determinism.py` checks that.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from . import db
from .policy.guardrails import load_policy
from .schemas import PromiseState
from .synthetic import BASE_NOW, GeneratedCase, assign_holdout, generate


def build_database(
    db_path: Optional[Path | str] = None,
    n_cases: int = 60,
    seed: int = 42,
    holdout: Optional[float] = None,
    now: datetime = BASE_NOW,
) -> tuple[list[GeneratedCase], object]:
    """Create (or reset) a database and populate it. Returns the cases and conn."""
    if holdout is None:
        holdout = load_policy().holdout_fraction

    cases = generate(n_cases=n_cases, seed=seed, now=now)
    assign_holdout(cases, holdout, seed)

    conn = db.connect(db_path)
    db.init_db(conn, reset=True)
    seen: set[str] = set()

    conn.execute("BEGIN")
    for gc in cases:
        if gc.customer.customer_id not in seen:
            db.insert_customer(conn, gc.customer)
            seen.add(gc.customer.customer_id)
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
    return cases, conn
