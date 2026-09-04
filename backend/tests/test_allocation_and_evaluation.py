"""Batch allocation, and the honesty properties of the evaluation."""

from __future__ import annotations

from recoveryos import db as dbmod
from recoveryos.agent.runner import run_batch
from recoveryos.bootstrap import build_database
from recoveryos.engine.allocator import Proposal, allocate
from recoveryos.engine.scoring import score_all
from recoveryos.evaluation.harness import evaluate_all
from recoveryos.evaluation.policies import POLICIES
from recoveryos.schemas import ActionType, PolicyDecision
from recoveryos.simulator.provider import MockPaymentProvider, load_truths

SEED = 42


def _proposal(conn, case, now, policy, contacts_7d=1):
    ctx = dbmod.build_context(conn, case.case_id, now)
    best = next(
        s for s in score_all(ctx, policy)
        if s.candidate.action is ActionType.PAYMENT_LINK
    )
    return Proposal(
        case_id=case.case_id,
        customer_id=ctx.customer.customer_id,
        action=best.candidate,
        scored=best,
        contacts_7d=contacts_7d,
        amount_paise=case.amount_paise,
    )


def test_two_cases_for_one_customer_compete_for_the_last_contact_slot(
    world, canonical, now, policy
):
    """The insight no per-case system can reach: a customer is one inbox."""
    conn, _, _ = world
    minor = canonical["ARBITRATION_MINOR"]
    major = canonical["ARBITRATION_MAJOR"]
    assert minor.customer_id == major.customer_id
    assert major.amount_paise > minor.amount_paise

    proposals = [_proposal(conn, c, now, policy) for c in (minor, major)]
    alloc = allocate(proposals, policy)

    assert alloc.grants[major.case_id].granted is True
    assert alloc.grants[minor.case_id].granted is False
    assert "contact slot" in alloc.grants[minor.case_id].reason
    assert alloc.grants[minor.case_id].displaced_by == major.case_id


def test_a_denied_case_waits_rather_than_being_abandoned(world, canonical, now, policy):
    conn, provider, _ = world
    result = run_batch(
        conn, provider, chooser=POLICIES["RECOVERYOS"], now=now,
        run_id="alloc", allocate_batch=True,
    )
    denials = [
        d for d in result.decisions
        if any(v.rule_id.startswith("ALLOCATOR-") for v in d.policy.verdicts)
    ]
    for d in denials:
        assert d.policy.decision is PolicyDecision.DEFER
        assert d.stop_reason is None, "losing a slot is a queue, not a refusal"


def test_the_batch_budget_is_respected(world, canonical, now, policy):
    conn, _, _ = world
    tight = policy.model_copy(update={"intervention_budget_paise": 500})
    proposals = [
        _proposal(conn, c, now, policy, contacts_7d=0)
        for c in list(dbmod.load_cases(conn))[:10]
    ]
    alloc = allocate(proposals, tight)
    assert alloc.spent_paise <= tight.intervention_budget_paise
    assert any(not g.granted for g in alloc.grants.values())


def test_human_capacity_is_finite(world, canonical, now, policy):
    conn, _, _ = world
    capped = policy.model_copy(update={"human_review_capacity": 1})
    proposals = []
    for case in list(dbmod.load_cases(conn))[:6]:
        ctx = dbmod.build_context(conn, case.case_id, now)
        best = next(
            s for s in score_all(ctx, policy)
            if s.candidate.action is ActionType.HUMAN_ESCALATION
        )
        proposals.append(Proposal(
            case_id=case.case_id, customer_id=ctx.customer.customer_id,
            action=best.candidate, scored=best, contacts_7d=0,
            amount_paise=case.amount_paise,
        ))
    alloc = allocate(proposals, capped)
    assert alloc.escalations_used <= 1
    assert sum(1 for g in alloc.grants.values() if not g.granted) >= 1


# --------------------------------------------------------------------------- #
# Evaluation properties
# --------------------------------------------------------------------------- #


def test_the_policy_comparison_holds_its_honesty_properties(tmp_path):
    run = evaluate_all(n_cases=90, seed=SEED, data_dir=tmp_path, record_audit=False)
    naive = run.results["NAIVE"]
    rulebook = run.results["RULEBOOK"]
    compliant = run.results["RULEBOOK+RULES"]
    ros = run.results["RECOVERYOS"]

    # Same book for everyone, or the comparison means nothing.
    at_risk = {m.revenue_at_risk_paise for m in run.results.values()}
    assert len(at_risk) == 1
    assert len({m.cases for m in run.results.values()}) == 1
    assert len({m.treated_selfcure_paise for m in run.results.values()}) == 1, (
        "the counterfactual is a property of the world, not of the policy"
    )

    # RecoveryOS is bounded; the baselines that ignore rules are not.
    assert ros.guardrail_violations == 0
    assert compliant.guardrail_violations == 0
    assert naive.guardrail_violations > 0
    assert rulebook.guardrail_violations > 0

    # Restraint shows up where each baseline is wasteful. They waste in
    # different places: NAIVE burns gateway retries, RULEBOOK burns the
    # customer's patience.
    assert ros.interventions < naive.interventions
    assert ros.retries < naive.retries
    assert ros.customer_contacts < rulebook.customer_contacts
    assert ros.interventions_on_self_curers < naive.interventions_on_self_curers
    assert ros.customers_opted_out == 0

    # Against the like-for-like compliant baseline, decision quality shows up as money.
    assert ros.true_incremental_paise > compliant.true_incremental_paise
    assert ros.true_incremental_per_contact_paise > compliant.true_incremental_per_contact_paise


def test_no_policy_can_exceed_the_oracle(tmp_path):
    run = evaluate_all(n_cases=90, seed=SEED, data_dir=tmp_path, record_audit=False)
    for name, m in run.results.items():
        assert m.recovered_paise <= run.oracle.recoverable_paise, (
            f"{name} recovered more than was theoretically available"
        )


def test_the_holdout_arm_is_genuinely_untouched(tmp_path):
    _, conn = build_database(tmp_path / "holdout.db", n_cases=90, seed=SEED)
    provider = MockPaymentProvider(load_truths(conn), seed=SEED)
    from recoveryos.synthetic import BASE_NOW

    run_batch(conn, provider, chooser=POLICIES["RECOVERYOS"], now=BASE_NOW, run_id="h")

    held = [c for c in dbmod.load_cases(conn) if c.is_holdout]
    assert held
    for case in held:
        assert case.attempts_made == 0
        touched = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE case_id = ? AND action != 'SEEDED_HISTORY'",
            (case.case_id,),
        ).fetchone()[0]
        assert touched == 0, f"{case.case_id} was contacted despite being a control"
        # ...and its outcome is exactly the latent self-cure draw.
        assert bool(case.recovered_paise) == provider.self_cures(case.case_id)
    conn.close()


def test_metrics_arithmetic_is_consistent(tmp_path):
    run = evaluate_all(n_cases=90, seed=SEED, data_dir=tmp_path, record_audit=False)
    for m in run.results.values():
        assert m.treated_cases + m.holdout_cases == m.cases
        assert m.treated_at_risk_paise + m.holdout_at_risk_paise == m.revenue_at_risk_paise
        assert m.true_incremental_paise == m.treated_recovered_paise - m.treated_selfcure_paise
        lo, hi = m.incremental_interval()
        assert lo <= hi
