"""Run every policy against an identical book of cases and report honestly.

Each policy gets its own freshly built database from the same seed, so all
three face byte-identical cases, and the simulator uses common random numbers,
so all three face identical customer behaviour. Any difference in the results
comes from the decision and nowhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..bootstrap import build_database
from ..agent.runner import run_batch
from ..config import ROOT
from ..policy.guardrails import load_policy
from ..simulator.provider import MockPaymentProvider, load_truths
from ..synthetic import BASE_NOW
from .metrics import Metrics, compute
from .oracle import OracleResult, capture_rate, compute_oracle
from .policies import ALLOCATES, DESCRIPTIONS, POLICIES

EVAL_DIR = ROOT / "data" / "evaluation"

DISCLAIMER = (
    "Controlled simulation on synthetic cases. These figures describe a "
    "reproducible model environment, not real merchants, customers, payments or "
    "recovered revenue, and support no causal claim about the real world."
)


@dataclass
class EvaluationRun:
    seed: int
    n_cases: int
    now: datetime
    oracle: OracleResult
    results: dict[str, Metrics] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "n_cases": self.n_cases,
            "clock": self.now.isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER,
            "oracle": {
                "recoverable_paise": self.oracle.recoverable_paise,
                "recoverable_cases": self.oracle.recoverable_cases,
                "self_cure_paise": self.oracle.self_cure_paise,
                "self_cure_cases": self.oracle.self_cure_cases,
                "winnable_paise": self.oracle.winnable_paise,
                "total_at_risk_paise": self.oracle.total_at_risk_paise,
                "note": (
                    "One lawful action per case with perfect foresight. Not achievable "
                    "by any real system; used as a denominator, not a target."
                ),
            },
            "policies": {
                name: {
                    **m.to_dict(),
                    "capture_rate": round(capture_rate(m.recovered_paise, self.oracle), 4),
                }
                for name, m in self.results.items()
            },
        }


def evaluate_all(
    n_cases: int = 60,
    seed: int = 42,
    policies: Optional[list[str]] = None,
    now: datetime = BASE_NOW,
    data_dir: Optional[Path] = None,
    record_audit: bool = True,
) -> EvaluationRun:
    names = policies or list(POLICIES)
    out_dir = Path(data_dir or EVAL_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    merchant_policy = load_policy()

    # The ceiling is computed once, on a pristine book, before anything runs.
    _, oracle_conn = build_database(out_dir / "oracle.db", n_cases, seed, now=now)
    oracle_provider = MockPaymentProvider(load_truths(oracle_conn), seed=seed)
    oracle = compute_oracle(oracle_conn, oracle_provider, now, merchant_policy)
    oracle_conn.close()

    run = EvaluationRun(seed=seed, n_cases=n_cases, now=now, oracle=oracle)

    for name in names:
        _, conn = build_database(out_dir / f"{name.lower()}.db", n_cases, seed, now=now)
        provider = MockPaymentProvider(load_truths(conn), seed=seed)
        result = run_batch(
            conn, provider,
            chooser=POLICIES[name],
            policy_name=name,
            merchant_policy=merchant_policy,
            now=now,
            run_id=f"{name.lower()}-seed{seed}",
            allocate_batch=ALLOCATES[name],
            record_audit=record_audit,
        )
        metrics = compute(
            conn, provider, result.decisions, name, DESCRIPTIONS[name],
            merchant_policy.action_costs_paise,
        )
        conn.execute(
            "UPDATE runs SET metrics_json = ? WHERE run_id = ?",
            (json.dumps(metrics.to_dict()), result.run_id),
        )
        run.results[name] = metrics
        conn.close()

    return run


def sweep_seeds(
    seeds: list[int], n_cases: int = 60, now: datetime = BASE_NOW
) -> dict[str, list[dict]]:
    """Same experiment, different worlds.

    A single seed produces one confident-looking number that means very little.
    Reporting the spread across seeds is the difference between a result and a
    screenshot.
    """
    out: dict[str, list[dict]] = {name: [] for name in POLICIES}
    for s in seeds:
        run = evaluate_all(
            n_cases=n_cases, seed=s, now=now,
            data_dir=EVAL_DIR / f"seed_{s}", record_audit=False,
        )
        for name, m in run.results.items():
            out[name].append({
                "seed": s,
                "incremental_paise": m.incremental_paise,
                "true_incremental_paise": m.true_incremental_paise,
                "interventions_on_self_curers": m.interventions_on_self_curers,
                "customers_opted_out": m.customers_opted_out,
                "recovered_paise": m.recovered_paise,
                "customer_contacts": m.customer_contacts,
                "interventions": m.interventions,
                "human_escalations": m.human_escalations,
                "guardrail_violations": m.guardrail_violations,
                "capture_rate": round(capture_rate(m.recovered_paise, run.oracle), 4),
            })
    return out
