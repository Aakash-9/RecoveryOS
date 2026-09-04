"""The agent state machine.

Primary implementation is a LangGraph `StateGraph` over the node functions in
`nodes.py`. If LangGraph is not importable, the identical nodes run through a
twenty-line driver instead.

ponytail: two drivers for one loop is normally waste. It is kept because the
graph is the artefact people want to see and the fallback is what makes a live
demo immune to a dependency failure. `tests/test_agent.py` asserts the two
paths produce identical decisions, so they cannot drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..schemas import Decision
from .nodes import AgentDeps, RunState, diagnose_node, execute, gate, load, observe, score_node

try:  # pragma: no cover - import-time capability check
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    LANGGRAPH_AVAILABLE = False

MAX_STEPS = 64


def _initial(case_id: str, run_id: str, now: datetime) -> RunState:
    return {
        "case_id": case_id,
        "run_id": run_id,
        "iteration": 0,
        "now": now,
        "started_at": now,
        "decisions": [],
        "done": False,
        "wait_hours": 0,
        "stop_reason": None,
    }


def build_graph(deps: AgentDeps):
    """Compile the LangGraph state machine. Raises if LangGraph is missing."""
    from functools import partial

    g = StateGraph(RunState)
    g.add_node("LOAD_CASE", partial(load, deps))
    g.add_node("DIAGNOSE", partial(diagnose_node, deps))
    g.add_node("SCORE_ACTIONS", partial(score_node, deps))
    g.add_node("CHECK_GUARDRAILS", partial(gate, deps))
    g.add_node("EXECUTE", partial(execute, deps))
    g.add_node("OBSERVE", partial(observe, deps))

    g.add_edge(START, "LOAD_CASE")
    g.add_conditional_edges(
        "LOAD_CASE", lambda s: END if s.get("done") else "DIAGNOSE", {END: END, "DIAGNOSE": "DIAGNOSE"}
    )
    g.add_conditional_edges(
        "DIAGNOSE",
        lambda s: END if s.get("done") else "SCORE_ACTIONS",
        {END: END, "SCORE_ACTIONS": "SCORE_ACTIONS"},
    )
    g.add_edge("SCORE_ACTIONS", "CHECK_GUARDRAILS")
    g.add_edge("CHECK_GUARDRAILS", "EXECUTE")
    g.add_edge("EXECUTE", "OBSERVE")
    # The cycle. A failed action re-enters the loop against a changed world.
    g.add_conditional_edges(
        "OBSERVE", lambda s: END if s.get("done") else "LOAD_CASE", {END: END, "LOAD_CASE": "LOAD_CASE"}
    )
    return g.compile()


def _run_plain(deps: AgentDeps, state: RunState) -> RunState:
    """Same nodes, same order, no dependency."""
    for _ in range(MAX_STEPS):
        state = load(deps, state)
        if state.get("done"):
            return state
        state = diagnose_node(deps, state)
        if state.get("done"):
            return state
        state = score_node(deps, state)
        state = gate(deps, state)
        state = execute(deps, state)
        state = observe(deps, state)
        if state.get("done"):
            return state
    return {**state, "done": True}


def run_case(
    deps: AgentDeps,
    case_id: str,
    now: datetime,
    run_id: str = "adhoc",
    use_langgraph: Optional[bool] = None,
) -> list[Decision]:
    """Work one case to a terminal state. Returns every decision made."""
    state = _initial(case_id, run_id, now)
    use_lg = LANGGRAPH_AVAILABLE if use_langgraph is None else use_langgraph
    if use_lg:
        final = build_graph(deps).invoke(state, {"recursion_limit": MAX_STEPS})
    else:
        final = _run_plain(deps, state)
    return list(final.get("decisions", []))


def mermaid() -> str:
    """The graph, for the README and the architecture page."""
    return """flowchart TD
    START([revenue at risk]) --> LOAD_CASE
    LOAD_CASE{{LOAD_CASE\\nheld out? terminal? horizon?}}
    LOAD_CASE -->|control arm / closed| DONE([close])
    LOAD_CASE --> DIAGNOSE[DIAGNOSE\\nreason code -> retryability\\npromise due?]
    DIAGNOSE -->|promise kept| DONE
    DIAGNOSE --> SCORE[SCORE_ACTIONS\\nself-cure baseline\\nuplift x amount - cost - fatigue - risk]
    SCORE --> GUARD[CHECK_GUARDRAILS\\nRBI / TRAI / merchant policy]
    GUARD --> EXEC[EXECUTE\\nprovider call or deliberate no-op]
    EXEC --> OBSERVE[OBSERVE\\npersist state, record outcome, hash-chain the decision]
    OBSERVE -->|recovered / stopped / escalated| DONE
    OBSERVE -->|failed, retry allowed, clock advanced| LOAD_CASE
"""
