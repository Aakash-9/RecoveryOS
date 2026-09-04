"""The guardrail engine.

This layer is deterministic, has no dependency on the decision engine, and is
the only thing that can authorise a money action. The agent proposes; this
disposes. It never calls an LLM and it never consults a probability.

Every verdict names the rule that produced it and cites where that rule comes
from -- see `rules.py`.

Timebase: all datetimes in RecoveryOS are naive local time in Asia/Kolkata.
There is no real integration and no cross-timezone traffic, so carrying tzinfo
would be ceremony. The quiet-hours rule reads the hour directly.
"""

from __future__ import annotations

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import (
    ActionType,
    FailureReason,
    CandidateAction,
    CaseContext,
    PolicyDecision,
    PolicyVerdict,
    PromiseState,
    Retryability,
    RuleVerdict,
    rupees,
)
from . import rules as R

POLICY_PATH = Path(__file__).with_name("merchant_policy.json")

# Actions that consume a unit of the customer's attention. HUMAN_ESCALATION
# does not -- it costs the merchant a person, not the customer a message.
CONTACT_ACTIONS = frozenset({ActionType.CUSTOMER_MESSAGE, ActionType.PAYMENT_LINK})


class MerchantPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str
    merchant_name: str
    description: str = ""

    max_retry_attempts: int
    min_retry_gap_hours: int
    max_contacts_per_24h: int
    max_contacts_per_7d: int
    human_approval_threshold_paise: int
    max_discount_percent: int
    allowed_actions: list[ActionType]

    quiet_hours_end_hour: int
    quiet_hours_start_hour: int

    min_utility_paise: int
    min_utility_note: str = ""
    intervention_budget_paise: int
    human_review_capacity: int = 6
    max_iterations_per_case: int
    holdout_fraction: float
    holdout_note: str = ""

    action_costs_paise: dict[str, int]
    cost_basis_note: str = ""
    goodwill_cost_per_contact_paise: int
    goodwill_cost_note: str = ""

    extra: dict = Field(default_factory=dict)


@lru_cache(maxsize=4)
def load_policy(path: str | None = None) -> MerchantPolicy:
    raw = json.loads(Path(path or POLICY_PATH).read_text(encoding="utf-8"))
    return MerchantPolicy(**raw)


# --------------------------------------------------------------------------- #
# Individual checks. Each returns a verdict or None (meaning "not my business").
# --------------------------------------------------------------------------- #


