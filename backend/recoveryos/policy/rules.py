"""The rule catalogue.

Every guardrail in RecoveryOS carries a citation. Merchant-configured limits
cite the merchant policy file; regulatory limits cite the actual Indian
regulation they come from. A blocked action is never a mystery -- the UI and
the audit log both show the rule that stopped it and where that rule comes
from.

Sources for the regulatory rules:
  * RBI, Digital Payments -- E-Mandate Framework, 2026 (issued 21 Apr 2026):
    mandatory pre-debit notification at least 24 hours before every recurring
    debit; additional-factor-authentication ceiling of INR 15,000, raised to
    INR 1,00,000 for insurance premiums, mutual-fund subscriptions and
    credit-card bill payments.
  * TRAI, Telecom Commercial Communications Customer Preference Regulations
    (TCCCPR) and the DLT regime: commercial/promotional messages are confined
    to a daytime window and every sender and template must be pre-registered
    with subscriber consent on record.

Note on scope: a dunning nudge asking a customer to pay is commercial
communication, not a transactional service alert, so the daytime window
applies to it. Getting that distinction wrong is a common compliance failure
in real recovery stacks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    citation: str
    description: str


# Regulatory ---------------------------------------------------------------- #

RBI_PRE_DEBIT_NOTICE = Rule(
    rule_id="RBI-EM-2026-PDN-24H",
    citation="RBI Digital Payments E-Mandate Framework, 2026, cl. pre-debit notification",
    description=(
        "A recurring debit may not be attempted unless the customer was "
        "notified at least 24 hours in advance with the amount, date and "
        "mandate reference."
    ),
)

RBI_AFA_CEILING = Rule(
    rule_id="RBI-EM-2026-AFA-CEILING",
    citation="RBI Digital Payments E-Mandate Framework, 2026, additional factor authentication",
    description=(
        "Recurring debits above INR 15,000 require additional factor "
        "authentication. The ceiling is INR 1,00,000 for insurance premiums, "
        "mutual-fund subscriptions and credit-card bill payments."
    ),
)

TRAI_QUIET_HOURS = Rule(
    rule_id="TRAI-TCCCPR-QUIET-HOURS",
    citation="TRAI TCCCPR / DLT commercial communication time restrictions",
    description=(
        "Commercial communication may only be delivered inside the permitted "
        "daytime window. A payment nudge is commercial, not transactional."
    ),
)

TRAI_DLT_CONSENT = Rule(
    rule_id="TRAI-DLT-CONSENT",
    citation="TRAI TCCCPR / DLT sender, template and consent registration",
    description=(
        "Commercial communication requires registered consent for the "
        "subscriber. Without it, no message may be sent."
    ),
)

CONSUMER_OPT_OUT = Rule(
    rule_id="CONSUMER-OPT-OUT",
    citation="TRAI TCCCPR customer preference registry",
    description="A customer who has opted out of commercial contact must not be contacted.",
)

# Merchant-configured ------------------------------------------------------- #

MERCHANT_ALLOWED_ACTIONS = Rule(
    rule_id="MERCHANT-ALLOWED-ACTIONS",
    citation="merchant_policy.json :: allowed_actions",
    description="Only actions on the merchant allow-list may be executed.",
)

MERCHANT_RETRY_CAP = Rule(
    rule_id="MERCHANT-RETRY-CAP",
    citation="merchant_policy.json :: max_retry_attempts",
    description="Automated debit retries are capped per case.",
)

MERCHANT_RETRY_GAP = Rule(
    rule_id="MERCHANT-RETRY-GAP",
    citation="merchant_policy.json :: min_retry_gap_hours",
    description="A minimum interval must elapse between debit attempts.",
)

MERCHANT_CONTACT_CAP_24H = Rule(
    rule_id="MERCHANT-CONTACT-CAP-24H",
    citation="merchant_policy.json :: max_contacts_per_24h",
    description="Customer contacts in any 24-hour window are capped, across all of that customer's cases.",
)

MERCHANT_CONTACT_CAP_7D = Rule(
    rule_id="MERCHANT-CONTACT-CAP-7D",
    citation="merchant_policy.json :: max_contacts_per_7d",
    description="Customer contacts in any 7-day window are capped, across all of that customer's cases.",
)

MERCHANT_APPROVAL_THRESHOLD = Rule(
    rule_id="MERCHANT-APPROVAL-THRESHOLD",
    citation="merchant_policy.json :: human_approval_threshold_paise",
    description="Recovery on high-value exposure requires a human to approve the action.",
)

# Domain safety ------------------------------------------------------------- #

INSTRUMENT_UNRECOVERABLE = Rule(
    rule_id="POLICY-INSTRUMENT-INVALID",
    citation="RecoveryOS diagnosis :: retryability class",
    description=(
        "A debit may not be retried when the instrument or mandate is "
        "structurally invalid. Retrying a dead card cannot succeed and still "
        "costs the merchant a gateway fee."
    ),
)

COMPLIANCE_BLOCKED_RETRY = Rule(
    rule_id="POLICY-COMPLIANCE-BLOCKED",
    citation="RecoveryOS diagnosis :: retryability class",
    description="A debit blocked for compliance reasons may not be retried until the blocker is cleared.",
)

PROMISE_ACTIVE = Rule(
    rule_id="POLICY-PROMISE-ACTIVE",
    citation="RecoveryOS promise-to-pay state machine",
    description=(
        "While a promise to pay is live and not yet due, the customer is not "
        "contacted and the debit is not retried. Chasing someone who has "
        "already committed to a date destroys goodwill and buys nothing."
    ),
)

# Batch allocation ---------------------------------------------------------- #
# Not compliance, but still a hard bound on autonomy: what the sweep can afford.

ALLOCATOR_CONTACT_SLOT = Rule(
    rule_id="ALLOCATOR-CONTACT-SLOT",
    citation="RecoveryOS batch allocator :: per-customer contact quota",
    description=(
        "Two live cases for the same customer cannot both spend the one contact "
        "slot the policy leaves. The higher-value case takes it; the other waits."
    ),
)

ALLOCATOR_BUDGET = Rule(
    rule_id="ALLOCATOR-BUDGET",
    citation="merchant_policy.json :: intervention_budget_paise",
    description="The sweep has a spend budget; interventions past it wait for the next sweep.",
)

ALLOCATOR_HUMAN_CAPACITY = Rule(
    rule_id="ALLOCATOR-HUMAN-CAPACITY",
    citation="merchant_policy.json :: human_review_capacity",
    description="There are a finite number of collections owners. Escalations queue.",
)

ALL_RULES: dict[str, Rule] = {
    r.rule_id: r
    for r in (
        RBI_PRE_DEBIT_NOTICE,
        RBI_AFA_CEILING,
        TRAI_QUIET_HOURS,
        TRAI_DLT_CONSENT,
        CONSUMER_OPT_OUT,
        MERCHANT_ALLOWED_ACTIONS,
        MERCHANT_RETRY_CAP,
        MERCHANT_RETRY_GAP,
        MERCHANT_CONTACT_CAP_24H,
        MERCHANT_CONTACT_CAP_7D,
        MERCHANT_APPROVAL_THRESHOLD,
        INSTRUMENT_UNRECOVERABLE,
        COMPLIANCE_BLOCKED_RETRY,
        PROMISE_ACTIVE,
        ALLOCATOR_CONTACT_SLOT,
        ALLOCATOR_BUDGET,
        ALLOCATOR_HUMAN_CAPACITY,
    )
}

# RBI AFA ceilings, in paise.
AFA_CEILING_PAISE = 1_500_000            # INR 15,000
AFA_CEILING_EXEMPT_PAISE = 10_000_000    # INR 1,00,000
