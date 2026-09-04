"""HTTP surface for the dashboard.

Read-mostly. The two write endpoints rebuild the synthetic world and run a
sweep -- there is nothing here that could touch real money, because there is no
real payment provider behind it.

Money crosses this boundary as integer paise. Formatting is the frontend's
problem; rounding errors are nobody's.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .. import db as dbmod
from ..agent.graph import mermaid
from ..agent.runner import run_batch
from ..audit import ledger
from ..bootstrap import build_database
from ..config import ROOT, settings
from ..engine.scoring import candidates, diagnose, recovery_score, score_all
from ..evaluation.harness import DISCLAIMER, EVAL_DIR
from ..evaluation.metrics import compute
from ..evaluation.policies import DESCRIPTIONS, POLICIES
from ..llm.client import build_narrator
from ..policy import rules as R
from ..policy.guardrails import evaluate, load_policy
from ..schemas import ActionType, CaseState, PolicyDecision
from ..simulator.provider import MockPaymentProvider, load_truths
from ..synthetic import BY_NAME, BASE_NOW

app = FastAPI(
    title="RecoveryOS",
    version="1.0.0",
    description=(
        "Agentic revenue-recovery decision engine. All data is synthetic and all "
        "outcomes are simulated."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clock for the synthetic world. Fixed so screenshots and demos reproduce.
NOW = BASE_NOW


def conn() -> sqlite3.Connection:
    return dbmod.connect()


def _ensure_world() -> None:
    """A first-run convenience: build the world if nobody has generated it yet."""
    try:
        c = conn()
        if c.execute("SELECT COUNT(*) FROM cases").fetchone()[0]:
            c.close()
            return
        c.close()
    except sqlite3.Error:
        pass
    build_database(n_cases=120, seed=settings.seed, now=NOW)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


def case_summary(c, ctx=None) -> dict[str, Any]:
    arch = BY_NAME.get(c.archetype)
    return {
        "case_id": c.case_id,
        "customer_id": c.customer_id,
        "case_type": c.case_type.value,
        "amount_paise": c.amount_paise,
        "failure_reason": c.failure_reason.value,
        "raw_error_code": c.raw_error_code,
        "state": c.state.value,
        "stop_reason": c.stop_reason.value if c.stop_reason else None,
        "recovered_paise": c.recovered_paise,
        "attempts_made": c.attempts_made,
        "is_holdout": c.is_holdout,
        "is_recurring": c.is_recurring,
        "instrument_type": c.instrument_type.value,
        "created_at": c.created_at.isoformat(timespec="seconds"),
        "next_action_at": c.next_action_at.isoformat(timespec="seconds") if c.next_action_at else None,
        "archetype": c.archetype,
        "archetype_lesson": arch.lesson if arch else "",
    }


def scored_json(s) -> dict[str, Any]:
    return {
        "action": s.candidate.action.value,
        "label": s.candidate.label,
        "variant": s.candidate.variant.value if s.candidate.variant else None,
        "delay_hours": s.candidate.delay_hours,
        "rationale": s.candidate.rationale,
        "p_self_cure": s.p_self_cure,
        "p_treated": s.p_treated,
        "uplift": s.uplift,
        "expected_incremental_paise": s.expected_incremental_paise,
        "cost_paise": s.cost_paise,
        "fatigue_penalty_paise": s.fatigue_penalty_paise,
        "risk_penalty_paise": s.risk_penalty_paise,
        "utility_paise": s.utility_paise,
        "explanation": s.explanation,
    }


def verdict_json(v) -> dict[str, Any]:
    return {
        "decision": v.decision.value,
        "rules": [
            {
                "rule_id": r.rule_id,
                "decision": r.decision.value,
                "citation": r.citation,
                "message": r.message,
                "defer_hours": r.defer_hours,
            }
            for r in v.verdicts
        ],
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "clock": NOW.isoformat(timespec="seconds"),
        "llm_enabled": settings.llm_enabled,
        "llm_model": settings.llm_model if settings.llm_enabled else None,
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/overview")
def overview() -> dict[str, Any]:
    _ensure_world()
    c = conn()
    policy = load_policy()
    provider = MockPaymentProvider(load_truths(c), seed=settings.seed)
    decisions_rows = ledger.recent(c, limit=100000)

    cases = dbmod.load_cases(c)
    at_risk = sum(x.amount_paise for x in cases)
    recovered = sum(x.recovered_paise for x in cases)
    open_cases = [x for x in cases if x.state in (CaseState.OPEN, CaseState.WAITING)]

    # Expected recoverable, from the engine's own beliefs -- clearly a forecast,
    # not a promise.
    expected = 0
    for x in open_cases[:400]:
        ctx = dbmod.build_context(c, x.case_id, NOW)
        best = score_all(ctx, policy)[0]
        expected += max(0, best.expected_incremental_paise)

    interventions = sum(
        1 for d in decisions_rows
        if d.get("chosen_action") and not d["chosen_action"].startswith("NO_ACTION")
        and d.get("outcome") not in (None, "NOT_EXECUTED")
    )
    contacts = c.execute(
        "SELECT COUNT(*) FROM contacts WHERE action != 'SEEDED_HISTORY'"
    ).fetchone()[0]
    self_cure_touched = sum(
        1 for x in cases
        if not x.is_holdout and x.attempts_made > 0 and provider.self_cures(x.case_id)
    )

    by_state: dict[str, int] = {}
    by_stop: dict[str, int] = {}
    for x in cases:
        by_state[x.state.value] = by_state.get(x.state.value, 0) + 1
        if x.stop_reason:
            by_stop[x.stop_reason.value] = by_stop.get(x.stop_reason.value, 0) + 1

    treated = [x for x in cases if not x.is_holdout]
    held = [x for x in cases if x.is_holdout]
    # The counterfactual only makes sense over cases that have actually been
    # settled. Counting unresolved exposure here would show a negative
    # incremental on a freshly generated book, before anything has run.
    settled = [x for x in treated if x.state not in (CaseState.OPEN, CaseState.WAITING)]
    treated_selfcure = sum(x.amount_paise for x in settled if provider.self_cures(x.case_id))

    c.close()
    return {
        "clock": NOW.isoformat(timespec="seconds"),
        "cases": len(cases),
        "revenue_at_risk_paise": at_risk,
        "expected_recoverable_paise": expected,
        "recovered_paise": recovered,
        "would_have_recovered_anyway_paise": treated_selfcure,
        "incremental_paise": sum(x.recovered_paise for x in settled) - treated_selfcure,
        "interventions": interventions,
        "customer_contacts": contacts,
        "interventions_on_self_curers": self_cure_touched,
        "human_escalations": by_state.get("ESCALATED", 0),
        "holdout_cases": len(held),
        "open_cases": len(open_cases),
        "by_state": by_state,
        "by_stop_reason": by_stop,
        "pipeline": [
            {"stage": "Revenue at risk", "cases": len(cases), "paise": at_risk},
            {"stage": "Analysed", "cases": len(cases), "paise": at_risk},
            {
                "stage": "Worth intervening",
                "cases": sum(1 for x in cases if x.attempts_made or x.state is not CaseState.OPEN),
                "paise": sum(x.amount_paise for x in treated),
            },
            {"stage": "Actions executed", "cases": interventions, "paise": 0},
            {
                "stage": "Recovered",
                "cases": by_state.get("RECOVERED", 0),
                "paise": recovered,
            },
        ],
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/cases")
def list_cases(
    state: Optional[str] = None,
    archetype: Optional[str] = None,
    limit: int = Query(default=200, le=1000),
) -> dict[str, Any]:
    _ensure_world()
    c = conn()
    rows = dbmod.load_cases(c, [state] if state else None)
    if archetype:
        rows = [r for r in rows if r.archetype == archetype]
    rows = sorted(rows, key=lambda r: -r.amount_paise)[:limit]
    out = [case_summary(r) for r in rows]
    c.close()
    return {"cases": out, "count": len(out)}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict[str, Any]:
    _ensure_world()
    c = conn()
    try:
        case = dbmod.load_case(c, case_id)
    except KeyError:
        c.close()
        raise HTTPException(404, f"no case {case_id}")

    policy = load_policy()
    ctx = dbmod.build_context(c, case_id, NOW)
    text, retryability = diagnose(ctx)
    scored = score_all(ctx, policy)
    gated = [(s, evaluate(ctx, s.candidate, policy)) for s in scored]
    audit = ledger.for_case(c, case_id)

    payload = {
        "case": case_summary(case),
        "customer": {
            "customer_id": ctx.customer.customer_id,
            "name": ctx.customer.name,
            "segment": ctx.customer.segment.value,
            "tenure_months": ctx.customer.tenure_months,
            "lifetime_value_paise": ctx.customer.lifetime_value_paise,
            "prior_payments_ok": ctx.customer.prior_payments_ok,
            "prior_payments_failed": ctx.customer.prior_payments_failed,
            "prior_self_cures": ctx.customer.prior_self_cures,
            "pays_after_payday": ctx.customer.pays_after_payday,
            "preferred_channel": ctx.customer.preferred_channel.value,
            "dlt_consent": ctx.customer.dlt_consent,
            "opted_out": ctx.customer.opted_out,
        },
        "context": {
            "contacts_24h": ctx.contacts_24h,
            "contacts_7d": ctx.contacts_7d,
            "last_contact_at": ctx.last_contact_at.isoformat(timespec="seconds") if ctx.last_contact_at else None,
            "open_sibling_cases": ctx.open_sibling_cases,
            "sibling_max_amount_paise": ctx.sibling_max_amount_paise,
            "hours_since_failure": round(ctx.hours_since_failure, 1),
            "days_overdue": round(ctx.days_overdue, 1),
        },
        "promise": {
            "state": ctx.promise.state.value,
            "promised_for": ctx.promise.promised_for.isoformat(timespec="seconds") if ctx.promise.promised_for else None,
            "promised_amount_paise": ctx.promise.promised_amount_paise,
            "confidence": ctx.promise.confidence,
            "source_text": ctx.promise.source_text,
        },
        "diagnosis": {"text": text, "retryability": retryability.value},
        "recovery_score": recovery_score(ctx, scored[0]),
        "p_self_cure": scored[0].p_self_cure,
        "self_cure_reasoning": next(
            (s.explanation for s in scored if s.candidate.action is ActionType.NO_ACTION), []
        ),
        "candidates": [
            {**scored_json(s), "policy": verdict_json(v)} for s, v in gated
        ],
        "audit": audit,
    }
    c.close()
    return payload


@app.get("/api/activity")
def activity(limit: int = Query(default=60, le=500), run_id: Optional[str] = None) -> dict[str, Any]:
    _ensure_world()
    c = conn()
    entries = ledger.recent(c, limit=limit, run_id=run_id)
    c.close()
    return {"entries": entries}


@app.get("/api/audit/verify")
def verify_audit() -> dict[str, Any]:
    c = conn()
    result = ledger.verify(c)
    c.close()
    return result


@app.get("/api/policy")
def policy_view() -> dict[str, Any]:
    p = load_policy()
    return {
        "policy": json.loads(p.model_dump_json()),
        "rules": [
            {"rule_id": r.rule_id, "citation": r.citation, "description": r.description}
            for r in R.ALL_RULES.values()
        ],
        "afa_ceiling_paise": R.AFA_CEILING_PAISE,
        "afa_ceiling_exempt_paise": R.AFA_CEILING_EXEMPT_PAISE,
    }


@app.get("/api/evaluation")
def evaluation() -> dict[str, Any]:
    path = EVAL_DIR / "latest.json"
    if not path.exists():
        raise HTTPException(
            404, "no evaluation has been run yet: python scripts/run_evaluation.py"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    sensitivity = EVAL_DIR / "sensitivity.json"
    if sensitivity.exists():
        data["sensitivity"] = json.loads(sensitivity.read_text(encoding="utf-8"))
    data["policy_descriptions"] = DESCRIPTIONS
    return data


@app.get("/api/graph")
def graph() -> dict[str, str]:
    return {"mermaid": mermaid()}


@app.get("/api/scenarios")
def scenarios() -> dict[str, Any]:
    from ..demo import SCENARIOS

    return {
        "scenarios": [
            {"key": k, "title": s.title, "question": s.question, "archetype": s.archetype}
            for k, s in SCENARIOS.items()
        ]
    }


@app.post("/api/run")
def run(cases: int = Query(default=120, le=600), seed: Optional[int] = None) -> dict[str, Any]:
    """Rebuild the synthetic world and work it end to end."""
    s = seed if seed is not None else settings.seed
    _, c = build_database(n_cases=cases, seed=s, now=NOW)
    provider = MockPaymentProvider(load_truths(c), seed=s)
    result = run_batch(
        c, provider, chooser=POLICIES["RECOVERYOS"], policy_name="RECOVERYOS",
        now=NOW, narrator=build_narrator(),
    )
    metrics = compute(
        c, provider, result.decisions, "RECOVERYOS", DESCRIPTIONS["RECOVERYOS"],
        load_policy().action_costs_paise,
    )
    c.close()
    return {
        "run_id": result.run_id,
        "rounds": result.rounds,
        "decisions": len(result.decisions),
        "metrics": metrics.to_dict(),
    }


@app.post("/api/demo/{key}")
def run_demo(key: str) -> dict[str, Any]:
    from ..demo import run_scenario

    try:
        return run_scenario(key)
    except KeyError:
        raise HTTPException(404, f"no scenario {key}")
