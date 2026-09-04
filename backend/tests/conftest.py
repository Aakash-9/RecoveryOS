from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from recoveryos import db as dbmod  # noqa: E402
from recoveryos.bootstrap import build_database  # noqa: E402
from recoveryos.policy.guardrails import load_policy  # noqa: E402
from recoveryos.simulator.provider import MockPaymentProvider, load_truths  # noqa: E402
from recoveryos.synthetic import ARCHETYPES, BASE_NOW  # noqa: E402

SEED = 42


@pytest.fixture(scope="session")
def now() -> datetime:
    return BASE_NOW


@pytest.fixture
def policy():
    return load_policy()


@pytest.fixture
def world(tmp_path):
    """A fresh synthetic world: one canonical case per archetype, plus a tail."""
    cases, conn = build_database(tmp_path / "test.db", n_cases=len(ARCHETYPES) + 20, seed=SEED)
    provider = MockPaymentProvider(load_truths(conn), seed=SEED)
    yield conn, provider, cases
    conn.close()


@pytest.fixture
def canonical(world):
    """First case of each archetype, keyed by archetype name."""
    conn, _, _ = world
    out = {}
    for case in dbmod.load_cases(conn):
        out.setdefault(case.archetype, case)
    return out
