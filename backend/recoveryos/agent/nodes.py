"""The agent, as six pure-ish node functions.

Each node takes the run state and returns the fields it changed. They are
ordinary functions: `graph.py` wires them into a LangGraph state machine, and
the same functions run under a plain driver if LangGraph is unavailable. The
decision logic lives here so that it is testable without either.

Business state is persisted to SQLite at every transition. Nothing important
lives in an LLM conversation, and nothing important lives only in memory.

The loop
--------
    LOAD -> DIAGNOSE -> SCORE -> GATE -> EXECUTE -> OBSERVE -+-> done
                          ^                                  |
                          +----------------------------------+

`OBSERVE` is what makes this an agent rather than a classifier: a failed retry
comes back through `DIAGNOSE` with a changed world -- one more attempt spent,
the clock moved, possibly a promise on record -- and gets re-decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional, TypedDict

from ..audit import ledger
from ..db import (
    build_context,
    insert_attempt,
    insert_contact,
    insert_promise,
    load_case,
    load_promise,
    update_case,
)
from ..engine.promises import (
    clamp_promise_date,
    default_promise_date,
    parse_promise_text,
)
from ..engine.scoring import diagnose, recovery_score, score_all
from ..policy.guardrails import MerchantPolicy, defer_hours, evaluate, load_policy
from ..schemas import (
    ActionType,
    CandidateAction,
    CaseState,
    Channel,
    Decision,
    ExecutionResult,
    OutcomeKind,
    PolicyDecision,
    PolicyVerdict,
    Promise,
    PromiseState,
    RuleVerdict,
    ScoredAction,
    StopReason,
    rupees,
)
from ..simulator.provider import MockPaymentProvider

# How long the merchant is willing to keep chasing one case.
RECOVERY_HORIZON_DAYS = 14
# How long to wait for a customer to react to a contact before re-deciding.
CONTACT_OBSERVATION_HOURS = 24
# A deferred action must be worth this much more than the best action available
# right now before the agent chooses to wait for it.
DEFER_PREFERENCE_RATIO = 1.2

# Which guardrail, having killed the best action, explains why the case stopped.
STOP_REASON_BY_RULE = {
    "MERCHANT-RETRY-CAP": StopReason.RETRY_CAP_REACHED,
    "MERCHANT-CONTACT-CAP-24H": StopReason.CONTACT_CAP_REACHED,
    "MERCHANT-CONTACT-CAP-7D": StopReason.CONTACT_CAP_REACHED,
    "POLICY-INSTRUMENT-INVALID": StopReason.INSTRUMENT_UNRECOVERABLE,
    "POLICY-COMPLIANCE-BLOCKED": StopReason.INSTRUMENT_UNRECOVERABLE,
    "CONSUMER-OPT-OUT": StopReason.CUSTOMER_OPTED_OUT,
    "TRAI-DLT-CONSENT": StopReason.CUSTOMER_OPTED_OUT,
    "MERCHANT-APPROVAL-THRESHOLD": StopReason.APPROVAL_REQUIRED,
    "MERCHANT-ALLOWED-ACTIONS": StopReason.NO_VALID_ACTIONS,
}


class RunState(TypedDict, total=False):
    case_id: str
    run_id: str
    iteration: int
    now: Any                      # datetime
    started_at: Any               # datetime, for the horizon check
    ctx: Any                      # CaseContext
    diagnosis: str
    retryability: Any
    p_self_cure: float
    scored: list[ScoredAction]
    gated: list[tuple[ScoredAction, PolicyVerdict]]
    chosen: Optional[CandidateAction]
    chosen_score: Optional[ScoredAction]
    chosen_verdict: Optional[PolicyVerdict]
    execution: Optional[ExecutionResult]
    state_after: Any
    stop_reason: Optional[StopReason]
    wait_hours: int
    done: bool
    decisions: list[Decision]


@dataclass
class AgentDeps:
    """Everything the nodes need from the outside world."""

    conn: Any
    provider: MockPaymentProvider
    policy: MerchantPolicy = field(default_factory=load_policy)
    narrator: Any = None          # optional LLM; never load-bearing
    record_audit: bool = True
    # How this run picks among the gated candidates. Swapped out to run the
    # naive and rule-based baselines through the identical machinery.
    chooser: Any = None


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


def load(deps: AgentDeps, state: RunState) -> RunState:
    case = load_case(deps.conn, state["case_id"])
    ctx = build_context(deps.conn, state["case_id"], state["now"])

    if case.is_holdout:
        # The silent control arm. Never touched, so that the treated arm has
        # something honest to be compared against.
        result = deps.provider.settle_untouched(ctx)
        return _terminate(
            deps, state, ctx, result, StopReason.HELD_OUT,
            note="held out of all intervention as the randomised control arm",
        )

    if case.state in (CaseState.RECOVERED, CaseState.STOPPED, CaseState.ESCALATED):
        return {**state, "ctx": ctx, "done": True}

    elapsed = state["now"] - state["started_at"]
    if state["iteration"] >= deps.policy.max_iterations_per_case or elapsed > timedelta(days=RECOVERY_HORIZON_DAYS):
        result = deps.provider.settle_untouched(ctx)
        return _terminate(deps, state, ctx, result, StopReason.MAX_ITERATIONS,
                          note="recovery horizon reached")

    return {**state, "ctx": ctx, "done": False}


def diagnose_node(deps: AgentDeps, state: RunState) -> RunState:
    ctx = state["ctx"]
    text, retryability = diagnose(ctx)

    # A promise that has come due resolves before anything else is considered.
    promise = load_promise(deps.conn, ctx.case.case_id)
    if promise.state is PromiseState.ACTIVE and promise.promised_for and state["now"] >= promise.promised_for:
        kept = deps.provider.self_cures(ctx.case.case_id)
        insert_promise(
            deps.conn,
            promise.model_copy(update={"state": PromiseState.FULFILLED if kept else PromiseState.BROKEN}),
        )
        ctx = build_context(deps.conn, ctx.case.case_id, state["now"])
        text = (
            "Customer honoured their promise to pay." if kept
            else "The promised payment date passed with nothing received. The promise is broken "
                 "and no longer protects this case from further action."
        )
        if kept:
            return _terminate(
                deps, state,
                ctx,
                ExecutionResult(
                    outcome=OutcomeKind.RECOVERED,
                    recovered_paise=ctx.case.amount_paise,
                    detail="Promise to pay honoured on the agreed date.",
                ),
                None,
                note=text,
            )

    return {**state, "ctx": ctx, "diagnosis": text, "retryability": retryability}


def score_node(deps: AgentDeps, state: RunState) -> RunState:
    scored = score_all(state["ctx"], deps.policy)
    return {**state, "scored": scored, "p_self_cure": scored[0].p_self_cure}


@dataclass(frozen=True)
class Choice:
    chosen: CandidateAction
    scored: ScoredAction
    verdict: PolicyVerdict
    stop_reason: Optional[StopReason] = None
    wait_hours: int = 0


def gate(deps: AgentDeps, state: RunState) -> RunState:
    """Evaluate every candidate against every rule, then let the policy choose.

    The verdicts are produced identically for all policies -- what differs is
    whether a policy *honours* them. That is what makes "guardrail violations"
    a measurable quantity in the evaluation rather than an assertion.
    """
    ctx = state["ctx"]
    gated = [(s, evaluate(ctx, s.candidate, deps.policy)) for s in state["scored"]]
    choice = (deps.chooser or choose_recoveryos)(gated, ctx, deps.policy)
    return {
        **state,
        "gated": gated,
        "chosen": choice.chosen,
        "chosen_score": choice.scored,
        "chosen_verdict": choice.verdict,
        "stop_reason": choice.stop_reason,
        "wait_hours": choice.wait_hours,
    }


def choose_recoveryos(
    gated: list[tuple[ScoredAction, PolicyVerdict]], ctx, policy: MerchantPolicy
) -> Choice:
    """Score first, then gate, then explain which gate killed the best option."""
    no_action = next(g for g in gated if g[0].candidate.action is ActionType.NO_ACTION)
    interventions = [g for g in gated if g[0].candidate.action is not ActionType.NO_ACTION]
    best_overall = interventions[0] if interventions else no_action

    # An action worth taking must pass every rule and beat doing nothing. The
    # merchant floor is a floor on *someone's time*, so it applies to human
    # escalation; a sub-rupee automated message only has to be worth more than
    # it costs.
    def worth_it(g) -> bool:
        floor = policy.min_utility_paise if g[0].candidate.action is ActionType.HUMAN_ESCALATION else 1
        return g[0].utility_paise >= floor

    viable = [g for g in interventions if g[1].decision is PolicyDecision.PASS and worth_it(g)]
    deferrable = [g for g in interventions if g[1].decision is PolicyDecision.DEFER and worth_it(g)]

    # A person is what you reach for when automation has run out of moves, not
    # an option competing on utility. Two reasons, and both are real:
    # escalation ends the case, forfeiting every cheaper attempt that could
    # still have worked; and a collections team is the one resource in this
    # system that cannot be scaled by writing more code.
    automated = [g for g in viable if g[0].candidate.action is not ActionType.HUMAN_ESCALATION]
    if automated:
        viable = automated

    # High-value exposure: a human decides, and is told exactly what was proposed.
    if best_overall[1].decision is PolicyDecision.REQUIRE_APPROVAL:
        escalation = next(
            (g for g in interventions
             if g[0].candidate.action is ActionType.HUMAN_ESCALATION
             and g[1].decision is PolicyDecision.PASS),
            None,
        )
        if escalation is not None:
            return Choice(escalation[0].candidate, escalation[0], escalation[1],
                          StopReason.APPROVAL_REQUIRED)

    best_now = viable[0] if viable else None
    best_later = deferrable[0] if deferrable else None

    # Is it worth waiting? Only if the deferred action is materially better than
    # anything lawful right now.
    if best_later is not None:
        threshold = best_now[0].utility_paise * DEFER_PREFERENCE_RATIO if best_now else 0
        if best_later[0].utility_paise > threshold:
            return Choice(best_later[0].candidate, best_later[0], best_later[1],
                          None, max(1, defer_hours(best_later[1])))

    if best_now is not None:
        return Choice(best_now[0].candidate, best_now[0], best_now[1])

    # Nothing is worth doing. Say why, using the rule that killed the best option.
    reason = StopReason.NO_POSITIVE_UTILITY
    blocking: list[RuleVerdict] = best_overall[1].blocking
    if blocking:
        reason = STOP_REASON_BY_RULE.get(blocking[0].rule_id, StopReason.NO_VALID_ACTIONS)
    if any(v.rule_id == "POLICY-PROMISE-ACTIVE" for v in blocking):
        reason = None       # not a stop: a live promise means wait and watch
    return Choice(no_action[0].candidate, no_action[0], no_action[1], reason)


def execute(deps: AgentDeps, state: RunState) -> RunState:
    ctx, action = state["ctx"], state["chosen"]
    now = state["now"]

    if state["wait_hours"]:
        # Deferred, not abandoned. Nothing is executed and nothing is charged.
        return {**state, "execution": ExecutionResult(
            outcome=OutcomeKind.NOT_EXECUTED,
            detail=(
                f"Deferred {state['wait_hours']}h: "
                + "; ".join(v.message for v in state["chosen_verdict"].blocking)
            ),
        )}

    if action.action is ActionType.NO_ACTION:
        # Only settle the case if the loop is finished with it. A live promise
        # means wait, not close.
        if state["stop_reason"] is None:
            return {**state, "execution": ExecutionResult(
                outcome=OutcomeKind.NOT_EXECUTED,
                detail="Monitoring an active promise to pay; no contact and no debit.",
            )}
        return {**state, "execution": deps.provider.settle_untouched(ctx)}

    body = ""
    if action.action is ActionType.CUSTOMER_MESSAGE and deps.narrator is not None:
        body = deps.narrator.draft_message(ctx, state["diagnosis"])

    at = now + timedelta(hours=action.delay_hours)
    result = deps.provider.execute(ctx, action, at, state["iteration"], body)

    if action.action is ActionType.DELAYED_RETRY:
        insert_attempt(deps.conn, ctx.case.case_id, at, result.outcome.value, state["run_id"])
    if result.contact_made:
        insert_contact(
            deps.conn, ctx.customer.customer_id, ctx.case.case_id, at,
            action.channel or Channel.WHATSAPP, action.action.value, state["run_id"],
        )
    return {**state, "execution": result}


def observe(deps: AgentDeps, state: RunState) -> RunState:
    """Fold the outcome back into persisted state and decide whether to continue."""
    ctx, action, result = state["ctx"], state["chosen"], state["execution"]
    case = ctx.case
    now = state["now"]
    stop_reason = state["stop_reason"]

    attempts = case.attempts_made + (1 if action.action is ActionType.DELAYED_RETRY and not state["wait_hours"] else 0)
    last_attempt = now + timedelta(hours=action.delay_hours) if attempts != case.attempts_made else case.last_attempt_at

    if result.outcome is OutcomeKind.RECOVERED:
        new_state, stop_reason = CaseState.RECOVERED, None
    elif result.outcome is OutcomeKind.TRANSFERRED:
        new_state, stop_reason = CaseState.ESCALATED, StopReason.APPROVAL_REQUIRED
    elif result.outcome is OutcomeKind.OPTED_OUT:
        new_state, stop_reason = CaseState.STOPPED, StopReason.CUSTOMER_OPTED_OUT
    elif action.action is ActionType.HUMAN_ESCALATION:
        new_state = CaseState.ESCALATED
    elif state["wait_hours"] or (action.action is ActionType.NO_ACTION and stop_reason is None):
        new_state = CaseState.WAITING
    elif action.action is ActionType.NO_ACTION:
        new_state = CaseState.STOPPED
    else:
        new_state = CaseState.OPEN      # keep working the case

    # A customer who answered with a commitment gets one recorded, and the
    # guardrail engine will then protect them from being chased again.
    if result.outcome is OutcomeKind.PROMISE_MADE and result.promise is not None:
        promise = _extract_promise(deps, ctx, result.promise.source_text or "", now)
        insert_promise(deps.conn, promise)
        new_state = CaseState.WAITING

    advance = _advance_hours(deps, state, action, result)
    updated = case.model_copy(update={
        "state": new_state,
        "stop_reason": stop_reason,
        "recovered_paise": result.recovered_paise,
        "attempts_made": attempts,
        "last_attempt_at": last_attempt,
        "next_action_at": None if new_state in (CaseState.RECOVERED, CaseState.STOPPED, CaseState.ESCALATED)
        else now + timedelta(hours=advance),
    })
    update_case(deps.conn, updated)

    decision = Decision(
        case_id=case.case_id,
        iteration=state["iteration"],
        at=now,
        state_before=case.state,
        diagnosis=state["diagnosis"],
        retryability=state["retryability"],
        p_self_cure=state["chosen_score"].p_self_cure,
        scored=state["scored"],
        chosen=action,
        policy=state["chosen_verdict"],
        execution=result,
        state_after=new_state,
        stop_reason=stop_reason,
        narrative=_narrate(deps, state, new_state),
    )
    _write_audit(deps, state, decision)

    done = new_state in (CaseState.RECOVERED, CaseState.STOPPED, CaseState.ESCALATED)
    return {
        **state,
        "state_after": new_state,
        "stop_reason": stop_reason,
        "now": now + timedelta(hours=advance),
        "iteration": state["iteration"] + 1,
        "decisions": [*state.get("decisions", []), decision],
        "done": done,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _advance_hours(deps: AgentDeps, state: RunState, action, result) -> int:
    """How long this action takes to play out before the case can be re-decided."""
    if state["wait_hours"]:
        return state["wait_hours"]
    if action.action is ActionType.NO_ACTION and state["stop_reason"] is None:
        # Waiting on a live promise. Sleep until it comes due rather than
        # re-deciding the same case every round: spinning here burns the
        # iteration budget and lands the case on MAX_ITERATIONS before the
        # customer's own deadline has even arrived.
        promise = load_promise(deps.conn, state["ctx"].case.case_id)
        if promise.state is PromiseState.ACTIVE and promise.promised_for:
            hours = (promise.promised_for - state["now"]).total_seconds() // 3600
            return max(1, int(hours) + 1)
        return 0
    if action.action is ActionType.DELAYED_RETRY:
        return action.delay_hours or 1
    if result.outcome is OutcomeKind.PROMISE_MADE:
        promise = load_promise(deps.conn, state["ctx"].case.case_id)
        if promise.promised_for:
            return max(1, int((promise.promised_for - state["now"]).total_seconds() // 3600))
        return 72
    if action.action in (ActionType.CUSTOMER_MESSAGE, ActionType.PAYMENT_LINK):
        return CONTACT_OBSERVATION_HOURS
    return 0


def _terminate(
    deps: AgentDeps,
    state: RunState,
    ctx,
    result: ExecutionResult,
    stop_reason: Optional[StopReason],
    note: str = "",
) -> RunState:
    new_state = CaseState.RECOVERED if result.outcome is OutcomeKind.RECOVERED else CaseState.STOPPED
    updated = ctx.case.model_copy(update={
        "state": new_state, "stop_reason": stop_reason,
        "recovered_paise": result.recovered_paise, "next_action_at": None,
    })
    update_case(deps.conn, updated)

    no_action = CandidateAction(action=ActionType.NO_ACTION, rationale=note)
    decision = Decision(
        case_id=ctx.case.case_id,
        iteration=state["iteration"],
        at=state["now"],
        state_before=ctx.case.state,
        diagnosis=note or "Case closed without intervention.",
        retryability=ctx.retryability,
        p_self_cure=0.0,
        scored=[],
        chosen=no_action,
        policy=PolicyVerdict(decision=PolicyDecision.PASS),
        execution=result,
        state_after=new_state,
        stop_reason=stop_reason,
    )
    _write_audit(deps, state, decision)
    return {**state, "ctx": ctx, "execution": result, "state_after": new_state,
            "stop_reason": stop_reason, "chosen": no_action, "chosen_score": None,
            "decisions": [*state.get("decisions", []), decision], "done": True}


def _extract_promise(deps: AgentDeps, ctx, text: str, now) -> Promise:
    """Structured promise from free text, in strict order of trust.

    A deterministic parser goes first. The language model is a fallback for
    text the parser cannot read, and even then its answer is clamped into the
    policy window before it reaches the state machine -- because a promise date
    decides when a real customer is next contacted, and that is not a decision
    a model gets to make unsupervised.
    """
    parsed = parse_promise_text(text, now)
    source = "deterministic parser"

    if parsed is None and deps.narrator is not None:
        try:
            candidate = deps.narrator.extract_promise(text, now)
        except Exception:
            candidate = None
        if candidate is not None:
            when = clamp_promise_date(candidate.get("promised_for"), now)
            if when is not None:
                parsed = {
                    "promised_for": when,
                    "amount_paise": candidate.get("amount_paise"),
                    "confidence": min(1.0, max(0.0, float(candidate.get("confidence") or 0.4))),
                }
                source = "language model, clamped to the policy window"

    if parsed is None:
        parsed = {
            "promised_for": default_promise_date(now),
            "amount_paise": None,
            "confidence": 0.3,
        }
        source = "conservative default"

    when = clamp_promise_date(parsed["promised_for"], now) or default_promise_date(now)
    amount = parsed.get("amount_paise")
    # An amount the customer never offered, or one larger than the exposure, is
    # not usable. Fall back to what is actually owed.
    if not amount or amount <= 0 or amount > ctx.case.amount_paise:
        amount = ctx.case.amount_paise

    return Promise(
        case_id=ctx.case.case_id,
        state=PromiseState.ACTIVE,
        promised_at=now,
        promised_for=when,
        promised_amount_paise=amount,
        confidence=round(float(parsed.get("confidence", 0.3)), 2),
        source_text=f"{text}  [{source}]",
    )


def _narrate(deps: AgentDeps, state: RunState, new_state: CaseState) -> Optional[str]:
    if deps.narrator is None:
        return None
    try:
        return deps.narrator.explain_decision(
            state["ctx"], state["diagnosis"], state["chosen_score"], state["chosen_verdict"], new_state
        )
    except Exception:      # narration is decoration; it may never break a run
        return None


def _write_audit(deps: AgentDeps, state: RunState, decision: Decision) -> None:
    if not deps.record_audit:
        return
    s = decision.chosen_score if hasattr(decision, "chosen_score") else None  # noqa: F841
    payload = {
        "iteration": decision.iteration,
        "state_before": decision.state_before.value,
        "state_after": decision.state_after.value,
        "diagnosis": decision.diagnosis,
        "retryability": decision.retryability.value,
        "p_self_cure": decision.p_self_cure,
        "chosen_action": decision.chosen.label if decision.chosen else None,
        "candidates": [
            {
                "action": sc.candidate.label,
                "uplift": sc.uplift,
                "expected_incremental_paise": sc.expected_incremental_paise,
                "cost_paise": sc.cost_paise,
                "fatigue_paise": sc.fatigue_penalty_paise,
                "risk_paise": sc.risk_penalty_paise,
                "utility_paise": sc.utility_paise,
            }
            for sc in decision.scored
        ],
        "policy_decision": decision.policy.decision.value,
        "policy_rules": [
            {"rule_id": v.rule_id, "decision": v.decision.value, "citation": v.citation, "message": v.message}
            for v in decision.policy.verdicts
        ],
        "outcome": decision.execution.outcome.value if decision.execution else None,
        "recovered_paise": decision.execution.recovered_paise if decision.execution else 0,
        "detail": decision.execution.detail if decision.execution else "",
        "stop_reason": decision.stop_reason.value if decision.stop_reason else None,
        "narrative": decision.narrative,
    }
    ledger.append(deps.conn, state["run_id"], decision.case_id, decision.at, payload)


def summarise(decision: Decision) -> str:
    """One-line human summary. Used by the CLI and the activity feed."""
    money = f"INR {rupees(decision.execution.recovered_paise)}" if decision.execution else ""
    bits = [decision.chosen.label if decision.chosen else "-", decision.state_after.value]
    if decision.stop_reason:
        bits.append(decision.stop_reason.value)
    if decision.execution and decision.execution.recovered_paise:
        bits.append(f"recovered {money}")
    return " | ".join(bits)
