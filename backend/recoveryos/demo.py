"""Hero scenarios.

Each one is a single question a reviewer would actually ask, answered by
running the real engine against the real simulator -- nothing here is scripted
or pre-recorded. They build their own database so a demo never disturbs the
dashboard state.

    python scripts/demo.py                # list
    python scripts/demo.py arbitration    # run one
    python scripts/demo.py --all
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from . import db as dbmod
from .agent.graph import run_case
from .agent.nodes import AgentDeps, choose_recoveryos
from .agent.runner import run_batch
from .audit import ledger
from .bootstrap import build_database
from .config import ROOT
from .engine.allocator import Proposal, allocate
from .engine.scoring import score_all
from .evaluation.policies import POLICIES
from .llm.client import ActionOpinion, Narrator
from .policy.guardrails import evaluate, load_policy
from .schemas import ActionType, CandidateAction, Channel, PolicyDecision, rupees
from .simulator.provider import MockPaymentProvider, load_truths
from .synthetic import BASE_NOW

DEMO_DB = ROOT / "data" / "demo.db"
SEED = 42


@dataclass
class Step:
    label: str
    detail: str
    kind: str = "info"          # info | decision | block | money | stop
    at: Optional[str] = None


@dataclass
class Scenario:
    title: str
    question: str
    archetype: str
    run: Callable[[], tuple[list[Step], str]]
    steps: list[Step] = field(default_factory=list)


def _world(n_cases: int = 60, seed: int = SEED):
    _, conn = build_database(DEMO_DB, n_cases=n_cases, seed=seed, now=BASE_NOW)
    provider = MockPaymentProvider(load_truths(conn), seed=seed)
    return conn, provider, load_policy()


def _canonical(conn, archetype: str):
    for case in dbmod.load_cases(conn):
        if case.archetype == archetype:
            return case
    raise KeyError(archetype)


def _money(paise: int) -> str:
    return f"INR {rupees(paise)}"


# --------------------------------------------------------------------------- #
# 1. The counterfactual
# --------------------------------------------------------------------------- #


def _scenario_counterfactual() -> tuple[list[Step], str]:
    from .evaluation.harness import evaluate_all
    from .evaluation.oracle import capture_rate

    run = evaluate_all(n_cases=150, seed=SEED, data_dir=ROOT / "data" / "demo_eval",
                       record_audit=False)
    steps: list[Step] = []
    for name, m in run.results.items():
        steps.append(Step(
            label=f"{name}",
            detail=(
                f"gross {_money(m.recovered_paise)} | "
                f"incremental {_money(m.true_incremental_paise)} | "
                f"{m.customer_contacts} contacts | "
                f"{m.guardrail_violations} violations | "
                f"{m.customers_opted_out} customers lost | "
                f"capture {capture_rate(m.recovered_paise, run.oracle):.0%}"
            ),
            kind="money",
        ))
    ros = run.results["RECOVERYOS"]
    compliant = run.results["RULEBOOK+RULES"]
    rulebook = run.results["RULEBOOK"]
    verdict = (
        f"Of the {_money(ros.recovered_paise)} RecoveryOS 'recovered', "
        f"{_money(ros.true_counterfactual_paise)} would have arrived without anyone "
        f"lifting a finger. The number that is actually ours is "
        f"{_money(ros.true_incremental_paise)}.\n"
        f"Against the same dunning ladder run fully compliant, that is "
        f"{ros.true_incremental_paise / max(1, compliant.true_incremental_paise):.2f}x the "
        f"incremental recovery from {ros.customer_contacts} contacts instead of "
        f"{compliant.customer_contacts}.\n"
        f"Only RULEBOOK beats it, and it buys the difference with "
        f"{rulebook.guardrail_violations} guardrail violations and "
        f"{rulebook.customers_opted_out} customers who opted out."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 2. Two cases, one customer, one contact slot
# --------------------------------------------------------------------------- #


def _scenario_arbitration() -> tuple[list[Step], str]:
    conn, provider, policy = _world()
    minor = _canonical(conn, "ARBITRATION_MINOR")
    major = _canonical(conn, "ARBITRATION_MAJOR")
    steps = [Step(
        label="Two live cases, one human",
        detail=(
            f"{minor.case_id} ({_money(minor.amount_paise)}) and "
            f"{major.case_id} ({_money(major.amount_paise)}) both belong to "
            f"customer {minor.customer_id}."
        ),
    )]

    proposals = []
    for case in (minor, major):
        ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)
        best = next(
            s for s in score_all(ctx, policy)
            if s.candidate.action is ActionType.PAYMENT_LINK
        )
        proposals.append(Proposal(
            case_id=case.case_id, customer_id=ctx.customer.customer_id,
            action=best.candidate, scored=best, contacts_7d=ctx.contacts_7d,
            amount_paise=case.amount_paise,
        ))
        steps.append(Step(
            label=f"{case.case_id} proposes {best.candidate.label}",
            detail=(
                f"utility {_money(best.utility_paise)}; "
                f"{ctx.contacts_7d} contact(s) already sent to this customer this week, "
                f"cap is {policy.max_contacts_per_7d}"
            ),
            kind="decision",
        ))

    alloc = allocate(proposals, policy)
    for p in proposals:
        g = alloc.grants[p.case_id]
        steps.append(Step(
            label=f"{p.case_id}: {'granted the slot' if g.granted else 'denied'}",
            detail=g.reason or "within every quota",
            kind="money" if g.granted else "block",
        ))

    loser = next(p for p in proposals if not alloc.grants[p.case_id].granted)
    winner = next(p for p in proposals if alloc.grants[p.case_id].granted)
    conn.close()
    verdict = (
        f"Neither case broke a rule. Both were individually worth doing. They were "
        f"still in conflict, because a customer is one inbox and not one per invoice.\n"
        f"{winner.case_id} takes the slot at {_money(winner.amount_paise)}; "
        f"{loser.case_id} is queued to the next sweep rather than abandoned. "
        f"A system that decides case by case cannot see this conflict at all -- it "
        f"sends both, and the customer receives two chases in a day."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 3. Rules that come from a regulator, not from taste
# --------------------------------------------------------------------------- #


def _scenario_regulator() -> tuple[list[Step], str]:
    conn, provider, policy = _world()
    steps: list[Step] = []

    # RBI: no recurring debit without a 24-hour pre-debit notification.
    case = _canonical(conn, "RBI_NOTICE_MISSING")
    ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)
    retry = CandidateAction(action=ActionType.DELAYED_RETRY, delay_hours=24)
    v = evaluate(ctx, retry, policy)
    steps.append(Step(
        label=f"{case.case_id}: agent proposes re-presenting the mandate",
        detail=f"{_money(case.amount_paise)} recurring debit, mandate {case.mandate_id}",
        kind="decision",
    ))
    for r in v.blocking:
        steps.append(Step(
            label=f"{r.decision.value} - {r.rule_id}",
            detail=f"{r.message}  [{r.citation}]",
            kind="block",
        ))

    # AFA ceiling.
    case = _canonical(conn, "AFA_CEILING_BREACH")
    ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)
    v = evaluate(ctx, retry, policy)
    steps.append(Step(
        label=f"{case.case_id}: {_money(case.amount_paise)} recurring auto-debit",
        detail="above the additional-factor-authentication ceiling, none on record",
        kind="decision",
    ))
    for r in v.blocking:
        steps.append(Step(
            label=f"{r.decision.value} - {r.rule_id}",
            detail=f"{r.message}  [{r.citation}]",
            kind="block",
        ))

    # TRAI: a payment chase is commercial communication.
    case = _canonical(conn, "CART_ABANDONED_HOT")
    late = BASE_NOW.replace(hour=22, minute=40)
    ctx = dbmod.build_context(conn, case.case_id, late)
    message = CandidateAction(action=ActionType.CUSTOMER_MESSAGE, channel=Channel.WHATSAPP)
    v = evaluate(ctx, message, policy)
    steps.append(Step(
        label=f"{case.case_id}: nudge queued at 22:40 IST",
        detail="abandoned checkout, high intent",
        kind="decision",
    ))
    for r in v.blocking:
        steps.append(Step(
            label=f"{r.decision.value} - {r.rule_id}",
            detail=f"{r.message} Held for {r.defer_hours}h.  [{r.citation}]",
            kind="block",
        ))
    conn.close()
    verdict = (
        "None of these blocks is a heuristic. Each cites the instrument it comes "
        "from -- the RBI e-mandate framework for the pre-debit notification and the "
        "authentication ceiling, TRAI's commercial-communication rules for the "
        "delivery window. Two of the three are deferrals, not refusals: the action "
        "becomes lawful later, and the agent waits for it rather than dropping the "
        "revenue."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 4. The model asks for something it is not allowed to have
# --------------------------------------------------------------------------- #


def _scenario_rogue_llm() -> tuple[list[Step], str]:
    conn, provider, policy = _world()
    case = _canonical(conn, "CARD_EXPIRED")
    ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)
    scored = score_all(ctx, policy)
    narrator = Narrator()

    rogue = ActionOpinion(
        action="OFFER_50_PERCENT_DISCOUNT",
        confidence=0.97,
        one_line_reason="the customer will definitely pay if we discount it",
    )
    ok, why = narrator.validate_opinion(rogue)
    steps = [
        Step(
            label="Language model proposes an action",
            detail=f"{rogue.action} (confidence {rogue.confidence:.0%}): {rogue.one_line_reason}",
            kind="decision",
        ),
        Step(
            label="Schema validation" + (" accepted" if ok else " rejected"),
            detail=why,
            kind="block",
        ),
    ]

    # Even a well-formed action the merchant has not allowed goes nowhere.
    restricted = policy.model_copy(update={
        "allowed_actions": [ActionType.NO_ACTION, ActionType.HUMAN_ESCALATION]
    })
    link = next(s for s in scored if s.candidate.action is ActionType.PAYMENT_LINK)
    v = evaluate(ctx, link.candidate, restricted)
    steps.append(Step(
        label=f"Model instead proposes {link.candidate.label}, a real action",
        detail="well-formed, plausible, and still not this merchant's to take",
        kind="decision",
    ))
    for r in v.blocking:
        steps.append(Step(
            label=f"{r.decision.value} - {r.rule_id}",
            detail=f"{r.message}  [{r.citation}]",
            kind="block",
        ))

    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=choose_recoveryos)
    decisions = run_case(deps, case.case_id, BASE_NOW, run_id="demo-rogue")
    chosen = decisions[0].chosen.label if decisions else "-"
    steps.append(Step(
        label=f"Deterministic engine proceeds: {chosen}",
        detail="the run is unaffected; the model was never in the decision path",
        kind="money",
    ))
    conn.close()
    verdict = (
        "The model can propose anything, including an action that does not exist and "
        "an action this merchant has forbidden. Neither reaches execution: the first "
        "fails schema validation, the second fails the allow-list.\n"
        "This is not defence in depth bolted on afterwards. The engine never asked "
        "the model what to do -- `engine/` and `policy/` do not import `llm/` at all, "
        "and `test_llm_is_not_load_bearing` runs the whole book against a deliberately "
        "hostile model and asserts every decision and every rupee is unchanged."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 5. The whole loop, including knowing when to stop
# --------------------------------------------------------------------------- #


def _scenario_full_loop() -> tuple[list[Step], str]:
    conn, provider, policy = _world(n_cases=120)
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=choose_recoveryos)

    # Find a case that genuinely went round the loop more than once.
    best_case, best_decisions = None, []
    for case in dbmod.load_cases(conn):
        decisions = run_case(deps, case.case_id, BASE_NOW, run_id="demo-loop")
        if len(decisions) > len(best_decisions):
            best_case, best_decisions = case, decisions
        if len(best_decisions) >= 4:
            break

    steps = [Step(
        label=f"{best_case.case_id}: {_money(best_case.amount_paise)} at risk",
        detail=f"{best_case.failure_reason.value} on a {best_case.case_type.value.lower()}",
    )]
    for d in best_decisions:
        steps.append(Step(
            label=f"iteration {d.iteration}: {d.chosen.label}",
            detail=(
                f"{d.execution.outcome.value} -- {d.execution.detail}"
                if d.execution else ""
            ),
            kind="money" if d.execution and d.execution.recovered_paise else "decision",
            at=d.at.strftime("%d %b %H:%M"),
        ))
        if d.stop_reason:
            steps.append(Step(
                label=f"STOP: {d.stop_reason.value}",
                detail="; ".join(v.message for v in d.policy.blocking) or "no action left that beats doing nothing",
                kind="stop",
            ))

    chain = ledger.verify(conn)
    steps.append(Step(
        label=f"Audit chain {'verified' if chain['intact'] else 'BROKEN'}",
        detail=f"{chain['entries_checked']} decisions hash-chained; head {chain.get('head', '')[:16]}...",
        kind="money" if chain["intact"] else "block",
    ))
    conn.close()
    verdict = (
        "One case, several rounds, a changed world each time: an attempt spent, the "
        "clock moved, possibly a promise on record. The loop ends by saying which "
        "rule ended it, and every step of it is in a hash-chained ledger that "
        "`GET /api/audit/verify` will re-derive from scratch."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 6. The case for doing nothing
# --------------------------------------------------------------------------- #


def _scenario_no_action() -> tuple[list[Step], str]:
    conn, provider, policy = _world()
    case = _canonical(conn, "SELF_HEALER")
    ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)
    scored = score_all(ctx, policy)

    steps = [
        Step(
            label=f"{case.case_id}: {_money(case.amount_paise)}, expired card",
            detail=(
                f"{ctx.customer.prior_self_cures} of "
                f"{ctx.customer.prior_payments_failed} past failures for this "
                f"customer resolved with no intervention at all"
            ),
        ),
        Step(
            label=f"Baseline: {scored[0].p_self_cure:.0%} chance this recovers untouched",
            detail=" | ".join(
                s.explanation[1] for s in scored
                if s.candidate.action is ActionType.NO_ACTION and len(s.explanation) > 1
            ),
        ),
    ]
    for s in scored:
        v = evaluate(ctx, s.candidate, policy)
        steps.append(Step(
            label=f"{s.candidate.label}: utility {_money(s.utility_paise)}",
            detail=(
                f"incremental {_money(s.expected_incremental_paise)} "
                f"(uplift {s.uplift:.1%}) - cost {_money(s.cost_paise)} "
                f"- fatigue {_money(s.fatigue_penalty_paise)} "
                f"- risk {_money(s.risk_penalty_paise)}"
                + ("" if v.decision is PolicyDecision.PASS else f"  [{v.decision.value}]")
            ),
            kind="decision" if s.utility_paise > 0 else "block",
        ))

    choice = choose_recoveryos(
        [(s, evaluate(ctx, s.candidate, policy)) for s in scored], ctx, policy
    )
    conn.close()
    verdict = (
        f"The retries are blocked, correctly -- no retry clears a dead card. But every "
        f"way of *reaching* this customer was permitted: they have contact quota left, "
        f"no promise on record and consent on file. Nothing stopped us.\n"
        f"The engine chose {choice.chosen.label} anyway, because a customer who "
        f"fixes their own card within days is worth less to chase than the chase "
        f"costs. NO_ACTION scores exactly zero by construction, so every other "
        f"option has to beat leaving them alone, and here none of them does.\n"
        f"This is the decision a system optimised for activity can never make."
    )
    return steps, verdict


# --------------------------------------------------------------------------- #
# 7. Promise to pay
# --------------------------------------------------------------------------- #


def _scenario_promise() -> tuple[list[Step], str]:
    conn, provider, policy = _world()
    case = _canonical(conn, "PROMISE_ACTIVE")
    ctx = dbmod.build_context(conn, case.case_id, BASE_NOW)

    steps = [
        Step(
            label=f"{case.case_id}: {_money(case.amount_paise)} overdue",
            detail=f"customer replied: {ctx.promise.source_text!r}",
        ),
        Step(
            label="Extracted commitment",
            detail=(
                f"pay by {ctx.promise.promised_for:%a %d %b}, "
                f"{_money(ctx.promise.promised_amount_paise or 0)}, "
                f"confidence {ctx.promise.confidence:.0%}"
            ),
            kind="decision",
        ),
    ]
    for action in (
        CandidateAction(action=ActionType.CUSTOMER_MESSAGE, channel=Channel.WHATSAPP),
        CandidateAction(action=ActionType.DELAYED_RETRY, delay_hours=24),
        CandidateAction(action=ActionType.HUMAN_ESCALATION),
    ):
        v = evaluate(ctx, action, policy)
        for r in v.blocking:
            steps.append(Step(
                label=f"{action.action.value}: {r.decision.value} - {r.rule_id}",
                detail=r.message,
                kind="block",
            ))

    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=choose_recoveryos)
    decisions = run_case(deps, case.case_id, BASE_NOW, run_id="demo-promise")
    for d in decisions:
        steps.append(Step(
            label=f"iteration {d.iteration}: {d.chosen.label}",
            detail=d.execution.detail if d.execution else "",
            kind="money" if d.execution and d.execution.recovered_paise else "info",
            at=d.at.strftime("%d %b %H:%M"),
        ))
    final = dbmod.load_promise(conn, case.case_id)
    steps.append(Step(
        label=f"Promise resolved: {final.state.value}",
        detail=f"case ended {dbmod.load_case(conn, case.case_id).state.value}",
        kind="money" if final.state.value == "FULFILLED" else "stop",
    ))
    conn.close()
    verdict = (
        "A live promise blocks every action, including putting a human on the phone. "
        "Chasing someone who has already committed to a date costs goodwill and buys "
        "nothing.\n"
        "The commitment is read by a deterministic parser first; the language model is "
        "a fallback for text the parser cannot handle, and even then its date is "
        "clamped into a policy window before it can schedule anything. An earlier "
        "version let the model set that date directly, and the test suite caught it "
        "parking a customer in WAITING until 2099."
    )
    return steps, verdict


SCENARIOS: dict[str, Scenario] = {
    "counterfactual": Scenario(
        title="What did we actually cause?",
        question="Every recovery tool reports what it collected. How much of it would have arrived anyway?",
        archetype="-",
        run=_scenario_counterfactual,
    ),
    "no-action": Scenario(
        title="The smartest action is sometimes none",
        question="Nothing is blocked and the customer is reachable. Why is doing nothing correct?",
        archetype="SELF_HEALER",
        run=_scenario_no_action,
    ),
    "arbitration": Scenario(
        title="Two cases, one inbox",
        question="Two live cases want to message the same person, and only one slot is left. Who gets it?",
        archetype="ARBITRATION_MAJOR",
        run=_scenario_arbitration,
    ),
    "regulator": Scenario(
        title="Guardrails that cite the regulation",
        question="What stops the agent, and where does that rule actually come from?",
        archetype="RBI_NOTICE_MISSING",
        run=_scenario_regulator,
    ),
    "rogue-llm": Scenario(
        title="The model asks for something forbidden",
        question="What happens when the language model proposes an action it should not?",
        archetype="CARD_EXPIRED",
        run=_scenario_rogue_llm,
    ),
    "full-loop": Scenario(
        title="Observe, decide, act, observe again, stop",
        question="Does it close the loop, and does it know when to give up?",
        archetype="-",
        run=_scenario_full_loop,
    ),
    "promise": Scenario(
        title="I will pay on Friday",
        question="A customer commits to a date. What does the system do until then?",
        archetype="PROMISE_ACTIVE",
        run=_scenario_promise,
    ),
}


def run_scenario(key: str) -> dict[str, Any]:
    scenario = SCENARIOS[key]
    steps, verdict = scenario.run()
    return {
        "key": key,
        "title": scenario.title,
        "question": scenario.question,
        "steps": [
            {"label": s.label, "detail": s.detail, "kind": s.kind, "at": s.at} for s in steps
        ],
        "verdict": verdict,
        "disclaimer": "Simulated environment. Synthetic cases, simulated outcomes, no real customer contacted.",
    }
