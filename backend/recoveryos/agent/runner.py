"""Batch runner: sweeps, not one-shots.

Real recovery happens as repeated sweeps over a book of cases, not as a single
pass. Each round here is one operating day:

    plan every eligible case  ->  allocate the scarce resources  ->  execute
    ->  observe  ->  advance the clock  ->  repeat

Planning before allocating is the whole point. A case cannot know, on its own,
that the customer it wants to message is the same person another case is about
to message. The allocator can, because it sees the round.

Cases park themselves on `next_action_at`, so a retry scheduled for +72h simply
does not appear in tomorrow's sweep.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from ..db import build_context, load_cases, update_case
from ..engine.allocator import Proposal, allocate
from ..policy import rules as R
from ..policy.guardrails import MerchantPolicy, load_policy
from ..schemas import (
    ActionType,
    CaseState,
    Decision,
    PolicyDecision,
    PolicyVerdict,
    RuleVerdict,
    StopReason,
)
from ..simulator.provider import MockPaymentProvider
from .nodes import AgentDeps, RunState, diagnose_node, execute, gate, load, observe, score_node

MAX_ROUNDS = 14
ROUND_HOURS = 24

DENIAL_RULES = {
    "contact": R.ALLOCATOR_CONTACT_SLOT,
    "budget": R.ALLOCATOR_BUDGET,
    "human": R.ALLOCATOR_HUMAN_CAPACITY,
}


@dataclass
class RunResult:
    run_id: str
    policy_name: str
    seed: int
    started_at: datetime
    rounds: int
    decisions: list[Decision] = field(default_factory=list)

    @property
    def case_ids(self) -> set[str]:
        return {d.case_id for d in self.decisions}


def _denial_verdict(reason: str) -> PolicyVerdict:
    if "contact slot" in reason:
        rule = DENIAL_RULES["contact"]
    elif "human review" in reason:
        rule = DENIAL_RULES["human"]
    else:
        rule = DENIAL_RULES["budget"]
    return PolicyVerdict(
        decision=PolicyDecision.DEFER,
        verdicts=[
            RuleVerdict(
                rule_id=rule.rule_id,
                citation=rule.citation,
                decision=PolicyDecision.DEFER,
                message=reason.capitalize() + ".",
                defer_hours=ROUND_HOURS,
            )
        ],
    )


def run_batch(
    conn,
    provider: MockPaymentProvider,
    *,
    chooser: Any = None,
    policy_name: str = "RECOVERYOS",
    merchant_policy: Optional[MerchantPolicy] = None,
    now: datetime,
    run_id: Optional[str] = None,
    narrator: Any = None,
    allocate_batch: bool = True,
    record_audit: bool = True,
    max_rounds: int = MAX_ROUNDS,
) -> RunResult:
    policy = merchant_policy or load_policy()
    run_id = run_id or f"{policy_name.lower()}-{uuid.uuid4().hex[:8]}"
    deps = AgentDeps(
        conn=conn, provider=provider, policy=policy, narrator=narrator,
        record_audit=record_audit, chooser=chooser,
    )

    started = now
    result = RunResult(run_id=run_id, policy_name=policy_name, seed=provider.seed,
                       started_at=started, rounds=0)
    iterations: dict[str, int] = {}

    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, policy_name, started_at, seed) VALUES (?,?,?,?)",
        (run_id, policy_name, started.isoformat(timespec="seconds"), provider.seed),
    )

    for round_no in range(max_rounds):
        active = [
            c for c in load_cases(conn, [CaseState.OPEN.value, CaseState.WAITING.value])
            if c.next_action_at is None or c.next_action_at <= now
        ]
        if not active:
            break
        result.rounds = round_no + 1

        # -- plan ---------------------------------------------------------- #
        plans: list[RunState] = []
        for case in active:
            state: RunState = {
                "case_id": case.case_id, "run_id": run_id,
                "iteration": iterations.get(case.case_id, 0),
                "now": now, "started_at": started, "decisions": [],
                "done": False, "wait_hours": 0, "stop_reason": None,
            }
            state = load(deps, state)
            if state.get("done"):
                result.decisions.extend(state.get("decisions", []))
                continue
            state = diagnose_node(deps, state)
            if state.get("done"):
                result.decisions.extend(state.get("decisions", []))
                continue
            state = score_node(deps, state)
            state = gate(deps, state)
            plans.append(state)

        # -- allocate ------------------------------------------------------ #
        alloc = None
        if allocate_batch:
            proposals = [
                Proposal(
                    case_id=s["case_id"],
                    customer_id=s["ctx"].customer.customer_id,
                    action=s["chosen"],
                    scored=s["chosen_score"],
                    contacts_7d=s["ctx"].contacts_7d,
                    amount_paise=s["ctx"].case.amount_paise,
                )
                for s in plans
                if s["chosen"].action is not ActionType.NO_ACTION and not s["wait_hours"]
            ]
            alloc = allocate(proposals, policy)

        # -- execute ------------------------------------------------------- #
        for state in plans:
            if alloc is not None:
                grant = alloc.grants.get(state["case_id"])
                if grant is not None and not grant.granted:
                    # Denied a scarce resource. Not a refusal, a queue: the case
                    # comes back in the next sweep.
                    state = {
                        **state,
                        "wait_hours": ROUND_HOURS,
                        "stop_reason": None,
                        "chosen_verdict": _denial_verdict(grant.reason),
                    }
            state = execute(deps, state)
            state = observe(deps, state)
            iterations[state["case_id"]] = state["iteration"]
            result.decisions.extend(state.get("decisions", []))

        now += timedelta(hours=ROUND_HOURS)

    # Anything still open at the horizon is resolved untouched. It has to be:
    # the holdout arm is resolved that way, so leaving timed-out treated cases
    # unresolved would quietly bias the comparison in the holdout's favour.
    for case in load_cases(conn, [CaseState.OPEN.value, CaseState.WAITING.value]):
        ctx = build_context(conn, case.case_id, now)
        settled = provider.settle_untouched(ctx)
        update_case(conn, case.model_copy(update={
            "state": CaseState.RECOVERED if settled.recovered_paise else CaseState.STOPPED,
            "stop_reason": None if settled.recovered_paise else (case.stop_reason or StopReason.MAX_ITERATIONS),
            "recovered_paise": settled.recovered_paise,
            "next_action_at": None,
        }))

    conn.execute(
        "UPDATE runs SET finished_at = ? WHERE run_id = ?",
        (now.isoformat(timespec="seconds"), run_id),
    )
    return result
