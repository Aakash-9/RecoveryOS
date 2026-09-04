"""Heuristic estimators over *observable* case data.

These are transparent weighted heuristics. They are not learned, not fitted,
and not statistically validated -- every number below is a documented prior,
and the UI labels them "heuristic" wherever they are shown. What matters is
not that they are precise, but that they are *explainable*, *bounded* and
computed from data a real merchant already has.

The two estimates
-----------------
`estimate_self_cure` -- P(this case recovers with no intervention at all).
    Collections practice calls this the self-cure rate, and it is the number
    that makes gross "revenue recovered" a fiction: a dunning system that
    contacts everybody bills itself for every self-cure it happened to
    overlap with.

`estimate_lift` -- the *incremental* probability an action adds on top of
    self-cure. Modelled multiplicatively so that:

        p_treated = p_self_cure + (1 - p_self_cure) * lift

    which has the property we want for free: as a customer's self-cure
    probability rises, the value of intervening on them falls towards zero.

Nothing here may import `simulator.truth`. The estimator is the agent's
*belief*; the simulator holds the world. `tests/test_audit_and_isolation.py` enforces it.

Priors are grounded in published failed-payment recovery benchmarks -- median
dunning recovers roughly half of failed charges, best-in-class 70-85% and only
by stacking retries, messaging and instrument updates -- plus the standard
24h / 72h / day-7 retry ladder and the Indian salary cycle.
"""

from __future__ import annotations

import math
from datetime import timedelta

from ..schemas import (
    ActionType,
    CandidateAction,
    CaseContext,
    CaseType,
    FailureReason,
    LinkVariant,
    PromiseState,
    Retryability,
    Segment,
)

# --------------------------------------------------------------------------- #
# Self-cure
# --------------------------------------------------------------------------- #

# Prior probability that a failure of this kind resolves itself.
BASE_SELF_CURE: dict[FailureReason, float] = {
    FailureReason.INSUFFICIENT_FUNDS: 0.20,
    FailureReason.PAYMENT_DECLINED: 0.15,
    FailureReason.BANK_TECHNICAL_ERROR: 0.22,
    FailureReason.GATEWAY_TECHNICAL_ERROR: 0.22,
    FailureReason.PAYMENT_TIMED_OUT: 0.22,
    FailureReason.COLLECT_REQUEST_EXPIRED: 0.25,
    FailureReason.CARD_EXPIRED: 0.08,
    FailureReason.INVALID_VPA: 0.06,
    FailureReason.VPA_RESOLUTION_FAILED: 0.06,
    FailureReason.MANDATE_REVOKED: 0.05,
    FailureReason.MANDATE_PAUSED: 0.07,
    FailureReason.AFA_REQUIRED: 0.08,
    FailureReason.PRE_DEBIT_NOTICE_MISSING: 0.10,
    FailureReason.NOT_APPLICABLE: 0.12,
}

# Pseudo-count for shrinking a customer's observed self-cure rate towards the
# prior. Two failures of evidence is not enough to abandon the prior; eight is.
SELF_CURE_PSEUDOCOUNT = 2.0

SELF_CURE_FLOOR, SELF_CURE_CEILING = 0.02, 0.88


def estimate_self_cure(ctx: CaseContext) -> tuple[float, list[str]]:
    """P(recovers untouched). Returns the estimate and how it was reached."""
    why: list[str] = []
    base = BASE_SELF_CURE[ctx.case.failure_reason]
    cust = ctx.customer

    n_failed = cust.prior_payments_failed
    if n_failed > 0:
        p = (cust.prior_self_cures + base * SELF_CURE_PSEUDOCOUNT) / (
            n_failed + SELF_CURE_PSEUDOCOUNT
        )
        why.append(
            f"{cust.prior_self_cures} of {n_failed} past failures for this customer "
            f"resolved with no intervention (prior {base:.0%}, shrunk to {p:.0%})"
        )
    else:
        p = base
        why.append(f"no failure history; using the {base:.0%} prior for {ctx.case.failure_reason.value}")

    if ctx.promise.state is PromiseState.ACTIVE:
        p = p + (1 - p) * 0.45
        why.append("live promise to pay raises the unaided-recovery estimate")
    elif ctx.promise.state is PromiseState.BROKEN:
        p *= 0.45
        why.append("a previous promise was broken; unaided recovery is less likely")

    if ctx.case.attempts_made:
        p *= 0.8 ** ctx.case.attempts_made
        why.append(f"{ctx.case.attempts_made} attempt(s) already failed without help")

    if ctx.days_overdue > 21:
        p *= 0.7
        why.append(f"{ctx.days_overdue:.0f} days overdue; ageing receivables self-cure less often")

    p = min(SELF_CURE_CEILING, max(SELF_CURE_FLOOR, p))
    return round(p, 4), why


