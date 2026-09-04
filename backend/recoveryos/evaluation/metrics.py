"""Measurement.

The headline number is **incremental** recovered revenue, not gross. Gross
recovery is the number every dunning tool reports and it is not a measurement
of anything: it counts customers who would have paid anyway. Here that is not a
philosophical objection, it is arithmetic we can actually do, because a random
slice of the book is held out and never touched.

    incremental = treated_recovered - treated_at_risk x holdout_recovery_rate

Two things this file is careful about:

* Everything is labelled simulated. These are controlled-simulation results
  from fabricated cases, not evidence about real merchant performance.
* The holdout arm is small, so the incremental estimate carries real sampling
  error. `sensitivity.py` re-runs across seeds so the spread is visible rather
  than hidden behind one confident-looking number.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from ..schemas import ActionType, CaseState, Decision, OutcomeKind, PolicyDecision


@dataclass
class Metrics:
    policy: str
    description: str = ""
    seed: int = 0

    cases: int = 0
    revenue_at_risk_paise: int = 0

    # Gross -- what a conventional dashboard would show.
    recovered_paise: int = 0
    cases_recovered: int = 0

    # Control arm. The per-case pairs are kept so the counterfactual can carry a
    # confidence interval instead of pretending to a precision it does not have.
    holdout_cases: int = 0
    holdout_at_risk_paise: int = 0
    holdout_recovered_paise: int = 0
    holdout_samples: list[tuple[int, int]] = field(default_factory=list)

    # Treated arm.
    treated_cases: int = 0
    treated_at_risk_paise: int = 0
    treated_recovered_paise: int = 0
    # Exact counterfactual. Knowable only because this is a simulation: treated
    # exposure that the latent draws say would have arrived untouched.
    treated_selfcure_paise: int = 0
    treated_selfcure_cases: int = 0

    # Effort.
    interventions: int = 0
    customer_contacts: int = 0
    retries: int = 0
    human_escalations: int = 0
    spend_paise: int = 0

    # Waste and safety.
    interventions_on_self_curers: int = 0
    guardrail_violations: int = 0
    violation_rules: dict[str, int] = field(default_factory=dict)
    customers_opted_out: int = 0

    # Endings.
    stopped_cases: int = 0
    stop_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def holdout_value_rate(self) -> float:
        if not self.holdout_at_risk_paise:
            return 0.0
        return self.holdout_recovered_paise / self.holdout_at_risk_paise

    @property
    def counterfactual_paise(self) -> int:
        """What the treated arm would have recovered untouched, estimated from the control."""
        return int(self.treated_at_risk_paise * self.holdout_value_rate)

    @property
    def incremental_paise(self) -> int:
        """The headline. Recovery this policy actually caused."""
        return self.treated_recovered_paise - self.counterfactual_paise

    @property
    def true_counterfactual_paise(self) -> int:
        """What the treated arm would have recovered untouched -- exactly."""
        return self.treated_selfcure_paise

    @property
    def true_incremental_paise(self) -> int:
        """Recovery this policy actually caused, with no sampling error.

        Computable only in simulation, and reported *beside* the holdout estimate
        rather than instead of it. The holdout is what a real deployment could
        measure; showing both is how you demonstrate the estimator works and
        where it is too noisy to trust.
        """
        return self.treated_recovered_paise - self.treated_selfcure_paise

    @property
    def true_incremental_per_contact_paise(self) -> int:
        if not self.customer_contacts:
            return 0
        return int(self.true_incremental_paise / self.customer_contacts)

    @property
    def value_recovery_rate(self) -> float:
        return self.recovered_paise / self.revenue_at_risk_paise if self.revenue_at_risk_paise else 0.0

    @property
    def case_recovery_rate(self) -> float:
        return self.cases_recovered / self.cases if self.cases else 0.0

    @property
    def incremental_per_intervention_paise(self) -> int:
        return int(self.incremental_paise / self.interventions) if self.interventions else 0

    @property
    def incremental_per_contact_paise(self) -> int:
        return int(self.incremental_paise / self.customer_contacts) if self.customer_contacts else 0

    @property
    def actions_per_case(self) -> float:
        return self.interventions / self.treated_cases if self.treated_cases else 0.0

    @property
    def return_on_spend(self) -> float:
        return self.incremental_paise / self.spend_paise if self.spend_paise else 0.0

    def incremental_interval(self, n_boot: int = 2000, seed: int = 7) -> tuple[int, int]:
        """Bootstrap interval on incremental recovery.

        The counterfactual is a value-weighted rate estimated from a handful of
        held-out cases with very unequal amounts. One large self-cure moves it a
        long way. Resampling the control arm makes that uncertainty visible
        instead of burying it under a single confident number.
        """
        import random as _random

        if not self.holdout_samples:
            return (self.incremental_paise, self.incremental_paise)
        rng = _random.Random(seed)
        n = len(self.holdout_samples)
        draws: list[int] = []
        for _ in range(n_boot):
            sample = [self.holdout_samples[rng.randrange(n)] for _ in range(n)]
            at_risk = sum(a for a, _ in sample)
            recovered = sum(r for _, r in sample)
            rate = recovered / at_risk if at_risk else 0.0
            draws.append(self.treated_recovered_paise - int(self.treated_at_risk_paise * rate))
        draws.sort()
        return (draws[int(0.025 * n_boot)], draws[int(0.975 * n_boot) - 1])

    @property
    def counterfactual_is_precise(self) -> bool:
        """Is the control arm big enough to say anything with a straight face?"""
        recovered_cases = sum(1 for _, r in self.holdout_samples if r)
        return len(self.holdout_samples) >= 20 and recovered_cases >= 3

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            holdout_value_rate=round(self.holdout_value_rate, 4),
            counterfactual_paise=self.counterfactual_paise,
            incremental_paise=self.incremental_paise,
            value_recovery_rate=round(self.value_recovery_rate, 4),
            case_recovery_rate=round(self.case_recovery_rate, 4),
            incremental_per_intervention_paise=self.incremental_per_intervention_paise,
            incremental_per_contact_paise=self.incremental_per_contact_paise,
            actions_per_case=round(self.actions_per_case, 2),
            return_on_spend=round(self.return_on_spend, 2),
            incremental_interval_paise=list(self.incremental_interval()),
            counterfactual_is_precise=self.counterfactual_is_precise,
            true_counterfactual_paise=self.true_counterfactual_paise,
            true_incremental_paise=self.true_incremental_paise,
            true_incremental_per_contact_paise=self.true_incremental_per_contact_paise,
            basis="Controlled simulation on synthetic cases. Not real merchant data.",
        )
        return d


def compute(
    conn,
    provider,
    decisions: list[Decision],
    policy_name: str,
    description: str = "",
    action_costs: Optional[dict[str, int]] = None,
) -> Metrics:
    from ..db import load_cases

    costs = action_costs or {}
    m = Metrics(policy=policy_name, description=description, seed=provider.seed)
    cases = load_cases(conn)
    m.cases = len(cases)

    for c in cases:
        m.revenue_at_risk_paise += c.amount_paise
        if c.state is CaseState.RECOVERED:
            m.cases_recovered += 1
            m.recovered_paise += c.recovered_paise
        if c.is_holdout:
            m.holdout_cases += 1
            m.holdout_at_risk_paise += c.amount_paise
            m.holdout_recovered_paise += c.recovered_paise
            m.holdout_samples.append((c.amount_paise, c.recovered_paise))
        else:
            m.treated_cases += 1
            m.treated_at_risk_paise += c.amount_paise
            m.treated_recovered_paise += c.recovered_paise
            if provider.self_cures(c.case_id):
                m.treated_selfcure_paise += c.amount_paise
                m.treated_selfcure_cases += 1
        if c.state is CaseState.STOPPED:
            m.stopped_cases += 1
        if c.stop_reason:
            m.stop_reasons[c.stop_reason.value] = m.stop_reasons.get(c.stop_reason.value, 0) + 1

    acted_on: set[str] = set()
    for d in decisions:
        if d.chosen is None or d.execution is None:
            continue
        executed = d.execution.outcome is not OutcomeKind.NOT_EXECUTED
        if d.chosen.action is ActionType.NO_ACTION or not executed:
            continue

        m.interventions += 1
        acted_on.add(d.case_id)
        m.spend_paise += costs.get(d.chosen.action.value, 0)
        if d.chosen.action is ActionType.DELAYED_RETRY:
            m.retries += 1
        if d.chosen.action is ActionType.HUMAN_ESCALATION:
            m.human_escalations += 1
        if d.execution.contact_made:
            m.customer_contacts += 1
        if d.execution.outcome is OutcomeKind.OPTED_OUT:
            m.customers_opted_out += 1

        # A policy that executes an action the guardrail engine refused has
        # committed a violation. Counting it is only possible because every
        # policy is evaluated against the same rules, whether or not it obeys them.
        if d.policy.decision is not PolicyDecision.PASS:
            m.guardrail_violations += 1
            for v in d.policy.blocking:
                if v.decision is PolicyDecision.BLOCK:
                    m.violation_rules[v.rule_id] = m.violation_rules.get(v.rule_id, 0) + 1

    # Effort spent on customers who were going to pay regardless. Visible only
    # because this is a simulation -- and precisely the thing no real dashboard
    # can show you.
    m.interventions_on_self_curers = sum(1 for cid in acted_on if provider.self_cures(cid))
    return m
