"""SQLite persistence.

Business state lives here, not in an LLM conversation. Every state transition
the agent makes is written before the next iteration reads it, so a run can be
killed at any point and resumed without losing or double-counting money.

Standard-library `sqlite3` on purpose: twelve tables, simple selects, an
append-only ledger. An ORM would add a dependency and a layer of indirection
for no gain.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .config import settings
from .schemas import (
    Case,
    CaseContext,
    CaseState,
    Channel,
    Customer,
    Promise,
    PromiseState,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_ACTIVE_STATES = (CaseState.OPEN.value, CaseState.WAITING.value)


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


def connect(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, reset: bool = False) -> None:
    if reset:
        for table in (
            "audit_log", "runs", "promises", "attempts", "contacts",
            "truths", "cases", "customers",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        case_type=row["case_type"],
        amount_paise=row["amount_paise"],
        currency=row["currency"],
        failure_reason=row["failure_reason"],
        raw_error_code=row["raw_error_code"],
        created_at=_dt(row["created_at"]),
        due_at=_dt(row["due_at"]),
        is_recurring=bool(row["is_recurring"]),
        mandate_id=row["mandate_id"],
        pre_debit_notice_sent_at=_dt(row["pre_debit_notice_sent_at"]),
        afa_present=bool(row["afa_present"]),
        afa_exempt_category=bool(row["afa_exempt_category"]),
        instrument_type=row["instrument_type"],
        instrument_expired=bool(row["instrument_expired"]),
        state=row["state"],
        stop_reason=row["stop_reason"],
        recovered_paise=row["recovered_paise"],
        attempts_made=row["attempts_made"],
        last_attempt_at=_dt(row["last_attempt_at"]),
        next_action_at=_dt(row["next_action_at"]),
        archetype=row["archetype"],
        is_holdout=bool(row["is_holdout"]),
    )


def _row_to_customer(row: sqlite3.Row) -> Customer:
    return Customer(
        customer_id=row["customer_id"],
        name=row["name"],
        segment=row["segment"],
        tenure_months=row["tenure_months"],
        lifetime_value_paise=row["lifetime_value_paise"],
        prior_payments_ok=row["prior_payments_ok"],
        prior_payments_failed=row["prior_payments_failed"],
        prior_self_cures=row["prior_self_cures"],
        pays_after_payday=bool(row["pays_after_payday"]),
        prior_recoveries_by_action=json.loads(row["prior_recoveries_by_action"]),
        preferred_channel=row["preferred_channel"],
        dlt_consent=bool(row["dlt_consent"]),
        opted_out=bool(row["opted_out"]),
    )


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def insert_customer(conn: sqlite3.Connection, c: Customer) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO customers VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            c.customer_id, c.name, c.segment.value, c.tenure_months,
            c.lifetime_value_paise, c.prior_payments_ok, c.prior_payments_failed,
            c.prior_self_cures, int(c.pays_after_payday),
            json.dumps(c.prior_recoveries_by_action), c.preferred_channel.value,
            int(c.dlt_consent), int(c.opted_out),
        ),
    )


def insert_case(conn: sqlite3.Connection, c: Case) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO cases VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            c.case_id, c.customer_id, c.case_type.value, c.amount_paise, c.currency,
            c.failure_reason.value, c.raw_error_code, _iso(c.created_at), _iso(c.due_at),
            int(c.is_recurring), c.mandate_id, _iso(c.pre_debit_notice_sent_at),
            int(c.afa_present), int(c.afa_exempt_category), c.instrument_type.value,
            int(c.instrument_expired), c.state.value,
            c.stop_reason.value if c.stop_reason else None, c.recovered_paise,
            c.attempts_made, _iso(c.last_attempt_at), _iso(c.next_action_at),
            c.archetype, int(c.is_holdout),
        ),
    )


def insert_truth(conn: sqlite3.Connection, case_id: str, truth: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO truths VALUES (?,?)",
        (case_id, json.dumps(truth, sort_keys=True)),
    )


def insert_promise(conn: sqlite3.Connection, p: Promise) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO promises VALUES (?,?,?,?,?,?,?)",
        (
            p.case_id, p.state.value, _iso(p.promised_at), _iso(p.promised_for),
            p.promised_amount_paise, p.confidence, p.source_text,
        ),
    )


def insert_contact(
    conn: sqlite3.Connection,
    customer_id: str,
    case_id: Optional[str],
    at: datetime,
    channel: Channel,
    action: str,
    run_id: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO contacts (customer_id, case_id, at, channel, action, run_id) VALUES (?,?,?,?,?,?)",
        (customer_id, case_id, _iso(at), channel.value, action, run_id),
    )


def insert_attempt(
    conn: sqlite3.Connection,
    case_id: str,
    at: datetime,
    outcome: str,
    run_id: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO attempts (case_id, at, outcome, run_id) VALUES (?,?,?,?)",
        (case_id, _iso(at), outcome, run_id),
    )


def update_case(conn: sqlite3.Connection, case: Case) -> None:
    insert_case(conn, case)


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def load_customer(conn: sqlite3.Connection, customer_id: str) -> Customer:
    row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown customer {customer_id}")
    return _row_to_customer(row)


def load_case(conn: sqlite3.Connection, case_id: str) -> Case:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown case {case_id}")
    return _row_to_case(row)


def load_cases(conn: sqlite3.Connection, states: Optional[Iterable[str]] = None) -> list[Case]:
    if states is None:
        rows = conn.execute("SELECT * FROM cases ORDER BY case_id").fetchall()
    else:
        states = list(states)
        marks = ",".join("?" * len(states))
        rows = conn.execute(
            f"SELECT * FROM cases WHERE state IN ({marks}) ORDER BY case_id", states
        ).fetchall()
    return [_row_to_case(r) for r in rows]


def load_promise(conn: sqlite3.Connection, case_id: str) -> Promise:
    row = conn.execute("SELECT * FROM promises WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return Promise(case_id=case_id, state=PromiseState.NONE)
    return Promise(
        case_id=row["case_id"],
        state=row["state"],
        promised_at=_dt(row["promised_at"]),
        promised_for=_dt(row["promised_for"]),
        promised_amount_paise=row["promised_amount_paise"],
        confidence=row["confidence"],
        source_text=row["source_text"],
    )


def count_contacts(
    conn: sqlite3.Connection, customer_id: str, since: datetime, until: datetime
) -> int:
    """Contacts to a *customer*, not a case -- fatigue is pooled per human."""
    return conn.execute(
        "SELECT COUNT(*) FROM contacts WHERE customer_id = ? AND at > ? AND at <= ?",
        (customer_id, _iso(since), _iso(until)),
    ).fetchone()[0]


def last_contact_at(conn: sqlite3.Connection, customer_id: str, until: datetime) -> Optional[datetime]:
    row = conn.execute(
        "SELECT MAX(at) AS m FROM contacts WHERE customer_id = ? AND at <= ?",
        (customer_id, _iso(until)),
    ).fetchone()
    return _dt(row["m"]) if row and row["m"] else None


def build_context(conn: sqlite3.Connection, case_id: str, now: datetime) -> CaseContext:
    """Assemble the single verified view the decision engine is allowed to see."""
    from datetime import timedelta

    case = load_case(conn, case_id)
    customer = load_customer(conn, case.customer_id)
    promise = load_promise(conn, case_id)

    siblings = conn.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(MAX(amount_paise), 0) AS mx
            FROM cases
            WHERE customer_id = ? AND case_id != ?
              AND state IN ({','.join('?' * len(_ACTIVE_STATES))})""",
        (case.customer_id, case_id, *_ACTIVE_STATES),
    ).fetchone()

    return CaseContext(
        case=case,
        customer=customer,
        promise=promise,
        now=now,
        contacts_24h=count_contacts(conn, case.customer_id, now - timedelta(hours=24), now),
        contacts_7d=count_contacts(conn, case.customer_id, now - timedelta(days=7), now),
        last_contact_at=last_contact_at(conn, case.customer_id, now),
        open_sibling_cases=siblings["n"],
        sibling_max_amount_paise=siblings["mx"],
    )
