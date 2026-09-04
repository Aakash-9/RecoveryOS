"""The guardrail layer, tested on its own.

These are the tests that matter most for a system allowed to spend a merchant's
money and a customer's patience. Each one names the rule it is protecting.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from recoveryos import db as dbmod
from recoveryos.policy import rules as R
from recoveryos.policy.guardrails import defer_hours, evaluate
from recoveryos.schemas import (
    ActionType,
    CandidateAction,
    Channel,
    LinkVariant,
    PolicyDecision,
)

RETRY = CandidateAction(action=ActionType.DELAYED_RETRY, delay_hours=24)
MESSAGE = CandidateAction(action=ActionType.CUSTOMER_MESSAGE, channel=Channel.WHATSAPP)
LINK = CandidateAction(
    action=ActionType.PAYMENT_LINK, variant=LinkVariant.COLLECT_NOW, channel=Channel.WHATSAPP
)
ESCALATE = CandidateAction(action=ActionType.HUMAN_ESCALATION)
NOTHING = CandidateAction(action=ActionType.NO_ACTION)


def ctx_for(conn, canonical, name, now):
    return dbmod.build_context(conn, canonical[name].case_id, now)


def rule_ids(verdict):
    return {v.rule_id for v in verdict.blocking}


def test_retry_cap_blocks_a_third_attempt(world, canonical, now, policy):
    conn, _, _ = world
    v = evaluate(ctx_for(conn, canonical, "RETRY_CAP_EXHAUSTED", now), RETRY, policy)
    assert v.decision is PolicyDecision.BLOCK
    assert R.MERCHANT_RETRY_CAP.rule_id in rule_ids(v)


def test_dead_card_cannot_be_retried(world, canonical, now, policy):
    conn, _, _ = world
    v = evaluate(ctx_for(conn, canonical, "CARD_EXPIRED", now), RETRY, policy)
    assert v.decision is PolicyDecision.BLOCK
    assert R.INSTRUMENT_UNRECOVERABLE.rule_id in rule_ids(v)


def test_revoked_mandate_cannot_be_retried(world, canonical, now, policy):
    conn, _, _ = world
    v = evaluate(ctx_for(conn, canonical, "MANDATE_REVOKED", now), RETRY, policy)
    assert v.decision is PolicyDecision.BLOCK


def test_contact_fatigue_blocks_a_third_message_in_a_week(world, canonical, now, policy):
    conn, _, _ = world
    v = evaluate(ctx_for(conn, canonical, "CONTACT_FATIGUED", now), MESSAGE, policy)
    assert v.decision is PolicyDecision.BLOCK
    assert R.MERCHANT_CONTACT_CAP_7D.rule_id in rule_ids(v)


def test_fatigue_is_pooled_across_a_customers_cases(world, canonical, now, policy):
    """One human, one inbox. Two cases must not each get their own quota."""
    conn, _, _ = world
    minor = ctx_for(conn, canonical, "ARBITRATION_MINOR", now)
    major = ctx_for(conn, canonical, "ARBITRATION_MAJOR", now)
    assert minor.customer.customer_id == major.customer.customer_id
    assert minor.contacts_7d == major.contacts_7d
    assert minor.open_sibling_cases >= 1


def test_quiet_hours_defer_a_late_night_nudge(world, canonical, now, policy):
    """TRAI: a payment chase is commercial communication, not a service alert."""
    conn, _, _ = world
    late = now.replace(hour=22, minute=40)
    ctx = dbmod.build_context(conn, canonical["CART_ABANDONED_HOT"].case_id, late)
    v = evaluate(ctx, MESSAGE, policy)
    assert v.decision is PolicyDecision.DEFER
    assert R.TRAI_QUIET_HOURS.rule_id in rule_ids(v)
    assert defer_hours(v) > 0
    assert (late.hour + defer_hours(v)) % 24 == policy.quiet_hours_end_hour


def test_daytime_nudge_is_allowed(world, canonical, now, policy):
    conn, _, _ = world
    ctx = dbmod.build_context(conn, canonical["CART_ABANDONED_HOT"].case_id, now.replace(hour=11))
    assert evaluate(ctx, MESSAGE, policy).decision is PolicyDecision.PASS


def test_missing_pre_debit_notice_defers_the_debit(world, canonical, now, policy):
    """RBI e-mandate: 24h advance notification is mandatory, so this waits."""
    conn, _, _ = world
    v = evaluate(ctx_for(conn, canonical, "RBI_NOTICE_MISSING", now), RETRY, policy)
    assert v.decision is PolicyDecision.DEFER
    assert R.RBI_PRE_DEBIT_NOTICE.rule_id in rule_ids(v)
    assert defer_hours(v) >= 1


def test_afa_ceiling_blocks_a_large_unauthenticated_autodebit(world, canonical, now, policy):
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "AFA_CEILING_BREACH", now)
    assert ctx.case.amount_paise > R.AFA_CEILING_PAISE
    v = evaluate(ctx, RETRY, policy)
    assert v.decision is PolicyDecision.BLOCK


def test_afa_exempt_categories_get_the_higher_ceiling(world, canonical, now, policy):
    """Insurance, mutual funds and credit-card bills sit under a INR 1,00,000 ceiling."""
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "AFA_CEILING_BREACH", now)
    exempt = ctx.model_copy(update={
        "case": ctx.case.model_copy(update={
            "afa_exempt_category": True,
            "amount_paise": 5_000_000,
            "failure_reason": "insufficient_funds",
        })
    })
    assert evaluate(exempt, RETRY, policy).decision is not PolicyDecision.BLOCK


def test_opted_out_customer_is_not_contacted_by_machine_or_human(world, canonical, now, policy):
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "OPTED_OUT", now)
    for action in (MESSAGE, LINK, ESCALATE):
        assert evaluate(ctx, action, policy).decision is PolicyDecision.BLOCK
    assert evaluate(ctx, NOTHING, policy).decision is PolicyDecision.PASS


def test_live_promise_protects_the_customer_from_everything_but_silence(world, canonical, now, policy):
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "PROMISE_ACTIVE", now)
    for action in (RETRY, MESSAGE, LINK, ESCALATE):
        v = evaluate(ctx, action, policy)
        assert v.decision is PolicyDecision.BLOCK
        assert R.PROMISE_ACTIVE.rule_id in rule_ids(v)


def test_promise_stops_protecting_once_it_comes_due(world, canonical, now, policy):
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "PROMISE_ACTIVE", now)
    later = dbmod.build_context(
        conn, ctx.case.case_id, ctx.promise.promised_for + timedelta(hours=1)
    )
    assert R.PROMISE_ACTIVE.rule_id not in rule_ids(evaluate(later, MESSAGE, policy))


def test_high_value_requires_a_human_to_approve(world, canonical, now, policy):
    conn, _, _ = world
    ctx = ctx_for(conn, canonical, "HIGH_VALUE_APPROVAL", now)
    assert ctx.case.amount_paise >= policy.human_approval_threshold_paise
    assert evaluate(ctx, LINK, policy).decision is PolicyDecision.REQUIRE_APPROVAL
    # Escalating *is* asking a human, so it is not itself gated on approval.
    assert evaluate(ctx, ESCALATE, policy).decision is PolicyDecision.PASS


def test_an_action_off_the_allow_list_is_refused(world, canonical, now, policy):
    """The rogue-action path: whatever proposes it, the policy engine refuses."""
    conn, _, _ = world
    restricted = policy.model_copy(update={
        "allowed_actions": [ActionType.NO_ACTION, ActionType.HUMAN_ESCALATION]
    })
    ctx = ctx_for(conn, canonical, "NSF_TRANSIENT", now)
    v = evaluate(ctx, RETRY, restricted)
    assert v.decision is PolicyDecision.BLOCK
    assert R.MERCHANT_ALLOWED_ACTIONS.rule_id in rule_ids(v)


def test_doing_nothing_is_always_permitted(world, canonical, now, policy):
    conn, _, _ = world
    for name in canonical:
        ctx = ctx_for(conn, canonical, name, now)
        assert evaluate(ctx, NOTHING, policy).decision is PolicyDecision.PASS


def test_every_verdict_names_a_rule_and_cites_a_source(world, canonical, now, policy):
    conn, _, _ = world
    for name in canonical:
        ctx = ctx_for(conn, canonical, name, now)
        for action in (RETRY, MESSAGE, LINK, ESCALATE, NOTHING):
            for v in evaluate(ctx, action, policy).verdicts:
                assert v.rule_id in R.ALL_RULES, f"unregistered rule {v.rule_id}"
                assert v.citation, f"{v.rule_id} has no citation"
                assert v.message, f"{v.rule_id} has no explanation"


@pytest.mark.parametrize("rule_id", sorted(R.ALL_RULES))
def test_rule_catalogue_is_complete(rule_id):
    rule = R.ALL_RULES[rule_id]
    assert rule.citation and rule.description
