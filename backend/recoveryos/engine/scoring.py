"""Candidate generation and the utility calculation.

    utility = expected_incremental - cost - fatigue_penalty - risk_penalty

`NO_ACTION` scores exactly zero, by construction. Every other action has to
earn its place against leaving the customer alone. That one identity is the
whole product: the system is not looking for the best thing to do, it is
looking for whether doing anything beats doing nothing.

All arithmetic is integer paise. No LLM touches any number in this module.
"""

from __future__ import annotations

from ..policy.guardrails import CONTACT_ACTIONS, MerchantPolicy, load_policy
from ..schemas import (
    ActionType,
    CandidateAction,
    CaseContext,
    CaseType,
    LinkVariant,
    REASON_DIAGNOSIS,
    Retryability,
    ScoredAction,
)
from .estimators import estimate_lift, estimate_self_cure

# Retry ladders, in hours from now. Short for rail failures, long for money.
RETRY_DELAYS_TECHNICAL = (2, 6, 24)
RETRY_DELAYS_LIQUIDITY = (24, 72, 168)

# Repeated hard declines are not free: issuers throttle merchants who keep
# hammering a failing mandate.
REPEAT_ATTEMPT_RISK_PAISE = 2000
# Autonomy risk scales with exposure -- 0.5% of the amount, capped at INR 50.
AUTONOMY_RISK_RATE, AUTONOMY_RISK_CAP_PAISE = 200, 5000


def diagnose(ctx: CaseContext) -> tuple[str, Retryability]:
    """Deterministic root cause. The LLM may restate this; it may not change it."""
    return REASON_DIAGNOSIS[ctx.case.failure_reason], ctx.retryability


def candidates(ctx: CaseContext) -> list[CandidateAction]:
    """The action space for this case. Small on purpose."""
    out: list[CandidateAction] = [
        CandidateAction(action=ActionType.NO_ACTION, rationale="leave the customer alone and let the baseline play out")
    ]

    if ctx.retryability is not Retryability.NO_FAILURE:
        delays = (
            RETRY_DELAYS_TECHNICAL
            if ctx.retryability is Retryability.TRANSIENT_TECHNICAL
            else RETRY_DELAYS_LIQUIDITY
        )
        for h in delays:
            out.append(
                CandidateAction(
                    action=ActionType.DELAYED_RETRY,
                    delay_hours=h,
                    rationale=f"re-present the debit {h}h from now",
                )
            )
        out.append(
            CandidateAction(
                action=ActionType.PAYMENT_LINK,
                variant=LinkVariant.UPDATE_INSTRUMENT,
                channel=ctx.customer.preferred_channel,
                rationale="ask the customer to replace the failing instrument or mandate",
            )
        )

    out.append(
        CandidateAction(
            action=ActionType.PAYMENT_LINK,
            variant=LinkVariant.COLLECT_NOW,
            channel=ctx.customer.preferred_channel,
            rationale="one-tap link to settle this amount now",
        )
    )
    out.append(
        CandidateAction(
            action=ActionType.CUSTOMER_MESSAGE,
            channel=ctx.customer.preferred_channel,
            rationale="explain what happened and what to do about it",
        )
    )
    out.append(
        CandidateAction(
            action=ActionType.HUMAN_ESCALATION,
            rationale="hand to a collections owner",
        )
    )
    return out


def fatigue_price(ctx: CaseContext, action: CandidateAction, policy: MerchantPolicy) -> int:
    """Shadow price of one unit of this customer's attention.

    Convex in recent contacts, and counted across *all* of that customer's open
    cases -- they have one inbox, not one per invoice. The second message in a
    week costs four times the first; the third, nine times.
    """
    if action.action not in CONTACT_ACTIONS:
        return 0
    return policy.goodwill_cost_per_contact_paise * (ctx.contacts_7d + 1) ** 2


def risk_price(ctx: CaseContext, action: CandidateAction, policy: MerchantPolicy) -> int:
    """Cost of the action going wrong, as distinct from simply not working."""
    if action.action is ActionType.NO_ACTION:
        return 0
    risk = 0
    if action.action is ActionType.DELAYED_RETRY and ctx.case.attempts_made:
        risk += REPEAT_ATTEMPT_RISK_PAISE * ctx.case.attempts_made
    if action.action is not ActionType.HUMAN_ESCALATION:
        risk += min(AUTONOMY_RISK_CAP_PAISE, ctx.case.amount_paise // AUTONOMY_RISK_RATE)
    return risk


def score(
    ctx: CaseContext, action: CandidateAction, policy: MerchantPolicy | None = None
) -> ScoredAction:
    p = policy or load_policy()
    p_self, why_self = estimate_self_cure(ctx)
    lift, why_lift = estimate_lift(ctx, action)

    p_treated = p_self + (1 - p_self) * lift
    uplift = round(p_treated - p_self, 4)
    expected = int(uplift * ctx.case.amount_paise)

    cost = p.action_costs_paise[action.action.value]
    fatigue = fatigue_price(ctx, action, p)
    risk = risk_price(ctx, action, p)
    utility = expected - cost - fatigue - risk

    explanation = list(why_lift)
    if action.action is ActionType.NO_ACTION:
        explanation = [
            f"baseline: {p_self:.0%} chance this recovers untouched",
            *why_self,
            "utility of doing nothing is zero by definition -- every other action is measured against it",
        ]
    else:
        explanation.insert(0, f"baseline without us: {p_self:.0%}; with this action: {p_treated:.0%}")
        explanation.append(
            f"incremental {uplift:.1%} of the exposure, minus cost, fatigue and risk"
        )

    return ScoredAction(
        candidate=action,
        p_treated=round(p_treated, 4),
        p_self_cure=p_self,
        uplift=uplift,
        expected_incremental_paise=expected,
        cost_paise=cost,
        fatigue_penalty_paise=fatigue,
        risk_penalty_paise=risk,
        utility_paise=utility,
        explanation=explanation,
    )


def score_all(ctx: CaseContext, policy: MerchantPolicy | None = None) -> list[ScoredAction]:
    """Every candidate, best first. Ties break toward the cheaper action."""
    p = policy or load_policy()
    scored = [score(ctx, c, p) for c in candidates(ctx)]
    scored.sort(key=lambda s: (-s.utility_paise, s.cost_paise, s.candidate.label))
    return scored


def recovery_score(ctx: CaseContext, best: ScoredAction) -> int:
    """A 0-100 heuristic Recovery Opportunity Score, for ranking in the UI only.

    Deliberately *not* a probability and never used in a decision: it exists so
    an operator can sort a queue. It blends how much is at stake with how much
    of that is actually winnable.
    """
    if best.utility_paise <= 0:
        return 0
    exposure = min(1.0, ctx.case.amount_paise / 5_000_000)  # INR 50,000 saturates
    winnable = min(1.0, best.uplift / 0.5)
    return int(round(100 * (0.4 * exposure + 0.6 * winnable)))
