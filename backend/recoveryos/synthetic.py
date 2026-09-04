"""Synthetic scenario generator.

Not random rows. Every case is an instance of a named *archetype* that exists
to exercise one specific decision, and each archetype declares the decision a
correct system should reach. `tests/test_decisions.py` asserts against those
declarations, so the scenario library doubles as the specification.

Reproducibility: everything is driven by one seeded `random.Random`. The same
`--seed` produces a byte-identical database on any machine, which
`tests/test_determinism.py` checks.

This data is fabricated. It contains no real customer, merchant, payment or
outcome, and nothing here should be read as evidence about real-world
recovery performance.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .schemas import (
    ActionType,
    Case,
    CaseState,
    CaseType,
    Channel,
    Customer,
    FailureReason,
    InstrumentType,
    LinkVariant,
    Promise,
    PromiseState,
    Segment,
)
from .simulator.truth import GroundTruth

# The reference clock for the whole synthetic world. Deliberately late in the
# month: it makes the salary-cycle decision in NSF_SALARY_CYCLE real rather
# than incidental.
BASE_NOW = datetime(2026, 9, 24, 10, 30)

FIRST_NAMES = [
    "Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vikram", "Ishita",
    "Arjun", "Nandini", "Farhan", "Priya", "Devansh", "Sneha", "Imran", "Tara",
    "Manish", "Kavya", "Rahul", "Zoya",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Nair", "Bose", "Kulkarni", "Reddy", "Menon", "Chatterjee",
    "Gupta", "Desai", "Khan", "Patel", "Rao", "Verma", "Joshi", "Pillai",
]

RAW_CODES = {
    FailureReason.INSUFFICIENT_FUNDS: "Z9",
    FailureReason.COLLECT_REQUEST_EXPIRED: "U69",
    FailureReason.BANK_TECHNICAL_ERROR: "BT-503",
    FailureReason.GATEWAY_TECHNICAL_ERROR: "GW-502",
    FailureReason.CARD_EXPIRED: "54",
    FailureReason.PAYMENT_DECLINED: "05",
    FailureReason.INVALID_VPA: "XH",
    FailureReason.VPA_RESOLUTION_FAILED: "XV",
    FailureReason.MANDATE_REVOKED: "M-REVOKED",
    FailureReason.MANDATE_PAUSED: "M-PAUSED",
    FailureReason.AFA_REQUIRED: "AFA-REQ",
    FailureReason.PRE_DEBIT_NOTICE_MISSING: "PDN-MISSING",
}


@dataclass(frozen=True)
class Archetype:
    """One teachable situation, plus the decision a correct system reaches."""

    name: str
    lesson: str
    expected_action: Optional[ActionType]
    expected_state: Optional[CaseState]

    case_type: CaseType
    reason: FailureReason
    amount_range: tuple[int, int]          # paise

    weight: float = 1.0                    # share of the randomised tail
    attempts: int = 0
    hours_since_failure: int = 3
    prior_contacts_7d: int = 0
    prior_contacts_24h: int = 0

    is_recurring: bool = True
    instrument: InstrumentType = InstrumentType.CARD
    instrument_expired: bool = False
    notice_hours_before: Optional[int] = 30   # pre-debit notice, hours ago
    afa_present: bool = True
    afa_exempt: bool = False

    promise: Optional[str] = None          # "ACTIVE" | "BROKEN"
    dlt_consent: bool = True
    opted_out: bool = False
    good_history: bool = True
    self_curer: bool = False      # history shows they fix failures unprompted
    payday_payer: bool = False    # their debits clear in the first week of the month
    days_overdue: int = 0

    truth: dict = field(default_factory=dict)
    pairs_with: Optional[str] = None       # emits a second linked case


# --------------------------------------------------------------------------- #
# The scenario library
# --------------------------------------------------------------------------- #

ARCHETYPES: list[Archetype] = [
    Archetype(
        name="NSF_TRANSIENT",
        lesson="Reliable payer, one-off shortfall. The cheapest action wins: wait and retry.",
        expected_action=ActionType.DELAYED_RETRY,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.INSUFFICIENT_FUNDS,
        amount_range=(49900, 299900),
        weight=3.0,
        truth=dict(
            self_cure_prob=0.18,
            retry_curve={"6": 0.30, "24": 0.62, "72": 0.55, "168": 0.40},
            link_collect_prob=0.34, link_update_prob=0.20, message_prob=0.22,
            human_prob=0.45, fatigue_sensitivity=0.30, ceiling=0.88,
        ),
    ),
    Archetype(
        name="NSF_SALARY_CYCLE",
        lesson=(
            "Balance is thin until payday. Retrying now is near-worthless; the "
            "same retry timed into the salary window is a different decision."
        ),
        expected_action=ActionType.DELAYED_RETRY,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.INSUFFICIENT_FUNDS,
        amount_range=(79900, 249900),
        weight=2.0,
        instrument=InstrumentType.UPI,
        payday_payer=True,
        truth=dict(
            self_cure_prob=0.12,
            retry_curve={"6": 0.08, "24": 0.14, "72": 0.22, "168": 0.46},
            link_collect_prob=0.18, link_update_prob=0.12, message_prob=0.15,
            human_prob=0.30, fatigue_sensitivity=0.35,
            salary_cycle_sensitive=True, ceiling=0.85,
        ),
    ),
    Archetype(
        name="CARD_EXPIRED",
        lesson="Retry can never clear a dead card. Only a new instrument can.",
        expected_action=ActionType.PAYMENT_LINK,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.CARD_EXPIRED,
        amount_range=(99900, 499900),
        weight=2.5,
        instrument_expired=True,
        truth=dict(
            self_cure_prob=0.06,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.30, link_update_prob=0.52, message_prob=0.24,
            human_prob=0.40, fatigue_sensitivity=0.28, ceiling=0.86,
        ),
    ),
    Archetype(
        name="INVALID_VPA",
        lesson="Dead UPI handle. Same shape as an expired card, different rail.",
        expected_action=ActionType.PAYMENT_LINK,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.INVALID_VPA,
        amount_range=(49900, 199900),
        weight=1.5,
        instrument=InstrumentType.UPI,
        truth=dict(
            self_cure_prob=0.05,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.33, link_update_prob=0.48, message_prob=0.21,
            human_prob=0.38, fatigue_sensitivity=0.30, ceiling=0.84,
        ),
    ),
    Archetype(
        name="ISSUER_DOWNTIME",
        lesson=(
            "Nothing is wrong with the customer. Contacting them would be noise; "
            "a short retry once the rail recovers is free money."
        ),
        expected_action=ActionType.DELAYED_RETRY,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.BANK_TECHNICAL_ERROR,
        amount_range=(29900, 189900),
        weight=2.0,
        hours_since_failure=1,
        truth=dict(
            self_cure_prob=0.20,
            retry_curve={"6": 0.71, "24": 0.78, "72": 0.72, "168": 0.60},
            link_collect_prob=0.26, link_update_prob=0.14, message_prob=0.16,
            human_prob=0.35, fatigue_sensitivity=0.32, ceiling=0.90,
        ),
    ),
    Archetype(
        name="RETRY_CAP_EXHAUSTED",
        lesson="Two attempts already spent. Automation must stop and say so.",
        expected_action=None,
        expected_state=CaseState.STOPPED,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.PAYMENT_DECLINED,
        amount_range=(19900, 99900),
        weight=1.5,
        attempts=2,
        hours_since_failure=96,
        prior_contacts_7d=2,
        prior_contacts_24h=1,
        good_history=False,
        truth=dict(
            self_cure_prob=0.08,
            retry_curve={"6": 0.06, "24": 0.09, "72": 0.09, "168": 0.07},
            link_collect_prob=0.12, link_update_prob=0.10, message_prob=0.07,
            human_prob=0.22, fatigue_sensitivity=0.55, ceiling=0.60,
        ),
    ),
    Archetype(
        name="SELF_HEALER",
        lesson=(
            "History says this customer fixes their own card within days. The "
            "action is permitted -- it simply is not worth its own cost."
        ),
        expected_action=ActionType.NO_ACTION,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.CARD_EXPIRED,
        amount_range=(14900, 39900),
        weight=2.5,
        instrument_expired=True,
        prior_contacts_7d=1,
        self_curer=True,
        truth=dict(
            self_cure_prob=0.74,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.77, link_update_prob=0.80, message_prob=0.76,
            human_prob=0.78, fatigue_sensitivity=0.45, ceiling=0.86,
        ),
    ),
    Archetype(
        name="CONTACT_FATIGUED",
        lesson=(
            "Already contacted twice this week. A third message is both blocked "
            "and, on the numbers, worthless."
        ),
        expected_action=ActionType.NO_ACTION,
        expected_state=None,
        case_type=CaseType.INVOICE_OVERDUE,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(450000, 1800000),
        weight=1.5,
        is_recurring=False,
        prior_contacts_7d=2,
        prior_contacts_24h=1,
        days_overdue=9,
        truth=dict(
            self_cure_prob=0.16,
            retry_curve={},
            link_collect_prob=0.24, link_update_prob=0.0, message_prob=0.18,
            human_prob=0.34, fatigue_sensitivity=0.85, ceiling=0.70,
        ),
    ),
    Archetype(
        name="PROMISE_ACTIVE",
        lesson="They committed to a date that has not arrived. Chasing them now costs goodwill and buys nothing.",
        expected_action=ActionType.NO_ACTION,
        expected_state=None,
        case_type=CaseType.INVOICE_OVERDUE,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(600000, 2400000),
        weight=1.2,
        is_recurring=False,
        promise="ACTIVE",
        days_overdue=6,
        truth=dict(
            self_cure_prob=0.62,
            retry_curve={},
            link_collect_prob=0.66, link_update_prob=0.0, message_prob=0.62,
            human_prob=0.70, fatigue_sensitivity=0.60, ceiling=0.80,
        ),
    ),
    Archetype(
        name="PROMISE_BROKEN",
        lesson="The promised date passed with no payment. The protection lapses and the case re-enters the loop.",
        expected_action=None,
        expected_state=None,
        case_type=CaseType.INVOICE_OVERDUE,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(700000, 3200000),
        weight=1.2,
        is_recurring=False,
        promise="BROKEN",
        days_overdue=14,
        good_history=False,
        truth=dict(
            self_cure_prob=0.10,
            retry_curve={},
            link_collect_prob=0.28, link_update_prob=0.0, message_prob=0.16,
            human_prob=0.44, fatigue_sensitivity=0.50, ceiling=0.72,
        ),
    ),
    Archetype(
        name="HIGH_VALUE_APPROVAL",
        lesson="Above the merchant autonomy threshold. A human decides, and the agent says exactly why.",
        expected_action=ActionType.HUMAN_ESCALATION,
        expected_state=CaseState.ESCALATED,
        case_type=CaseType.INVOICE_OVERDUE,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(10500000, 20000000),
        weight=0.8,
        is_recurring=False,
        days_overdue=21,
        truth=dict(
            self_cure_prob=0.14,
            retry_curve={},
            link_collect_prob=0.30, link_update_prob=0.0, message_prob=0.18,
            human_prob=0.58, fatigue_sensitivity=0.35, ceiling=0.80,
        ),
    ),
    Archetype(
        name="MANDATE_REVOKED",
        lesson="Authorisation is gone. Debiting anyway would not just fail, it would be unauthorised.",
        expected_action=ActionType.PAYMENT_LINK,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.MANDATE_REVOKED,
        amount_range=(89900, 349900),
        weight=1.2,
        instrument=InstrumentType.UPI,
        truth=dict(
            self_cure_prob=0.04,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.31, link_update_prob=0.40, message_prob=0.20,
            human_prob=0.36, fatigue_sensitivity=0.33, ceiling=0.78,
        ),
    ),
    Archetype(
        name="RBI_NOTICE_MISSING",
        lesson=(
            "No 24-hour pre-debit notification on the mandate. The retry is not "
            "unwise, it is not lawful yet -- so it is deferred, not abandoned."
        ),
        expected_action=None,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.PRE_DEBIT_NOTICE_MISSING,
        amount_range=(99900, 399900),
        weight=1.0,
        notice_hours_before=None,
        truth=dict(
            self_cure_prob=0.09,
            retry_curve={"6": 0.40, "24": 0.52, "72": 0.48, "168": 0.38},
            link_collect_prob=0.29, link_update_prob=0.18, message_prob=0.19,
            human_prob=0.36, fatigue_sensitivity=0.32, ceiling=0.82,
        ),
    ),
    Archetype(
        name="AFA_CEILING_BREACH",
        lesson=(
            "Recurring debit above the RBI additional-factor ceiling with no AFA "
            "on record. Auto-debit is off the table; collection must be re-consented."
        ),
        expected_action=ActionType.PAYMENT_LINK,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.AFA_REQUIRED,
        amount_range=(1800000, 4500000),
        weight=1.0,
        afa_present=False,
        truth=dict(
            self_cure_prob=0.07,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.36, link_update_prob=0.44, message_prob=0.22,
            human_prob=0.50, fatigue_sensitivity=0.30, ceiling=0.80,
        ),
    ),
    Archetype(
        name="CART_ABANDONED_HOT",
        lesson="High intent, minutes old. A single well-timed nudge is worth more here than anywhere else.",
        expected_action=ActionType.CUSTOMER_MESSAGE,
        expected_state=None,
        case_type=CaseType.CHECKOUT_ABANDONMENT,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(120000, 800000),
        weight=2.0,
        is_recurring=False,
        hours_since_failure=1,
        truth=dict(
            self_cure_prob=0.11,
            retry_curve={},
            link_collect_prob=0.38, link_update_prob=0.0, message_prob=0.42,
            human_prob=0.30, fatigue_sensitivity=0.45, ceiling=0.78,
        ),
    ),
    Archetype(
        name="OPTED_OUT",
        lesson="No lawful channel remains. The honest answer is to stop, not to find a loophole.",
        expected_action=ActionType.NO_ACTION,
        expected_state=None,
        case_type=CaseType.INVOICE_OVERDUE,
        reason=FailureReason.NOT_APPLICABLE,
        amount_range=(300000, 900000),
        weight=0.8,
        is_recurring=False,
        opted_out=True,
        dlt_consent=False,
        days_overdue=11,
        truth=dict(
            self_cure_prob=0.13,
            retry_curve={},
            link_collect_prob=0.22, link_update_prob=0.0, message_prob=0.14,
            human_prob=0.40, fatigue_sensitivity=0.50, ceiling=0.70,
        ),
    ),
    Archetype(
        name="ARBITRATION_MINOR",
        lesson=(
            "Same human, two live cases, one lawful contact slot left this week. "
            "The small case must lose it to the large one."
        ),
        expected_action=ActionType.NO_ACTION,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.CARD_EXPIRED,
        amount_range=(39900, 89900),
        weight=0.0,                      # canonical only, never in the tail
        instrument_expired=True,
        prior_contacts_7d=1,
        pairs_with="ARBITRATION_MAJOR",
        truth=dict(
            self_cure_prob=0.07,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.30, link_update_prob=0.46, message_prob=0.22,
            human_prob=0.35, fatigue_sensitivity=0.40, ceiling=0.80,
        ),
    ),
    Archetype(
        name="ARBITRATION_MAJOR",
        lesson="The same customer owes far more here. This case wins the contact slot.",
        expected_action=ActionType.PAYMENT_LINK,
        expected_state=None,
        case_type=CaseType.SUBSCRIPTION_RENEWAL,
        reason=FailureReason.CARD_EXPIRED,
        amount_range=(450000, 900000),
        weight=0.0,
        instrument_expired=True,
        prior_contacts_7d=1,
        truth=dict(
            self_cure_prob=0.08,
            retry_curve={"6": 0.0, "24": 0.0, "72": 0.0, "168": 0.0},
            link_collect_prob=0.34, link_update_prob=0.50, message_prob=0.24,
            human_prob=0.42, fatigue_sensitivity=0.40, ceiling=0.84,
        ),
    ),
]

BY_NAME = {a.name: a for a in ARCHETYPES}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


@dataclass
class GeneratedCase:
    customer: Customer
    case: Case
    truth: GroundTruth
    promise: Promise
    prior_contacts: list[datetime]


def _make_customer(rng: random.Random, idx: int, arch: Archetype) -> Customer:
    ok = rng.randint(9, 40) if arch.good_history else rng.randint(1, 6)
    failed = rng.randint(0, 2) if arch.good_history else rng.randint(3, 9)
    if arch.self_curer:
        # Enough prior failures to make the self-cure rate observable, and
        # most of them resolved with nobody lifting a finger.
        failed = rng.randint(5, 9)
        self_cures = failed - rng.randint(0, 1)
    else:
        self_cures = min(failed, 1) if rng.random() < 0.2 else 0
    prior: dict[str, int] = {}
    if arch.good_history and rng.random() < 0.55:
        prior[rng.choice([ActionType.DELAYED_RETRY.value, ActionType.PAYMENT_LINK.value])] = rng.randint(1, 3)
    return Customer(
        customer_id=f"CUST_{idx:04d}",
        name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        segment=rng.choices(
            [Segment.RETAIL, Segment.SME, Segment.ENTERPRISE], weights=[6, 3, 1]
        )[0],
        tenure_months=rng.randint(1, 48),
        lifetime_value_paise=rng.randint(200000, 8000000),
        prior_payments_ok=ok,
        prior_payments_failed=failed,
        prior_self_cures=self_cures,
        pays_after_payday=arch.payday_payer,
        prior_recoveries_by_action=prior,
        preferred_channel=rng.choices(
            [Channel.WHATSAPP, Channel.SMS, Channel.EMAIL], weights=[6, 2, 2]
        )[0],
        dlt_consent=arch.dlt_consent,
        opted_out=arch.opted_out,
    )


def _make_case(
    rng: random.Random, idx: int, arch: Archetype, customer: Customer, now: datetime
) -> Case:
    amount = rng.randrange(arch.amount_range[0], arch.amount_range[1] + 1, 100)
    created = now - timedelta(hours=arch.hours_since_failure)
    due = now - timedelta(days=arch.days_overdue) if arch.days_overdue else None
    notice = (
        now - timedelta(hours=arch.notice_hours_before)
        if arch.is_recurring and arch.notice_hours_before is not None
        else None
    )
    return Case(
        case_id=f"CASE_{idx:04d}",
        customer_id=customer.customer_id,
        case_type=arch.case_type,
        amount_paise=amount,
        failure_reason=arch.reason,
        raw_error_code=RAW_CODES.get(arch.reason),
        created_at=created,
        due_at=due,
        is_recurring=arch.is_recurring,
        mandate_id=f"MND_{idx:04d}" if arch.is_recurring else None,
        pre_debit_notice_sent_at=notice,
        afa_present=arch.afa_present,
        afa_exempt_category=arch.afa_exempt,
        instrument_type=arch.instrument,
        instrument_expired=arch.instrument_expired,
        state=CaseState.OPEN,
        attempts_made=arch.attempts,
        last_attempt_at=created + timedelta(hours=1) if arch.attempts else None,
        archetype=arch.name,
    )


def _make_promise(rng: random.Random, arch: Archetype, case: Case, now: datetime) -> Promise:
    if arch.promise == "ACTIVE":
        return Promise(
            case_id=case.case_id,
            state=PromiseState.ACTIVE,
            promised_at=now - timedelta(days=1),
            promised_for=now + timedelta(days=rng.randint(2, 4)),
            promised_amount_paise=case.amount_paise,
            confidence=0.82,
            source_text="I will clear this on Friday, we are waiting on a client payment.",
        )
    if arch.promise == "BROKEN":
        return Promise(
            case_id=case.case_id,
            state=PromiseState.BROKEN,
            promised_at=now - timedelta(days=8),
            promised_for=now - timedelta(days=2),
            promised_amount_paise=case.amount_paise,
            confidence=0.77,
            source_text="Payment will be done by Monday for sure.",
        )
    return Promise(case_id=case.case_id, state=PromiseState.NONE)


def _prior_contacts(rng: random.Random, arch: Archetype, now: datetime) -> list[datetime]:
    out: list[datetime] = []
    for _ in range(arch.prior_contacts_24h):
        out.append(now - timedelta(hours=rng.randint(3, 22)))
    for _ in range(max(0, arch.prior_contacts_7d - arch.prior_contacts_24h)):
        out.append(now - timedelta(hours=rng.randint(30, 160)))
    return sorted(out)


def _jitter_truth(rng: random.Random, base: dict, case_id: str) -> GroundTruth:
    """Vary the tail cases around the archetype so the batch is not 17 clones."""

    def wob(v: float, spread: float = 0.15) -> float:
        return round(min(0.98, max(0.0, v * rng.uniform(1 - spread, 1 + spread))), 4)

    d = dict(base)
    d["self_cure_prob"] = wob(d["self_cure_prob"])
    d["retry_curve"] = {k: wob(v) for k, v in d.get("retry_curve", {}).items()}
    for key in ("link_collect_prob", "link_update_prob", "message_prob", "human_prob"):
        if key in d:
            d[key] = wob(d[key])
    d["fatigue_sensitivity"] = round(d.get("fatigue_sensitivity", 0.35) * rng.uniform(0.85, 1.15), 4)
    return GroundTruth(case_id=case_id, **d)


def generate(
    n_cases: int = 60, seed: int = 42, now: datetime = BASE_NOW
) -> list[GeneratedCase]:
    """Canonical set first (one of every archetype), then a weighted tail."""
    rng = random.Random(seed)
    # Partner archetypes are emitted alongside their primary, never on their own.
    partners = {a.pairs_with for a in ARCHETYPES if a.pairs_with}
    order = [a for a in ARCHETYPES if a.name not in partners]
    tail_pool = [a for a in ARCHETYPES if a.weight > 0]
    tail_weights = [a.weight for a in tail_pool]

    while len(order) < n_cases:
        order.append(rng.choices(tail_pool, weights=tail_weights)[0])
    order = order[:n_cases]

    out: list[GeneratedCase] = []
    idx = 0
    pending_pair: Optional[tuple[Archetype, Customer]] = None

    for arch in order:
        idx += 1
        if pending_pair is not None:
            partner_arch, customer = pending_pair
            pending_pair = None
            case = _make_case(rng, idx, partner_arch, customer, now)
            out.append(
                GeneratedCase(
                    customer=customer,
                    case=case,
                    truth=_jitter_truth(rng, partner_arch.truth, case.case_id),
                    promise=_make_promise(rng, partner_arch, case, now),
                    prior_contacts=[],   # shared with the paired case
                )
            )
            idx += 1

        customer = _make_customer(rng, idx, arch)
        case = _make_case(rng, idx, arch, customer, now)
        out.append(
            GeneratedCase(
                customer=customer,
                case=case,
                truth=_jitter_truth(rng, arch.truth, case.case_id),
                promise=_make_promise(rng, arch, case, now),
                prior_contacts=_prior_contacts(rng, arch, now),
            )
        )
        if arch.pairs_with:
            pending_pair = (BY_NAME[arch.pairs_with], customer)

    return out[:n_cases]


def assign_holdout(cases: list[GeneratedCase], fraction: float, seed: int) -> None:
    """Randomised silent control arm.

    A fraction of cases is never touched by any policy. Comparing them with the
    treated arm is the only honest way to say how much recovery an intervention
    actually *caused* -- everything else counts self-cures as a win.

    Canonical archetype instances (the first of each, used by the tests and the
    demo) are excluded so the scenario library stays fully exercised.
    """
    rng = random.Random(seed + 977)
    seen: set[str] = set()
    eligible: list[int] = []
    for i, gc in enumerate(cases):
        if gc.case.archetype in seen:
            eligible.append(i)
        seen.add(gc.case.archetype)

    for i in rng.sample(eligible, k=int(len(eligible) * fraction)):
        cases[i] = GeneratedCase(
            customer=cases[i].customer,
            case=cases[i].case.model_copy(update={"is_holdout": True}),
            truth=cases[i].truth,
            promise=cases[i].promise,
            prior_contacts=cases[i].prior_contacts,
        )
