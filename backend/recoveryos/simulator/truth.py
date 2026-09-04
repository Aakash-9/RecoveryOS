"""The hidden response model -- the simulated world's answer key.

**Nothing in `recoveryos/engine/` or `recoveryos/policy/` may import this
module.** `tests/test_audit_and_isolation.py` enforces that by static import scan. The
decision engine estimates these quantities from observable history; the
simulator knows them. Keeping the two apart is the only reason the evaluation
numbers mean anything.

Calibration
-----------
Parameters are drawn from published failed-payment recovery benchmarks, not
tuned to make RecoveryOS win:

* Involuntary churn is roughly 20-40% of total subscription churn, and the
  average subscription business loses about 9% of MRR to failed payments.
* Median dunning recovers around half of failed charges; best-in-class stacks
  reach 70-85%, and only by combining retries, messaging and instrument
  updates -- no single lever gets there.
* Retry practice converged on ~24h / ~72h / ~day-7 windows because
  insufficient-funds is a liquidity *timing* problem. In India that is
  sharpened by salary credits clustering in the first week of the month.

The parameter block was frozen before any policy code was written. See
the Limitations section of the README.

Potential-outcomes assumption
-----------------------------
A treated case recovers whenever the untreated case would have (monotonicity /
no defiers), so treatment normally only adds recoveries. The single deliberate
exception is opt-out: a customer contacted past their tolerance walks away even
from a payment they were going to make, so over-contacting is genuinely
destructive rather than merely wasteful. See `provider.py`.
"""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import ActionType, CandidateAction, LinkVariant, Retryability

# Retry-window anchors, in hours since failure.
RETRY_ANCHORS = (6, 24, 72, 168)

# Salary credits cluster in the first week of the month in India, so a
# liquidity-driven retry landing there is materially more likely to clear.
SALARY_WINDOW_DAYS = range(1, 8)
SALARY_WINDOW_MULTIPLIER = 1.35


class GroundTruth(BaseModel):
    """Per-case latent behaviour. Simulator-only."""

    model_config = ConfigDict(frozen=True)

    case_id: str

    # P(recovers with no intervention at all, within the horizon). The single
    # most important number in the whole evaluation: it is what a naive
    # recovery tool silently bills itself for.
    self_cure_prob: float

    # P(debit clears | retried at this many hours after failure).
    retry_curve: dict[str, float] = Field(default_factory=dict)

    link_collect_prob: float = 0.0
    link_update_prob: float = 0.0
    message_prob: float = 0.0
    human_prob: float = 0.0

    # Convex decay of response with recent contacts. Higher = tunes out faster.
    fatigue_sensitivity: float = 0.35
    # P(opts out entirely | contacted while already fatigued).
    opt_out_prob_per_excess_contact: float = 0.06

    salary_cycle_sensitive: bool = False

    # Ceiling on any single action. No intervention is magic.
    ceiling: float = 0.9


def _interp_retry(curve: dict[str, float], hours: float) -> float:
    """Piecewise-linear read of the retry curve at an arbitrary delay."""
    if not curve:
        return 0.0
    pts = sorted((int(k), v) for k, v in curve.items())
    if hours <= pts[0][0]:
        return pts[0][1]
    if hours >= pts[-1][0]:
        return pts[-1][1]
    for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
        if h0 <= hours <= h1:
            span = h1 - h0
            return v0 + (v1 - v0) * ((hours - h0) / span if span else 0.0)
    return pts[-1][1]


def fatigue_decay(truth: GroundTruth, contacts_7d: int) -> float:
    """Diminishing returns on the customer's attention."""
    return math.exp(-truth.fatigue_sensitivity * contacts_7d)


def response_probability(
    truth: GroundTruth,
    action: CandidateAction,
    retryability: Retryability,
    hours_since_failure: float,
    contacts_7d: int,
    executes_at: datetime,
) -> float:
    """P(this case is recovered | this action is executed now).

    This is the *world*, not the agent's estimate of it.
    """
    if action.action is ActionType.NO_ACTION:
        return truth.self_cure_prob

    if action.action is ActionType.HUMAN_ESCALATION:
        return max(truth.human_prob, truth.self_cure_prob)

    if action.action is ActionType.DELAYED_RETRY:
        # A retry against a dead instrument or an unauthorised mandate cannot
        # clear, whatever the curve says. Guardrails normally prevent this
        # action being proposed at all; the simulator refuses to reward it
        # even if some policy sneaks it through.
        if retryability in (
            Retryability.INSTRUMENT_INVALID,
            Retryability.MANDATE_INVALID,
            Retryability.COMPLIANCE_BLOCKED,
            Retryability.NO_FAILURE,
        ):
            return truth.self_cure_prob
        p = _interp_retry(truth.retry_curve, hours_since_failure + action.delay_hours)
        if truth.salary_cycle_sensitive:
            landing_day = (executes_at.day - 1 + (action.delay_hours // 24)) % 31 + 1
            if landing_day in SALARY_WINDOW_DAYS:
                p *= SALARY_WINDOW_MULTIPLIER
        return min(max(p, truth.self_cure_prob), truth.ceiling)

    decay = fatigue_decay(truth, contacts_7d)
    if action.action is ActionType.PAYMENT_LINK:
        base = (
            truth.link_update_prob
            if action.variant is LinkVariant.UPDATE_INSTRUMENT
            else truth.link_collect_prob
        )
    else:  # CUSTOMER_MESSAGE
        base = truth.message_prob
    return min(max(base * decay, truth.self_cure_prob), truth.ceiling)
