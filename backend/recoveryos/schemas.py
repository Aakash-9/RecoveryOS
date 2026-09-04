"""Typed domain model for RecoveryOS.

Two rules encoded here that the rest of the system depends on:

1. Money is integer **paise** everywhere. Rupees exist only at presentation
   boundaries. No float ever touches an amount.
2. `CaseContext` is the *only* view the decision engine is allowed to see.
   `GroundTruth` (the simulator's hidden response model) lives in a separate
   module the engine never imports. `tests/test_audit_and_isolation.py` enforces that --
   it is what keeps the evaluation honest.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class CaseType(str, Enum):
    SUBSCRIPTION_RENEWAL = "SUBSCRIPTION_RENEWAL"
    ONE_TIME_PAYMENT = "ONE_TIME_PAYMENT"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    CHECKOUT_ABANDONMENT = "CHECKOUT_ABANDONMENT"


class FailureReason(str, Enum):
    """Real Razorpay / NPCI failure reasons.

    Kept faithful to the published error codes rather than invented labels.
    The diagnosis engine earns its keep because different codes imply
    structurally different recovery options -- retrying a dead card is the
    exact waste this system exists to remove.
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"          # NPCI Z9
    PAYMENT_DECLINED = "payment_declined"              # do-not-honour
    CARD_EXPIRED = "card_expired"
    INVALID_VPA = "invalid_vpa"
    VPA_RESOLUTION_FAILED = "vpa_resolution_failed"
    BANK_TECHNICAL_ERROR = "bank_technical_error"      # issuer downtime
    GATEWAY_TECHNICAL_ERROR = "gateway_technical_error"
    PAYMENT_TIMED_OUT = "payment_timed_out"
    COLLECT_REQUEST_EXPIRED = "payment_collect_request_expired"  # NPCI U69
    MANDATE_REVOKED = "mandate_revoked"
    MANDATE_PAUSED = "mandate_paused"
    AFA_REQUIRED = "afa_required"                      # RBI e-mandate AFA
    PRE_DEBIT_NOTICE_MISSING = "pre_debit_notice_missing"
    NOT_APPLICABLE = "not_applicable"                  # cart / invoice cases


class Retryability(str, Enum):
    """What a failure reason *structurally* permits. Set by diagnosis."""

    TRANSIENT_LIQUIDITY = "TRANSIENT_LIQUIDITY"    # money will appear; time it
    TRANSIENT_TECHNICAL = "TRANSIENT_TECHNICAL"    # rail issue; short retry
    INSTRUMENT_INVALID = "INSTRUMENT_INVALID"      # needs a new instrument
    MANDATE_INVALID = "MANDATE_INVALID"            # needs re-authorisation
    COMPLIANCE_BLOCKED = "COMPLIANCE_BLOCKED"      # retry is not lawful yet
    NO_FAILURE = "NO_FAILURE"                      # cart / invoice: never failed


class ActionType(str, Enum):
    NO_ACTION = "NO_ACTION"
    DELAYED_RETRY = "DELAYED_RETRY"
    PAYMENT_LINK = "PAYMENT_LINK"
    CUSTOMER_MESSAGE = "CUSTOMER_MESSAGE"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"


class LinkVariant(str, Enum):
    COLLECT_NOW = "COLLECT_NOW"              # pay this amount with any method
    UPDATE_INSTRUMENT = "UPDATE_INSTRUMENT"  # fix the card/mandate for future


class InstrumentType(str, Enum):
    CARD = "CARD"
    UPI = "UPI"
    NETBANKING = "NETBANKING"
    WALLET = "WALLET"


class CaseState(str, Enum):
    OPEN = "OPEN"
    WAITING = "WAITING"        # deferred, or a promise-to-pay is running
    RECOVERED = "RECOVERED"
    STOPPED = "STOPPED"
    ESCALATED = "ESCALATED"


TERMINAL_STATES = {CaseState.RECOVERED, CaseState.STOPPED, CaseState.ESCALATED}