# --------------------------------------------------------------------------- #
# Action lift
# --------------------------------------------------------------------------- #

# Incremental clearance probability by retry delay, in hours since failure.
RETRY_LIFT_LIQUIDITY = {6: 0.16, 24: 0.42, 72: 0.34, 168: 0.24}
RETRY_LIFT_TECHNICAL = {2: 0.48, 6: 0.58, 24: 0.52, 72: 0.38, 168: 0.26}

LINK_UPDATE_LIFT = {
    Retryability.INSTRUMENT_INVALID: 0.45,
    Retryability.MANDATE_INVALID: 0.40,
    Retryability.COMPLIANCE_BLOCKED: 0.42,
}
LINK_UPDATE_LIFT_DEFAULT = 0.16

LINK_COLLECT_LIFT = {
    CaseType.INVOICE_OVERDUE: 0.30,
    CaseType.CHECKOUT_ABANDONMENT: 0.30,
}
LINK_COLLECT_LIFT_DEFAULT = 0.24

MESSAGE_LIFT = {
    CaseType.CHECKOUT_ABANDONMENT: 0.34,
    CaseType.INVOICE_OVERDUE: 0.20,
    CaseType.SUBSCRIPTION_RENEWAL: 0.15,
    CaseType.ONE_TIME_PAYMENT: 0.18,
}

ESCALATION_LIFT = {Segment.ENTERPRISE: 0.34, Segment.SME: 0.28, Segment.RETAIL: 0.20}

# Salary credits cluster in the first week of the month.
SALARY_WINDOW = range(1, 8)
# How the estimator reads a customer whose debits are known to clear post-payday.
PAYDAY_IN_WINDOW, PAYDAY_OUT_OF_WINDOW = 1.90, 0.45
# ...and everyone else.
GENERIC_IN_WINDOW = 1.15

# The agent's own belief about diminishing returns on attention. Independent of
# whatever the simulated customer actually does.
FATIGUE_DECAY_PER_CONTACT = 0.30
# Checkout intent decays fast.
CART_INTENT_HALFLIFE_HOURS = 18.0
# Receivables get harder with age; floor the penalty so old debt is not written
# off by arithmetic alone.
AGEING_MAX_PENALTY, AGEING_DAYS = 0.45, 45.0
# A customer who has responded to this exact action before is more likely to again.
PERSONALISATION_BONUS = 1.25


def _interp(curve: dict[int, float], hours: float) -> float:
    pts = sorted(curve.items())
    if hours <= pts[0][0]:
        return pts[0][1]
    if hours >= pts[-1][0]:
        return pts[-1][1]
    for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
        if h0 <= hours <= h1:
            return v0 + (v1 - v0) * (hours - h0) / (h1 - h0)
    return pts[-1][1]