def _allowed(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action in p.allowed_actions:
        return None
    return RuleVerdict(
        rule_id=R.MERCHANT_ALLOWED_ACTIONS.rule_id,
        citation=R.MERCHANT_ALLOWED_ACTIONS.citation,
        decision=PolicyDecision.BLOCK,
        message=f"{a.action.value} is not on the merchant allow-list.",
    )


def _retry_cap(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action is not ActionType.DELAYED_RETRY:
        return None
    if ctx.case.attempts_made < p.max_retry_attempts:
        return None
    return RuleVerdict(
        rule_id=R.MERCHANT_RETRY_CAP.rule_id,
        citation=R.MERCHANT_RETRY_CAP.citation,
        decision=PolicyDecision.BLOCK,
        message=(
            f"{ctx.case.attempts_made} automated attempts already made; "
            f"merchant cap is {p.max_retry_attempts}."
        ),
    )


def _retry_gap(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action is not ActionType.DELAYED_RETRY or ctx.case.last_attempt_at is None:
        return None
    elapsed = (ctx.now - ctx.case.last_attempt_at).total_seconds() / 3600.0
    # The action itself may carry a delay; count it toward the required gap.
    if elapsed + a.delay_hours >= p.min_retry_gap_hours:
        return None
    return RuleVerdict(
        rule_id=R.MERCHANT_RETRY_GAP.rule_id,
        citation=R.MERCHANT_RETRY_GAP.citation,
        decision=PolicyDecision.DEFER,
        message=(
            f"Only {elapsed:.1f}h since the last attempt; "
            f"{p.min_retry_gap_hours}h minimum gap required."
        ),
        defer_hours=int(round(p.min_retry_gap_hours - elapsed - a.delay_hours)) or 1,
    )


def _instrument_invalid(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action is not ActionType.DELAYED_RETRY:
        return None
    if ctx.retryability not in (Retryability.INSTRUMENT_INVALID, Retryability.MANDATE_INVALID):
        return None
    return RuleVerdict(
        rule_id=R.INSTRUMENT_UNRECOVERABLE.rule_id,
        citation=R.INSTRUMENT_UNRECOVERABLE.citation,
        decision=PolicyDecision.BLOCK,
        message=(
            f"Failure class {ctx.retryability.value} "
            f"({ctx.case.failure_reason.value}) cannot succeed on retry."
        ),
    )


def _compliance_blocked(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action is not ActionType.DELAYED_RETRY:
        return None
    # Only a genuinely unauthorised debit is hard-blocked here. A missing
    # pre-debit notification is a paperwork gap, not a dead mandate -- that is
    # a DEFER, and _rbi_pre_debit_notice owns it.
    if ctx.case.failure_reason is not FailureReason.AFA_REQUIRED:
        return None
    return RuleVerdict(
        rule_id=R.COMPLIANCE_BLOCKED_RETRY.rule_id,
        citation=R.COMPLIANCE_BLOCKED_RETRY.citation,
        decision=PolicyDecision.BLOCK,
        message=(
            "Debit requires additional factor authentication that is not on "
            "record; re-presenting it is not authorised."
        ),
    )


def _rbi_pre_debit_notice(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    """RBI e-mandate: no recurring debit without a 24h advance notification."""
    if a.action is not ActionType.DELAYED_RETRY or not ctx.case.is_recurring:
        return None
    notice = ctx.case.pre_debit_notice_sent_at
    attempt_at = ctx.now + timedelta(hours=a.delay_hours)
    if notice is not None and (attempt_at - notice) >= timedelta(hours=24):
        return None
    if notice is None:
        shortfall = 24
        detail = "No pre-debit notification on record for this mandate."
    else:
        gap = (attempt_at - notice).total_seconds() / 3600.0
        shortfall = max(1, int(round(24 - gap)))
        detail = f"Notification issued only {gap:.1f}h before the intended debit."
    return RuleVerdict(
        rule_id=R.RBI_PRE_DEBIT_NOTICE.rule_id,
        citation=R.RBI_PRE_DEBIT_NOTICE.citation,
        decision=PolicyDecision.DEFER,
        message=f"{detail} Debit deferred until 24h after notification.",
        defer_hours=shortfall,
    )


def _rbi_afa_ceiling(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action is not ActionType.DELAYED_RETRY or not ctx.case.is_recurring:
        return None
    if ctx.case.afa_present:
        return None
    ceiling = R.AFA_CEILING_EXEMPT_PAISE if ctx.case.afa_exempt_category else R.AFA_CEILING_PAISE
    if ctx.case.amount_paise <= ceiling:
        return None
    return RuleVerdict(
        rule_id=R.RBI_AFA_CEILING.rule_id,
        citation=R.RBI_AFA_CEILING.citation,
        decision=PolicyDecision.BLOCK,
        message=(
            f"INR {rupees(ctx.case.amount_paise)} exceeds the AFA-free ceiling of "
            f"INR {rupees(ceiling)} and no additional factor authentication is on "
            f"record. Auto-debit is not permitted."
        ),
    )


def _contact_caps(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> list[RuleVerdict]:
    if a.action not in CONTACT_ACTIONS:
        return []
    out: list[RuleVerdict] = []
    if ctx.contacts_24h >= p.max_contacts_per_24h:
        out.append(
            RuleVerdict(
                rule_id=R.MERCHANT_CONTACT_CAP_24H.rule_id,
                citation=R.MERCHANT_CONTACT_CAP_24H.citation,
                decision=PolicyDecision.BLOCK,
                message=(
                    f"{ctx.contacts_24h} contact(s) to this customer in the last 24h; "
                    f"cap is {p.max_contacts_per_24h}."
                ),
            )
        )
    if ctx.contacts_7d >= p.max_contacts_per_7d:
        out.append(
            RuleVerdict(
                rule_id=R.MERCHANT_CONTACT_CAP_7D.rule_id,
                citation=R.MERCHANT_CONTACT_CAP_7D.citation,
                decision=PolicyDecision.BLOCK,
                message=(
                    f"{ctx.contacts_7d} contact(s) to this customer in the last 7 days; "
                    f"cap is {p.max_contacts_per_7d}. Counted across all "
                    f"{ctx.open_sibling_cases + 1} open case(s) for this customer."
                ),
            )
        )
    return out


def _quiet_hours(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action not in CONTACT_ACTIONS:
        return None
    hour = (ctx.now + timedelta(hours=a.delay_hours)).hour
    if p.quiet_hours_end_hour <= hour < p.quiet_hours_start_hour:
        return None
    if hour < p.quiet_hours_end_hour:
        defer = p.quiet_hours_end_hour - hour
    else:
        defer = (24 - hour) + p.quiet_hours_end_hour
    return RuleVerdict(
        rule_id=R.TRAI_QUIET_HOURS.rule_id,
        citation=R.TRAI_QUIET_HOURS.citation,
        decision=PolicyDecision.DEFER,
        message=(
            f"{hour:02d}:00 falls outside the permitted "
            f"{p.quiet_hours_end_hour:02d}:00-{p.quiet_hours_start_hour:02d}:00 "
            f"window for commercial communication."
        ),
        defer_hours=defer,
    )


def _dlt_consent(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action not in CONTACT_ACTIONS or ctx.customer.dlt_consent:
        return None
    return RuleVerdict(
        rule_id=R.TRAI_DLT_CONSENT.rule_id,
        citation=R.TRAI_DLT_CONSENT.citation,
        decision=PolicyDecision.BLOCK,
        message="No registered commercial-communication consent for this customer.",
    )


def _opt_out(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    # Escalation is included: a collections call is commercial contact too.
    if a.action is ActionType.NO_ACTION or not ctx.customer.opted_out:
        return None
    return RuleVerdict(
        rule_id=R.CONSUMER_OPT_OUT.rule_id,
        citation=R.CONSUMER_OPT_OUT.citation,
        decision=PolicyDecision.BLOCK,
        message="Customer has opted out of commercial contact.",
    )


def _promise_active(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    # Nothing is exempt except doing nothing. Putting a human on the phone to
    # someone who has already committed to a date is the same breach of trust
    # as an automated nudge, with a bigger bill attached.
    if a.action is ActionType.NO_ACTION:
        return None
    if ctx.promise.state is not PromiseState.ACTIVE:
        return None
    due = ctx.promise.promised_for
    if due is not None and ctx.now >= due:
        return None  # promise has come due; it is no longer protective
    when = due.strftime("%d %b %H:%M") if due else "an agreed date"
    return RuleVerdict(
        rule_id=R.PROMISE_ACTIVE.rule_id,
        citation=R.PROMISE_ACTIVE.citation,
        decision=PolicyDecision.BLOCK,
        message=f"Customer has an active promise to pay by {when}. Honour it before acting.",
    )


def _approval_threshold(ctx: CaseContext, a: CandidateAction, p: MerchantPolicy) -> Optional[RuleVerdict]:
    if a.action in (ActionType.NO_ACTION, ActionType.HUMAN_ESCALATION):
        return None
    if ctx.case.amount_paise < p.human_approval_threshold_paise:
        return None
    return RuleVerdict(
        rule_id=R.MERCHANT_APPROVAL_THRESHOLD.rule_id,
        citation=R.MERCHANT_APPROVAL_THRESHOLD.citation,
        decision=PolicyDecision.REQUIRE_APPROVAL,
        message=(
            f"Exposure of INR {rupees(ctx.case.amount_paise)} is at or above the "
            f"INR {rupees(p.human_approval_threshold_paise)} autonomy threshold. "
            f"A human must approve."
        ),
    )


_SINGLE_CHECKS = (
    _allowed,
    _retry_cap,
    _retry_gap,
    _instrument_invalid,
    _compliance_blocked,
    _rbi_pre_debit_notice,
    _rbi_afa_ceiling,
    _quiet_hours,
    _dlt_consent,
    _opt_out,
    _promise_active,
    _approval_threshold,
)

# BLOCK beats approval beats defer. A blocked action is not made lawful by
# waiting, and an action needing a human is surfaced rather than silently
# postponed.
_PRECEDENCE = (
    PolicyDecision.BLOCK,
    PolicyDecision.REQUIRE_APPROVAL,
    PolicyDecision.DEFER,
)


def evaluate(ctx: CaseContext, action: CandidateAction, policy: MerchantPolicy | None = None) -> PolicyVerdict:
    """Validate one proposed action against every rule. Pure function."""
    p = policy or load_policy()
    verdicts: list[RuleVerdict] = []
    for check in _SINGLE_CHECKS:
        v = check(ctx, action, p)
        if v is not None:
            verdicts.append(v)
    verdicts.extend(_contact_caps(ctx, action, p))

    decision = PolicyDecision.PASS
    for level in _PRECEDENCE:
        if any(v.decision is level for v in verdicts):
            decision = level
            break
    return PolicyVerdict(decision=decision, verdicts=verdicts)


def defer_hours(verdict: PolicyVerdict) -> int:
    """How long to wait before this action becomes lawful. 0 if never."""
    hours = [v.defer_hours for v in verdict.verdicts if v.decision is PolicyDecision.DEFER]
    return max(hours) if hours else 0
