"""The scenario library is the specification; these tests enforce it.

Each archetype exists to exercise one decision. If the engine starts retrying
dead cards, chasing customers who have already promised to pay, or messaging
people who opted out, the build fails here rather than in a demo.
"""

from __future__ import annotations

import pytest

from recoveryos import db as dbmod
from recoveryos.agent.nodes import choose_recoveryos
from recoveryos.engine.scoring import score_all
from recoveryos.expectations import (
    EXPECTED_ACTION,
    EXPECTED_ANY,
    EXPECTED_MAX_DELAY,
    EXPECTED_MIN_DELAY,
    EXPECTED_RULE,
    EXPECTED_TOP_VERDICT,
    FORBIDDEN,
)
from recoveryos.policy.guardrails import evaluate
from recoveryos.schemas import ActionType, PolicyDecision
from recoveryos.synthetic import ARCHETYPES

ARCHETYPE_NAMES = [a.name for a in ARCHETYPES]


def decide(conn, case_id, now, policy):
    ctx = dbmod.build_context(conn, case_id, now)
    gated = [(s, evaluate(ctx, s.candidate, policy)) for s in score_all(ctx, policy)]
    return ctx, gated, choose_recoveryos(gated, ctx, policy)


@pytest.mark.parametrize("name", ARCHETYPE_NAMES)
def test_archetype_reaches_its_expected_decision(world, canonical, now, policy, name):
    conn, _, _ = world
    case = canonical[name]
    _, _, choice = decide(conn, case.case_id, now, policy)
    chosen = choice.chosen.action

    if name in EXPECTED_ACTION:
        assert chosen is EXPECTED_ACTION[name], (
            f"{name}: expected {EXPECTED_ACTION[name].value}, got {choice.chosen.label}"
        )
    if name in EXPECTED_ANY:
        assert chosen in EXPECTED_ANY[name], (
            f"{name}: {choice.chosen.label} is not one of "
            f"{[a.value for a in EXPECTED_ANY[name]]}"
        )


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_forbidden_actions_are_never_chosen(world, canonical, now, policy, name):
    conn, _, _ = world
    _, _, choice = decide(conn, canonical[name].case_id, now, policy)
    assert choice.chosen.action not in FORBIDDEN[name], (
        f"{name}: chose forbidden action {choice.chosen.label}"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_TOP_VERDICT))
def test_top_scoring_action_gets_the_expected_verdict(world, canonical, now, policy, name):
    conn, _, _ = world
    _, gated, _ = decide(conn, canonical[name].case_id, now, policy)
    top = next(g for g in gated if g[0].candidate.action is not ActionType.NO_ACTION)
    assert top[1].decision is EXPECTED_TOP_VERDICT[name], (
        f"{name}: top action {top[0].candidate.label} got {top[1].decision.value}"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_RULE))
def test_the_right_rule_is_cited(world, canonical, now, policy, name):
    conn, _, _ = world
    _, gated, _ = decide(conn, canonical[name].case_id, now, policy)
    cited = {v.rule_id for _, verdict in gated for v in verdict.blocking}
    assert EXPECTED_RULE[name] in cited, f"{name}: cited {sorted(cited)}"


@pytest.mark.parametrize("name", sorted(EXPECTED_MIN_DELAY))
def test_retry_timing_reaches_the_salary_window(world, canonical, now, policy, name):
    conn, _, _ = world
    _, _, choice = decide(conn, canonical[name].case_id, now, policy)
    assert choice.chosen.action is ActionType.DELAYED_RETRY
    assert choice.chosen.delay_hours >= EXPECTED_MIN_DELAY[name], (
        f"{name}: retried at +{choice.chosen.delay_hours}h, too early to reach payday"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_MAX_DELAY))
def test_rail_outages_get_a_short_retry(world, canonical, now, policy, name):
    conn, _, _ = world
    _, _, choice = decide(conn, canonical[name].case_id, now, policy)
    assert choice.chosen.action is ActionType.DELAYED_RETRY
    assert choice.chosen.delay_hours <= EXPECTED_MAX_DELAY[name]


def test_no_action_always_scores_exactly_zero(world, canonical, now, policy):
    """The identity the whole product rests on."""
    conn, _, _ = world
    for case in dbmod.load_cases(conn):
        ctx = dbmod.build_context(conn, case.case_id, now)
        no_action = next(
            s for s in score_all(ctx, policy) if s.candidate.action is ActionType.NO_ACTION
        )
        assert no_action.utility_paise == 0
        assert no_action.uplift == 0.0


def test_utility_is_the_stated_arithmetic(world, canonical, now, policy):
    """Every score on screen must be reproducible from its own components."""
    conn, _, _ = world
    for case in dbmod.load_cases(conn)[:25]:
        ctx = dbmod.build_context(conn, case.case_id, now)
        for s in score_all(ctx, policy):
            assert s.utility_paise == (
                s.expected_incremental_paise
                - s.cost_paise
                - s.fatigue_penalty_paise
                - s.risk_penalty_paise
            )
            assert s.expected_incremental_paise == int(s.uplift * case.amount_paise)
            assert 0.0 <= s.p_self_cure <= 1.0
            assert s.p_treated >= s.p_self_cure - 1e-9


def test_uplift_shrinks_as_self_cure_rises(world, canonical, now, policy):
    """A customer who always pays unaided is worth less to chase, by construction."""
    conn, _, _ = world
    healer = dbmod.build_context(conn, canonical["SELF_HEALER"].case_id, now)
    expired = dbmod.build_context(conn, canonical["CARD_EXPIRED"].case_id, now)

    def link_uplift(ctx):
        return max(
            s.uplift for s in score_all(ctx, policy)
            if s.candidate.action is ActionType.PAYMENT_LINK
        )

    healer_scores = score_all(healer, policy)
    assert healer_scores[0].p_self_cure > 0.5, "self-healer history should be visible to the engine"
    assert link_uplift(healer) < link_uplift(expired)


def test_every_decision_carries_an_explanation(world, canonical, now, policy):
    conn, _, _ = world
    for name, case in canonical.items():
        ctx = dbmod.build_context(conn, case.case_id, now)
        for s in score_all(ctx, policy):
            assert s.explanation, f"{name}: {s.candidate.label} scored with no explanation"


def test_a_blocked_case_states_why_it_stopped(world, canonical, now, policy):
    conn, _, _ = world
    _, _, choice = decide(conn, canonical["RETRY_CAP_EXHAUSTED"].case_id, now, policy)
    assert choice.chosen.action is not ActionType.DELAYED_RETRY
    if choice.chosen.action is ActionType.NO_ACTION:
        assert choice.stop_reason is not None, "stopping without a reason is not acceptable"


def test_promise_active_blocks_even_a_human_call(world, canonical, now, policy):
    """Putting a person on the phone to someone who already committed is the
    same breach of trust as an automated nudge, with a bigger bill."""
    conn, _, _ = world
    ctx, gated, choice = decide(conn, canonical["PROMISE_ACTIVE"].case_id, now, policy)
    assert choice.chosen.action is ActionType.NO_ACTION
    for scored, verdict in gated:
        if scored.candidate.action is not ActionType.NO_ACTION:
            assert verdict.decision is not PolicyDecision.PASS
