"""The loop: does it close, does it stop, and does it stay bounded."""

from __future__ import annotations

from datetime import timedelta

from recoveryos import db as dbmod
from recoveryos.agent.graph import LANGGRAPH_AVAILABLE, run_case
from recoveryos.agent.nodes import AgentDeps
from recoveryos.evaluation.policies import POLICIES
from recoveryos.schemas import (
    ActionType,
    CaseState,
    OutcomeKind,
    PromiseState,
    TERMINAL_STATES,
)


def deps_for(world, policy, **kw):
    conn, provider, _ = world
    return AgentDeps(conn=conn, provider=provider, policy=policy,
                     chooser=POLICIES["RECOVERYOS"], **kw)


def test_every_case_reaches_a_terminal_state(world, canonical, now, policy):
    conn, _, _ = world
    deps = deps_for(world, policy)
    for name, case in canonical.items():
        run_case(deps, case.case_id, now, run_id="t")
        final = dbmod.load_case(conn, case.case_id)
        assert final.state in TERMINAL_STATES, f"{name} ended {final.state}"


def test_a_terminal_case_ends_with_a_reason_or_the_money(world, canonical, now, policy):
    conn, _, _ = world
    deps = deps_for(world, policy)
    for name, case in canonical.items():
        run_case(deps, case.case_id, now, run_id="t")
        final = dbmod.load_case(conn, case.case_id)
        if final.state is not CaseState.RECOVERED:
            assert final.stop_reason is not None, f"{name} stopped without saying why"


def test_langgraph_and_the_plain_driver_agree(tmp_path, now, policy):
    """Two drivers, one loop. If they ever diverge, the graph is lying."""
    from recoveryos.bootstrap import build_database
    from recoveryos.simulator.provider import MockPaymentProvider, load_truths

    assert LANGGRAPH_AVAILABLE, "langgraph should be installed for this suite"

    def run(use_langgraph: bool):
        _, conn = build_database(tmp_path / f"lg_{use_langgraph}.db", n_cases=40, seed=42)
        provider = MockPaymentProvider(load_truths(conn), seed=42)
        deps = AgentDeps(conn=conn, provider=provider, policy=policy,
                         chooser=POLICIES["RECOVERYOS"], record_audit=False)
        trace = []
        for case in dbmod.load_cases(conn):
            for d in run_case(deps, case.case_id, now, "t", use_langgraph=use_langgraph):
                trace.append((
                    d.case_id, d.iteration,
                    d.chosen.label if d.chosen else None,
                    d.policy.decision.value,
                    d.execution.outcome.value if d.execution else None,
                    d.execution.recovered_paise if d.execution else 0,
                    d.state_after.value,
                ))
        conn.close()
        return trace

    assert run(True) == run(False)


def test_a_failed_action_is_re_decided_rather_than_repeated(world, canonical, now, policy):
    """The closed loop. Without this it is a classifier with extra steps."""
    conn, _, _ = world
    deps = deps_for(world, policy)
    multi = 0
    for case in dbmod.load_cases(conn):
        decisions = run_case(deps, case.case_id, now, run_id="t")
        if len(decisions) > 1:
            multi += 1
            labels = [d.chosen.label for d in decisions if d.chosen]
            assert len(decisions) == len(set(d.iteration for d in decisions))
            assert labels
    assert multi > 0, "no case ever went round the loop twice"


def test_the_loop_is_bounded(world, canonical, now, policy):
    conn, _, _ = world
    deps = deps_for(world, policy)
    for case in dbmod.load_cases(conn):
        decisions = run_case(deps, case.case_id, now, run_id="t")
        assert len(decisions) <= policy.max_iterations_per_case + 2


def test_a_closed_case_is_not_worked_again(world, canonical, now, policy):
    """Invalid state transition: terminal means terminal."""
    conn, _, _ = world
    deps = deps_for(world, policy)
    case_id = canonical["NSF_TRANSIENT"].case_id
    run_case(deps, case_id, now, run_id="t")
    before = dbmod.load_case(conn, case_id)
    again = run_case(deps, case_id, now + timedelta(days=1), run_id="t2")
    after = dbmod.load_case(conn, case_id)
    assert again == []
    assert (after.state, after.recovered_paise) == (before.state, before.recovered_paise)


def test_re_ingesting_the_same_case_is_idempotent(world, canonical, now):
    """Duplicate events must not duplicate exposure."""
    conn, _, _ = world
    case = canonical["NSF_TRANSIENT"]
    before = len(dbmod.load_cases(conn))
    total_before = sum(c.amount_paise for c in dbmod.load_cases(conn))
    dbmod.insert_case(conn, case)
    dbmod.insert_case(conn, case)
    assert len(dbmod.load_cases(conn)) == before
    assert sum(c.amount_paise for c in dbmod.load_cases(conn)) == total_before


def test_holdout_cases_are_never_touched(world, now, policy):
    conn, _, _ = world
    deps = deps_for(world, policy)
    for case in dbmod.load_cases(conn):
        if not case.is_holdout:
            continue
        decisions = run_case(deps, case.case_id, now, run_id="t")
        for d in decisions:
            assert d.chosen.action is ActionType.NO_ACTION
            assert not (d.execution and d.execution.contact_made)
        assert dbmod.load_case(conn, case.case_id).attempts_made == case.attempts_made


def test_a_promise_extracted_from_free_text_then_protects_the_customer(world, canonical, now, policy):
    """Message -> customer replies with a commitment -> we stop chasing them."""
    conn, provider, _ = world
    deps = deps_for(world, policy)
    found = False
    for case in dbmod.load_cases(conn):
        for d in run_case(deps, case.case_id, now, run_id="t"):
            if d.execution and d.execution.outcome is OutcomeKind.PROMISE_MADE:
                found = True
                promise = dbmod.load_promise(conn, d.case_id)
                assert promise.state in (
                    PromiseState.ACTIVE, PromiseState.FULFILLED, PromiseState.BROKEN
                )
                assert promise.source_text
                assert promise.promised_for is not None
    assert found, "no case produced a promise-to-pay in this book"


def test_a_broken_promise_releases_the_case_back_into_the_loop(world, canonical, now, policy):
    conn, _, _ = world
    deps = deps_for(world, policy)
    case = canonical["PROMISE_BROKEN"]
    decisions = run_case(deps, case.case_id, now, run_id="t")
    assert decisions
    assert decisions[0].chosen.action is not ActionType.NO_ACTION, (
        "a broken promise should no longer shield the case"
    )


def test_waiting_on_a_promise_advances_the_clock(world, canonical, now, policy):
    """Regression: monitoring a promise used to re-decide the same case every
    round at the same timestamp, burning the iteration budget and stopping the
    case on MAX_ITERATIONS before the customer's own deadline arrived."""
    conn, _, _ = world
    deps = deps_for(world, policy)
    case = canonical["PROMISE_ACTIVE"]
    promise = dbmod.load_promise(conn, case.case_id)
    decisions = run_case(deps, case.case_id, now, run_id="t")

    assert decisions
    waiting = [d for d in decisions if d.chosen.action is ActionType.NO_ACTION
               and d.stop_reason is None]
    assert len(waiting) <= 2, "the agent spun on a live promise instead of sleeping"
    after = [d for d in decisions if d.at >= promise.promised_for]
    assert after, "the case never woke up to see whether the promise was kept"
    assert dbmod.load_promise(conn, case.case_id).state.value in {"FULFILLED", "BROKEN"}