class StopReason(str, Enum):
    RETRY_CAP_REACHED = "RETRY_CAP_REACHED"
    CONTACT_CAP_REACHED = "CONTACT_CAP_REACHED"
    NO_POSITIVE_UTILITY = "NO_POSITIVE_UTILITY"
    NO_VALID_ACTIONS = "NO_VALID_ACTIONS"
    INSTRUMENT_UNRECOVERABLE = "INSTRUMENT_UNRECOVERABLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MAX_ITERATIONS = "MAX_ITERATIONS"
    CUSTOMER_OPTED_OUT = "CUSTOMER_OPTED_OUT"
    HELD_OUT = "HELD_OUT"      # silent control arm: never touched, by design


class PolicyDecision(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DEFER = "DEFER"            # lawful later, not now (quiet hours, notices)


class PromiseState(str, Enum):
    NONE = "NONE"
    ACTIVE = "ACTIVE"
    FULFILLED = "FULFILLED"
    MISSED = "MISSED"
    BROKEN = "BROKEN"


class Segment(str, Enum):
    RETAIL = "RETAIL"
    SME = "SME"
    ENTERPRISE = "ENTERPRISE"


class Channel(str, Enum):
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    EMAIL = "EMAIL"


class OutcomeKind(str, Enum):
    RECOVERED = "RECOVERED"
    NO_RESPONSE = "NO_RESPONSE"
    FAILED_AGAIN = "FAILED_AGAIN"
    PROMISE_MADE = "PROMISE_MADE"
    OPTED_OUT = "OPTED_OUT"
    TRANSFERRED = "TRANSFERRED"
    NOT_EXECUTED = "NOT_EXECUTED"


# --------------------------------------------------------------------------- #
# Reason -> retryability. Deterministic. Never LLM-decided.
# --------------------------------------------------------------------------- #

REASON_RETRYABILITY: dict[FailureReason, Retryability] = {
    FailureReason.INSUFFICIENT_FUNDS: Retryability.TRANSIENT_LIQUIDITY,
    FailureReason.PAYMENT_DECLINED: Retryability.TRANSIENT_LIQUIDITY,
    FailureReason.BANK_TECHNICAL_ERROR: Retryability.TRANSIENT_TECHNICAL,
    FailureReason.GATEWAY_TECHNICAL_ERROR: Retryability.TRANSIENT_TECHNICAL,
    FailureReason.PAYMENT_TIMED_OUT: Retryability.TRANSIENT_TECHNICAL,
    FailureReason.COLLECT_REQUEST_EXPIRED: Retryability.TRANSIENT_TECHNICAL,
    FailureReason.CARD_EXPIRED: Retryability.INSTRUMENT_INVALID,
    FailureReason.INVALID_VPA: Retryability.INSTRUMENT_INVALID,
    FailureReason.VPA_RESOLUTION_FAILED: Retryability.INSTRUMENT_INVALID,
    FailureReason.MANDATE_REVOKED: Retryability.MANDATE_INVALID,
    FailureReason.MANDATE_PAUSED: Retryability.MANDATE_INVALID,
    FailureReason.AFA_REQUIRED: Retryability.COMPLIANCE_BLOCKED,
    FailureReason.PRE_DEBIT_NOTICE_MISSING: Retryability.COMPLIANCE_BLOCKED,
    FailureReason.NOT_APPLICABLE: Retryability.NO_FAILURE,
}

# Human-readable diagnosis per reason. Deterministic baseline; the LLM may
# rewrite the prose but never the classification.
REASON_DIAGNOSIS: dict[FailureReason, str] = {
    FailureReason.INSUFFICIENT_FUNDS: (
        "Account had insufficient balance at debit time. This is a liquidity "
        "timing problem, not a solvency problem -- the same instrument will "
        "usually clear once the balance is replenished."
    ),
    FailureReason.PAYMENT_DECLINED: (
        "Issuer declined the debit (do-not-honour). Often balance or risk "
        "related; a retry in a different window can succeed."
    ),
    FailureReason.CARD_EXPIRED: (
        "The stored card has expired. No number of retries can succeed -- the "
        "customer must supply a new instrument."
    ),
    FailureReason.INVALID_VPA: (
        "The UPI ID is not a valid handle. Retrying the same VPA is futile; a "
        "new payment instrument is required."
    ),
    FailureReason.VPA_RESOLUTION_FAILED: (
        "The UPI handle could not be resolved by the PSP. Retrying the same "
        "handle is futile; collect via a fresh instrument."
    ),
    FailureReason.BANK_TECHNICAL_ERROR: (
        "Issuer-side downtime on the rail. Nothing is wrong with the customer "
        "or the instrument. Wait for the window to clear, then retry."
    ),
    FailureReason.GATEWAY_TECHNICAL_ERROR: (
        "Partner-bank technical error. Transient; a short-delay retry is the "
        "correct and cheapest response."
    ),
    FailureReason.PAYMENT_TIMED_OUT: (
        "The debit timed out before confirmation. Transient; retry after a "
        "short cool-off to avoid a double debit."
    ),
    FailureReason.COLLECT_REQUEST_EXPIRED: (
        "The UPI collect request expired before the customer approved it "
        "(NPCI U69). The customer did not decline -- they did not act in time."
    ),
    FailureReason.MANDATE_REVOKED: (
        "The e-mandate has been revoked by the customer. Further debits are "
        "not authorised. Recovery requires a fresh mandate."
    ),
    FailureReason.MANDATE_PAUSED: (
        "The e-mandate is paused. Debits are not authorised until the customer "
        "resumes it."
    ),
    FailureReason.AFA_REQUIRED: (
        "Debit exceeds the additional-factor-authentication ceiling under the "
        "RBI e-mandate framework. It cannot be auto-debited without AFA."
    ),
    FailureReason.PRE_DEBIT_NOTICE_MISSING: (
        "The mandatory 24-hour pre-debit notification was not issued, so the "
        "recurring debit is not compliant and must not be attempted."
    ),
    FailureReason.NOT_APPLICABLE: (
        "No payment was attempted. Revenue is at risk from abandonment or "
        "non-payment rather than from a technical failure."
    ),
}


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #


class Customer(BaseModel):
    model_config = ConfigDict(frozen=True)

    customer_id: str
    name: str
    segment: Segment
    tenure_months: int
    lifetime_value_paise: int
    prior_payments_ok: int
    prior_payments_failed: int
    # Past failures that resolved with no intervention at all. Merchants have
    # this in their data and almost nobody uses it -- it is the single best
    # observable predictor of whether chasing someone is wasted effort.
    prior_self_cures: int = 0
    # Observed pattern: this customer's successful debits cluster in the first
    # week of the month. Salary-cycle timing, visible without any model.
    pays_after_payday: bool = False
    # Which action historically worked for this customer, e.g.
    # {"DELAYED_RETRY": 3, "PAYMENT_LINK": 1}. Observed history, not truth.
    prior_recoveries_by_action: dict[str, int] = Field(default_factory=dict)
    preferred_channel: Channel = Channel.WHATSAPP
    dlt_consent: bool = True          # TRAI / DLT commercial-comms consent
    opted_out: bool = False


class Case(BaseModel):
    """A unit of revenue at risk. Everything here is observable to the engine."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    customer_id: str
    case_type: CaseType
    amount_paise: int
    currency: str = "INR"

    failure_reason: FailureReason
    raw_error_code: Optional[str] = None

    created_at: datetime          # when the revenue went at risk
    due_at: Optional[datetime] = None

    is_recurring: bool = False
    mandate_id: Optional[str] = None
    pre_debit_notice_sent_at: Optional[datetime] = None
    afa_present: bool = False
    afa_exempt_category: bool = False   # insurance / MF / credit-card bill

    instrument_type: InstrumentType = InstrumentType.CARD
    instrument_expired: bool = False

    state: CaseState = CaseState.OPEN
    stop_reason: Optional[StopReason] = None
    recovered_paise: int = 0

    attempts_made: int = 0
    last_attempt_at: Optional[datetime] = None
    # When this case is next eligible for a decision. A deferred retry or a
    # live promise parks the case here instead of leaving it spinning.
    next_action_at: Optional[datetime] = None

    # Evaluation metadata. NOT an input to scoring -- used by tests and the
    # evaluation report only.
    archetype: str = "UNSPECIFIED"
    is_holdout: bool = False


class Promise(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    state: PromiseState = PromiseState.NONE
    promised_at: Optional[datetime] = None
    promised_for: Optional[datetime] = None
    promised_amount_paise: Optional[int] = None
    confidence: float = 0.0
    source_text: Optional[str] = None


class CaseContext(BaseModel):
    """The complete, verified view handed to diagnosis and scoring.

    Fatigue counters are **customer-level**, deliberately: a customer with a
    failed subscription and an overdue invoice is still one human with one
    inbox. Their cases compete for the same contact budget.
    """

    model_config = ConfigDict(frozen=True)

    case: Case
    customer: Customer
    promise: Promise
    now: datetime

    contacts_24h: int = 0
    contacts_7d: int = 0
    last_contact_at: Optional[datetime] = None

    open_sibling_cases: int = 0             # other live cases, same customer
    sibling_max_amount_paise: int = 0

    @property
    def hours_since_failure(self) -> float:
        return (self.now - self.case.created_at).total_seconds() / 3600.0

    @property
    def days_overdue(self) -> float:
        if self.case.due_at is None:
            return 0.0
        return max(0.0, (self.now - self.case.due_at).total_seconds() / 86400.0)

    @property
    def retryability(self) -> Retryability:
        return REASON_RETRYABILITY[self.case.failure_reason]


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


class CandidateAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ActionType
    variant: Optional[LinkVariant] = None
    delay_hours: int = 0                    # for DELAYED_RETRY / DEFER
    channel: Optional[Channel] = None
    rationale: str = ""

    @property
    def label(self) -> str:
        if self.variant:
            return f"{self.action.value}:{self.variant.value}"
        if self.action is ActionType.DELAYED_RETRY and self.delay_hours:
            return f"{self.action.value}+{self.delay_hours}h"
        return self.action.value


class ScoredAction(BaseModel):
    """Every number is in paise and every one is explainable.

        utility = expected_incremental - cost - fatigue_penalty - risk_penalty

    NO_ACTION always scores exactly 0. That is the entire product thesis: an
    intervention is taken only when it beats leaving the customer alone.
    """

    model_config = ConfigDict(frozen=True)

    candidate: CandidateAction
    p_treated: float                 # P(recover | this action)
    p_self_cure: float               # P(recover | NO_ACTION)  <- the baseline
    uplift: float                    # max(0, p_treated - p_self_cure)
    expected_incremental_paise: int
    cost_paise: int
    fatigue_penalty_paise: int
    risk_penalty_paise: int
    utility_paise: int
    explanation: list[str] = Field(default_factory=list)


class RuleVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    citation: str
    decision: PolicyDecision
    message: str
    defer_hours: int = 0


class PolicyVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: PolicyDecision
    verdicts: list[RuleVerdict] = Field(default_factory=list)

    @property
    def blocking(self) -> list[RuleVerdict]:
        return [v for v in self.verdicts if v.decision is not PolicyDecision.PASS]


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: OutcomeKind
    recovered_paise: int = 0
    detail: str = ""
    promise: Optional[Promise] = None
    contact_made: bool = False


class Decision(BaseModel):
    """One full pass of the loop for one case. This is the audit unit."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    iteration: int
    at: datetime
    state_before: CaseState
    diagnosis: str
    retryability: Retryability
    p_self_cure: float
    scored: list[ScoredAction]
    chosen: Optional[CandidateAction]
    policy: PolicyVerdict
    execution: Optional[ExecutionResult]
    state_after: CaseState
    stop_reason: Optional[StopReason] = None
    narrative: Optional[str] = None       # LLM prose. Never load-bearing.


# --------------------------------------------------------------------------- #
# Presentation helpers -- the only place rupees exist.
# --------------------------------------------------------------------------- #


def rupees(paise: int) -> str:
    """Format paise as an Indian-grouped rupee string, e.g. 240000000 -> 24,00,000.00"""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{sign}{s}.{frac:02d}"
