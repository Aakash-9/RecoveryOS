"""Auditability, reproducibility, and the wall between the agent and the answers."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from recoveryos import db as dbmod
from recoveryos.agent.graph import run_case
from recoveryos.agent.nodes import AgentDeps
from recoveryos.audit import ledger
from recoveryos.bootstrap import build_database
from recoveryos.evaluation.policies import POLICIES
from recoveryos.schemas import CaseContext

PACKAGE = Path(__file__).resolve().parents[1] / "recoveryos"
# Modules that decide what to do. None of them may see how the world responds.
DECISION_DIRS = ("engine", "policy")
FORBIDDEN_IMPORTS = ("simulator.truth", "simulator.provider", "simulator")


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Relative imports: ".." + "simulator.truth" -> "simulator.truth"
            found.add(node.module or "")
    return found


@pytest.mark.parametrize(
    "path",
    [p for d in DECISION_DIRS for p in (PACKAGE / d).glob("*.py")],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_the_decision_engine_cannot_see_the_answer_key(path: Path):
    """The single most important test in this repository.

    If the estimator could read the simulator's response model, every number in
    the evaluation would be circular and the whole project would be theatre.
    """
    for mod in _imports(path):
        assert not any(mod.endswith(f) or mod == f for f in FORBIDDEN_IMPORTS), (
            f"{path.name} imports {mod}; the decision layer must not see the simulator"
        )


def test_the_context_handed_to_the_engine_carries_no_ground_truth():
    fields = set(CaseContext.model_fields)
    leaks = {f for f in fields if "truth" in f or "self_cure_prob" in f or "retry_curve" in f}
    assert not leaks, f"CaseContext leaks {leaks}"


def test_ground_truth_lives_in_its_own_table(world):
    conn, _, _ = world
    case_columns = {r[1] for r in conn.execute("PRAGMA table_info(cases)")}
    assert not (case_columns & {"truth_json", "self_cure_prob", "retry_curve"})
    assert conn.execute("SELECT COUNT(*) FROM truths").fetchone()[0] > 0


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_every_decision_is_written_to_the_ledger(world, canonical, now, policy):
    conn, provider, _ = world
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=POLICIES["RECOVERYOS"])
    written = 0
    for case in dbmod.load_cases(conn):
        written += len(run_case(deps, case.case_id, now, run_id="audit"))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == written
    assert written > 0


def test_the_ledger_verifies(world, canonical, now, policy):
    conn, provider, _ = world
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=POLICIES["RECOVERYOS"])
    for case in dbmod.load_cases(conn):
        run_case(deps, case.case_id, now, run_id="audit")
    result = ledger.verify(conn)
    assert result["intact"] is True
    assert result["entries_checked"] > 0


def test_tampering_with_a_past_decision_is_detected(world, canonical, now, policy):
    """An audit log you can quietly edit is not an audit log."""
    conn, provider, _ = world
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=POLICIES["RECOVERYOS"])
    for case in list(dbmod.load_cases(conn))[:15]:
        run_case(deps, case.case_id, now, run_id="audit")
    assert ledger.verify(conn)["intact"] is True

    row = conn.execute("SELECT seq, payload_json FROM audit_log ORDER BY seq LIMIT 1 OFFSET 2").fetchone()
    payload = json.loads(row["payload_json"])
    payload["recovered_paise"] = 99_999_999          # somebody moves the money
    conn.execute("UPDATE audit_log SET payload_json = ? WHERE seq = ?",
                 (json.dumps(payload, sort_keys=True, separators=(",", ":")), row["seq"]))

    verdict = ledger.verify(conn)
    assert verdict["intact"] is False
    assert verdict["broken_at_seq"] == row["seq"]


def test_deleting_a_decision_is_detected(world, canonical, now, policy):
    conn, provider, _ = world
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=POLICIES["RECOVERYOS"])
    for case in list(dbmod.load_cases(conn))[:10]:
        run_case(deps, case.case_id, now, run_id="audit")
    conn.execute("DELETE FROM audit_log WHERE seq = (SELECT MIN(seq) + 1 FROM audit_log)")
    assert ledger.verify(conn)["intact"] is False


def test_an_audit_entry_reconstructs_the_whole_decision(world, canonical, now, policy):
    conn, provider, _ = world
    deps = AgentDeps(conn=conn, provider=provider, policy=policy, chooser=POLICIES["RECOVERYOS"])
    case = canonical["CARD_EXPIRED"]
    run_case(deps, case.case_id, now, run_id="audit")

    entries = ledger.for_case(conn, case.case_id)
    assert entries
    first = entries[0]
    for key in ("diagnosis", "retryability", "p_self_cure", "chosen_action",
                "candidates", "policy_decision", "policy_rules", "outcome",
                "state_before", "state_after", "entry_hash", "prev_hash"):
        assert key in first, f"audit entry missing {key}"
    assert first["candidates"], "the alternatives considered must be recorded, not just the winner"
    for candidate in first["candidates"]:
        assert candidate["utility_paise"] == (
            candidate["expected_incremental_paise"] - candidate["cost_paise"]
            - candidate["fatigue_paise"] - candidate["risk_paise"]
        )


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def _fingerprint(conn) -> str:
    rows = []
    for table in ("customers", "cases", "truths", "contacts", "promises"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            rows.append("|".join(str(v) for v in tuple(row)))
    return "\n".join(rows)


def test_the_same_seed_builds_the_same_world(tmp_path):
    _, a = build_database(tmp_path / "a.db", n_cases=50, seed=7)
    _, b = build_database(tmp_path / "b.db", n_cases=50, seed=7)
    _, c = build_database(tmp_path / "c.db", n_cases=50, seed=8)
    fa, fb, fc = _fingerprint(a), _fingerprint(b), _fingerprint(c)
    a.close(); b.close(); c.close()
    assert fa == fb
    assert fa != fc, "different seeds should build different worlds"


def test_the_same_seed_produces_the_same_run(tmp_path, now, policy):
    from recoveryos.simulator.provider import MockPaymentProvider, load_truths

    def run(tag: str):
        _, conn = build_database(tmp_path / f"{tag}.db", n_cases=50, seed=11)
        provider = MockPaymentProvider(load_truths(conn), seed=11)
        deps = AgentDeps(conn=conn, provider=provider, policy=policy,
                         chooser=POLICIES["RECOVERYOS"], record_audit=False)
        trace = []
        for case in dbmod.load_cases(conn):
            for d in run_case(deps, case.case_id, now, "t"):
                trace.append((d.case_id, d.iteration, d.chosen.label,
                              d.execution.outcome.value, d.execution.recovered_paise))
        conn.close()
        return trace

    assert run("run1") == run("run2")