def _retry_lift(ctx: CaseContext, action: CandidateAction, why: list[str]) -> float:
    r = ctx.retryability
    # A dead instrument or a withdrawn mandate cannot be retried into working.
    # The guardrail layer refuses these outright; the estimator agrees, so the
    # action never even looks attractive.
    if r in (Retryability.INSTRUMENT_INVALID, Retryability.MANDATE_INVALID, Retryability.NO_FAILURE):
        why.append(f"retry cannot clear a {r.value.lower().replace('_', ' ')} failure")
        return 0.0

    at = ctx.hours_since_failure + action.delay_hours
    if r is Retryability.TRANSIENT_TECHNICAL:
        lift = _interp(RETRY_LIFT_TECHNICAL, at)
        why.append(f"rail-level failure; clearance peaks on a short retry ({lift:.0%} at +{action.delay_hours}h)")
    else:
        # Liquidity, and compliance blocks whose underlying debit is sound.
        lift = _interp(RETRY_LIFT_LIQUIDITY, at)
        why.append(f"liquidity failure; retry ladder gives {lift:.0%} at +{action.delay_hours}h")

        landing_day = (ctx.now + timedelta(hours=action.delay_hours)).day
        in_window = landing_day in SALARY_WINDOW
        if ctx.customer.pays_after_payday:
            mult = PAYDAY_IN_WINDOW if in_window else PAYDAY_OUT_OF_WINDOW
            lift *= mult
            why.append(
                f"this customer's debits historically clear after payday; "
                f"retry lands on day {landing_day} ({'inside' if in_window else 'outside'} "
                f"the salary window) x{mult}"
            )
        elif in_window:
            lift *= GENERIC_IN_WINDOW
            why.append(f"retry lands on day {landing_day}, inside the salary window x{GENERIC_IN_WINDOW}")

    reliability = ctx.customer.prior_payments_ok / max(
        1, ctx.customer.prior_payments_ok + ctx.customer.prior_payments_failed
    )
    lift *= 0.8 + 0.4 * reliability
    why.append(f"payment reliability {reliability:.0%}")
    return lift


def _contact_lift(ctx: CaseContext, action: CandidateAction, why: list[str]) -> float:
    if action.action is ActionType.PAYMENT_LINK:
        if action.variant is LinkVariant.UPDATE_INSTRUMENT:
            lift = LINK_UPDATE_LIFT.get(ctx.retryability, LINK_UPDATE_LIFT_DEFAULT)
            why.append(f"instrument replacement addresses the actual cause ({lift:.0%} base)")
        else:
            lift = LINK_COLLECT_LIFT.get(ctx.case.case_type, LINK_COLLECT_LIFT_DEFAULT)
            why.append(f"one-tap collection link ({lift:.0%} base)")
    else:
        lift = MESSAGE_LIFT[ctx.case.case_type]
        why.append(f"reminder on {ctx.case.case_type.value.lower().replace('_', ' ')} ({lift:.0%} base)")

    if ctx.contacts_7d:
        decay = math.exp(-FATIGUE_DECAY_PER_CONTACT * ctx.contacts_7d)
        lift *= decay
        why.append(f"{ctx.contacts_7d} contact(s) in the last 7 days; response decays x{decay:.2f}")

    if ctx.case.case_type is CaseType.CHECKOUT_ABANDONMENT:
        decay = math.exp(-ctx.hours_since_failure / CART_INTENT_HALFLIFE_HOURS)
        lift *= decay
        why.append(f"checkout intent {ctx.hours_since_failure:.0f}h old; decays x{decay:.2f}")

    if ctx.promise.state is PromiseState.BROKEN:
        lift *= 0.6
        why.append("a prior promise was broken; further messaging is discounted")
    return lift


def estimate_lift(ctx: CaseContext, action: CandidateAction) -> tuple[float, list[str]]:
    """Incremental P(recovery) this action adds on top of doing nothing."""
    why: list[str] = []

    if action.action is ActionType.NO_ACTION:
        return 0.0, ["no intervention; the baseline is the outcome"]

    if action.action is ActionType.DELAYED_RETRY:
        lift = _retry_lift(ctx, action, why)
    elif action.action is ActionType.HUMAN_ESCALATION:
        lift = ESCALATION_LIFT[ctx.customer.segment]
        why.append(f"human collections contact for a {ctx.customer.segment.value.lower()} account ({lift:.0%})")
        if ctx.promise.state is PromiseState.BROKEN:
            lift *= 1.2
            why.append("broken promise; a person is more effective than another automated nudge")
    else:
        lift = _contact_lift(ctx, action, why)

    if ctx.days_overdue:
        penalty = 1 - min(AGEING_MAX_PENALTY, ctx.days_overdue / AGEING_DAYS)
        lift *= penalty
        why.append(f"{ctx.days_overdue:.0f} days overdue; ageing discount x{penalty:.2f}")

    if ctx.customer.prior_recoveries_by_action.get(action.action.value):
        lift *= PERSONALISATION_BONUS
        n = ctx.customer.prior_recoveries_by_action[action.action.value]
        why.append(f"this customer has responded to {action.action.value} {n}x before x{PERSONALISATION_BONUS}")

    return round(max(0.0, min(0.95, lift)), 4), why
