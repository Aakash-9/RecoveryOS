"""The three policies under test.

All three run through identical machinery -- same context, same candidate
actions, same scoring, same guardrail evaluation, same simulator, same random
draws. The *only* thing that varies is which candidate gets chosen and whether
the guardrail verdicts are honoured.

That matters for honesty. If the baselines used different plumbing, any
difference in the results could be plumbing. Here, a difference can only come
from the decision.

    A  NAIVE           Chase every failure with the first thing that might
                       work. No economics, no fatigue, no compliance. This is
                       what "recovery automation" looks like as a for-loop.

    B  RULEBOOK        The standard dunning playbook: failure type maps to a
                       fixed action. Honours hard operational blocks only.
                       Ignores fatigue, promises, regulation and cost.

    B+ RULEBOOK+RULES  The same ladder with every guardrail honoured. This is
                       the arm that makes the comparison fair -- B buys part of
                       its recovery with violations, and pretending otherwise
                       would flatter RecoveryOS.

    C  RECOVERYOS      Self-cure baseline, incremental utility, convex fatigue
                       pricing, full guardrails, stopping rules, and batch
                       allocation across a shared contact budget.
"""

from __future__ import annotations

from typing import Callable

from ..agent.nodes import Choice, choose_recoveryos
from ..policy.guardrails import MerchantPolicy
from ..schemas import (
    ActionType,
    CaseContext,
    LinkVariant,
    PolicyDecision,
    PolicyVerdict,
    Retryability,
    ScoredAction,
    StopReason,
)

Gated = list[tuple[ScoredAction, PolicyVerdict]]
Chooser = Callable[[Gated, CaseContext, MerchantPolicy], Choice]

# Guardrails a rulebook-era system would still have, because they are
# operational rather than regulatory: it knows not to retry forever and not to
# retry a dead card. It has never heard of TRAI, contact fatigue, or a
# promise-to-pay state machine.
RULEBOOK_HONOURS = {
    "MERCHANT-RETRY-CAP",
    "MERCHANT-ALLOWED-ACTIONS",
    "POLICY-INSTRUMENT-INVALID",
    "POLICY-COMPLIANCE-BLOCKED",
}


def _find(gated: Gated, action: ActionType, variant: LinkVariant | None = None):
    for s, v in gated:
        if s.candidate.action is action and (variant is None or s.candidate.variant is variant):
            return s, v
    return None


def _no_action(gated: Gated) -> tuple[ScoredAction, PolicyVerdict]:
    return _find(gated, ActionType.NO_ACTION)


def _blocked_by(verdict: PolicyVerdict, honoured: set[str] | None) -> bool:
    if honoured is None:
        return verdict.decision is not PolicyDecision.PASS
    return any(
        v.decision is PolicyDecision.BLOCK and v.rule_id in honoured for v in verdict.verdicts
    )


def choose_naive(gated: Gated, ctx: CaseContext, policy: MerchantPolicy) -> Choice:
    """Something must be done; this is something; therefore this must be done.

    Retry if the case ever failed, otherwise message. No guardrail is consulted
    and no cost is considered -- blocked actions are executed anyway, which is
    what makes the violation count in the evaluation a real measurement.
    """
    if ctx.retryability is not Retryability.NO_FAILURE:
        pick = _find(gated, ActionType.DELAYED_RETRY)
        if pick:
            # Soonest possible retry: no thought given to timing.
            pick = min(
                (g for g in gated if g[0].candidate.action is ActionType.DELAYED_RETRY),
                key=lambda g: g[0].candidate.delay_hours,
            )
            return Choice(pick[0].candidate, pick[0], pick[1])
    pick = _find(gated, ActionType.CUSTOMER_MESSAGE) or _no_action(gated)
    return Choice(pick[0].candidate, pick[0], pick[1])


def _rulebook(gated: Gated, ctx: CaseContext, honours: set[str] | None) -> Choice:
    """Failure type in, fixed action out. The industry-standard dunning ladder.

    `honours` selects which guardrails this variant respects. `None` means all
    of them, which is the fair comparison against RecoveryOS: same compliance
    posture, different decision quality.
    """
    order: list[tuple[ActionType, LinkVariant | None]] = []
    r = ctx.retryability
    if r in (Retryability.TRANSIENT_LIQUIDITY, Retryability.TRANSIENT_TECHNICAL):
        order = [
            (ActionType.DELAYED_RETRY, None),
            (ActionType.CUSTOMER_MESSAGE, None),
            (ActionType.PAYMENT_LINK, LinkVariant.COLLECT_NOW),
        ]
    elif r in (Retryability.INSTRUMENT_INVALID, Retryability.MANDATE_INVALID, Retryability.COMPLIANCE_BLOCKED):
        order = [
            (ActionType.PAYMENT_LINK, LinkVariant.UPDATE_INSTRUMENT),
            (ActionType.CUSTOMER_MESSAGE, None),
        ]
    else:
        order = [
            (ActionType.CUSTOMER_MESSAGE, None),
            (ActionType.PAYMENT_LINK, LinkVariant.COLLECT_NOW),
        ]

    for action, variant in order:
        pick = _find(gated, action, variant)
        if pick and not _blocked_by(pick[1], honours):
            if action is ActionType.DELAYED_RETRY:
                # Fixed 24h ladder. No salary-cycle awareness, no rail awareness.
                pick = min(
                    (g for g in gated
                     if g[0].candidate.action is ActionType.DELAYED_RETRY
                     and not _blocked_by(g[1], honours)),
                    key=lambda g: abs(g[0].candidate.delay_hours - 24),
                    default=pick,
                )
            return Choice(pick[0].candidate, pick[0], pick[1])

    pick = _no_action(gated)
    return Choice(pick[0].candidate, pick[0], pick[1], StopReason.NO_VALID_ACTIONS)


def choose_rulebook(gated: Gated, ctx: CaseContext, policy: MerchantPolicy) -> Choice:
    return _rulebook(gated, ctx, RULEBOOK_HONOURS)


def choose_rulebook_compliant(gated: Gated, ctx: CaseContext, policy: MerchantPolicy) -> Choice:
    return _rulebook(gated, ctx, None)


POLICIES: dict[str, Chooser] = {
    "NAIVE": choose_naive,
    "RULEBOOK": choose_rulebook,
    "RULEBOOK+RULES": choose_rulebook_compliant,
    "RECOVERYOS": choose_recoveryos,
}

# Batch allocation is part of what RecoveryOS *is*; the baselines have no
# concept of a shared budget.
ALLOCATES = {"NAIVE": False, "RULEBOOK": False, "RULEBOOK+RULES": False, "RECOVERYOS": True}

DESCRIPTIONS = {
    "NAIVE": "Chase every failure immediately. No economics, no fatigue, no compliance checks.",
    "RULEBOOK": "Standard dunning ladder: failure type maps to a fixed action. Honours operational caps only.",
    "RULEBOOK+RULES": "The same dunning ladder, fully compliant. The fair like-for-like comparison: identical guardrails, no economics.",
    "RECOVERYOS": "Self-cure baseline, incremental utility, fatigue pricing, full guardrails, stopping rules, batch allocation.",
}
