"""What a correct system does with each archetype.

The scenario library is the specification. This module states, per archetype,
the decision RecoveryOS is required to reach -- and, just as importantly, the
decisions it is required *not* to reach. `tests/test_decisions.py` asserts
against it, so a change in the engine that quietly starts spamming fatigued
customers fails the build rather than shipping.

`expected_action` is used where exactly one answer is right. Where more than
one action is defensible (a cart nudge is a message or a link -- both are a
prompt contact, and arguing about which is theatre), `expected_any` lists the
acceptable set and `forbidden` carries the real assertion.
"""

from __future__ import annotations

from .schemas import ActionType, CaseState, PolicyDecision

CONTACTS = (ActionType.CUSTOMER_MESSAGE, ActionType.PAYMENT_LINK)

# One right answer.
EXPECTED_ACTION: dict[str, ActionType] = {
    "NSF_TRANSIENT": ActionType.DELAYED_RETRY,
    "NSF_SALARY_CYCLE": ActionType.DELAYED_RETRY,
    "CARD_EXPIRED": ActionType.PAYMENT_LINK,
    "INVALID_VPA": ActionType.PAYMENT_LINK,
    "ISSUER_DOWNTIME": ActionType.DELAYED_RETRY,
    "MANDATE_REVOKED": ActionType.PAYMENT_LINK,
    "AFA_CEILING_BREACH": ActionType.PAYMENT_LINK,
    "SELF_HEALER": ActionType.NO_ACTION,
    "PROMISE_ACTIVE": ActionType.NO_ACTION,
    "OPTED_OUT": ActionType.NO_ACTION,
    "HIGH_VALUE_APPROVAL": ActionType.HUMAN_ESCALATION,
    "ARBITRATION_MAJOR": ActionType.PAYMENT_LINK,
}

# Several defensible answers.
EXPECTED_ANY: dict[str, tuple[ActionType, ...]] = {
    "CART_ABANDONED_HOT": CONTACTS,
    "RBI_NOTICE_MISSING": (ActionType.DELAYED_RETRY, ActionType.PAYMENT_LINK),
    "PROMISE_BROKEN": CONTACTS + (ActionType.HUMAN_ESCALATION,),
    "RETRY_CAP_EXHAUSTED": (ActionType.NO_ACTION, ActionType.HUMAN_ESCALATION),
    "CONTACT_FATIGUED": (ActionType.NO_ACTION, ActionType.HUMAN_ESCALATION),
    "ARBITRATION_MINOR": (ActionType.NO_ACTION, ActionType.PAYMENT_LINK),
}

# Actions that must never be chosen, whatever else changes.
FORBIDDEN: dict[str, tuple[ActionType, ...]] = {
    "CARD_EXPIRED": (ActionType.DELAYED_RETRY,),
    "INVALID_VPA": (ActionType.DELAYED_RETRY,),
    "MANDATE_REVOKED": (ActionType.DELAYED_RETRY,),
    "AFA_CEILING_BREACH": (ActionType.DELAYED_RETRY,),
    "SELF_HEALER": CONTACTS,
    "CONTACT_FATIGUED": CONTACTS,
    "PROMISE_ACTIVE": CONTACTS + (ActionType.DELAYED_RETRY, ActionType.HUMAN_ESCALATION),
    "OPTED_OUT": CONTACTS + (ActionType.HUMAN_ESCALATION,),
    "RETRY_CAP_EXHAUSTED": (ActionType.DELAYED_RETRY,),
    "ISSUER_DOWNTIME": CONTACTS,
}

# The guardrail verdict the top-scoring action must receive.
EXPECTED_TOP_VERDICT: dict[str, PolicyDecision] = {
    "RBI_NOTICE_MISSING": PolicyDecision.DEFER,
    "HIGH_VALUE_APPROVAL": PolicyDecision.REQUIRE_APPROVAL,
    "AFA_CEILING_BREACH": PolicyDecision.BLOCK,
    "PROMISE_ACTIVE": PolicyDecision.BLOCK,
    "OPTED_OUT": PolicyDecision.BLOCK,
    "RETRY_CAP_EXHAUSTED": PolicyDecision.BLOCK,
}

# The rule that must be cited when the top action is refused.
EXPECTED_RULE: dict[str, str] = {
    "RBI_NOTICE_MISSING": "RBI-EM-2026-PDN-24H",
    "AFA_CEILING_BREACH": "POLICY-COMPLIANCE-BLOCKED",
    "HIGH_VALUE_APPROVAL": "MERCHANT-APPROVAL-THRESHOLD",
    "PROMISE_ACTIVE": "POLICY-PROMISE-ACTIVE",
    "OPTED_OUT": "TRAI-DLT-CONSENT",
    "RETRY_CAP_EXHAUSTED": "MERCHANT-RETRY-CAP",
    "CONTACT_FATIGUED": "MERCHANT-CONTACT-CAP-7D",
}

# Where the case must end up.
EXPECTED_STATE: dict[str, CaseState] = {
    "HIGH_VALUE_APPROVAL": CaseState.ESCALATED,
}

# Minimum retry delay, in hours. The salary-cycle case must reach into the
# first week of the month rather than firing the standard next-day retry.
EXPECTED_MIN_DELAY: dict[str, int] = {
    "NSF_SALARY_CYCLE": 120,
}

# Maximum retry delay. A rail outage wants a short retry, not next week.
EXPECTED_MAX_DELAY: dict[str, int] = {
    "ISSUER_DOWNTIME": 24,
}
