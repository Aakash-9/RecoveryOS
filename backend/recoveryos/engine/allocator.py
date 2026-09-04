"""Batch allocation: recovery as a portfolio, not a pile of independent cases.

Deciding each case alone is how recovery systems end up over-contacting. Three
things are scarce across a batch and invisible from inside a single case:

1. **Money.** The merchant has a spend budget for the sweep.
2. **People.** There are only so many collections owners.
3. **A customer's attention.** Someone with a failed subscription *and* an
   overdue invoice has one inbox. Their cases have to compete for the one
   contact slot the policy and TRAI leave available -- and the smaller case has
   to lose. No amount of per-case reasoning can see that conflict.

Greedy by utility density (utility per rupee spent), which is the standard
fractional-knapsack ordering and, more usefully, the ordering an operator can
defend in a sentence: spend the next rupee where it buys the most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..policy.guardrails import CONTACT_ACTIONS, MerchantPolicy
from ..schemas import ActionType, CandidateAction, ScoredAction


@dataclass(frozen=True)
class Proposal:
    case_id: str
    customer_id: str
    action: CandidateAction
    scored: ScoredAction
    contacts_7d: int
    amount_paise: int

    @property
    def is_contact(self) -> bool:
        return self.action.action in CONTACT_ACTIONS

    @property
    def is_escalation(self) -> bool:
        return self.action.action is ActionType.HUMAN_ESCALATION

    @property
    def density(self) -> float:
        return self.scored.utility_paise / max(1, self.scored.cost_paise)


@dataclass
class Grant:
    case_id: str
    granted: bool
    reason: str = ""
    displaced_by: Optional[str] = None


@dataclass
class Allocation:
    grants: dict[str, Grant] = field(default_factory=dict)
    spent_paise: int = 0
    escalations_used: int = 0
    contacts_granted: dict[str, int] = field(default_factory=dict)

    def allows(self, case_id: str) -> bool:
        g = self.grants.get(case_id)
        return g is None or g.granted


def allocate(proposals: list[Proposal], policy: MerchantPolicy) -> Allocation:
    """Decide which proposed interventions the batch can actually afford."""
    alloc = Allocation()
    ordered = sorted(proposals, key=lambda p: (-p.density, -p.scored.utility_paise, p.case_id))
    # Highest-value case per customer, so a denial can name what outbid it.
    best_by_customer: dict[str, Proposal] = {}
    for p in ordered:
        if p.is_contact and p.customer_id not in best_by_customer:
            best_by_customer[p.customer_id] = p

    for p in ordered:
        if p.action.action is ActionType.NO_ACTION:
            alloc.grants[p.case_id] = Grant(p.case_id, True)
            continue

        if alloc.spent_paise + p.scored.cost_paise > policy.intervention_budget_paise:
            alloc.grants[p.case_id] = Grant(
                p.case_id, False,
                f"batch intervention budget of INR {policy.intervention_budget_paise / 100:,.0f} exhausted",
            )
            continue

        if p.is_escalation and alloc.escalations_used >= policy.human_review_capacity:
            alloc.grants[p.case_id] = Grant(
                p.case_id, False,
                f"human review capacity for this sweep ({policy.human_review_capacity}) is fully committed",
            )
            continue

        if p.is_contact:
            used = alloc.contacts_granted.get(p.customer_id, 0)
            remaining = policy.max_contacts_per_7d - p.contacts_7d - used
            if remaining <= 0:
                winner = best_by_customer.get(p.customer_id)
                alloc.grants[p.case_id] = Grant(
                    p.case_id, False,
                    (
                        "the customer has no contact slot left this week; it went to a "
                        f"higher-value case for the same customer"
                        if winner is not None and winner.case_id != p.case_id
                        else "the customer has no contact slot left this week"
                    ),
                    displaced_by=winner.case_id if winner and winner.case_id != p.case_id else None,
                )
                continue
            alloc.contacts_granted[p.customer_id] = used + 1

        if p.is_escalation:
            alloc.escalations_used += 1
        alloc.spent_paise += p.scored.cost_paise
        alloc.grants[p.case_id] = Grant(p.case_id, True)

    return alloc
