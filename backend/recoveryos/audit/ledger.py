"""Append-only, hash-chained decision ledger.

Every decision the agent makes -- including the ones where it decided to do
nothing, and the ones a guardrail refused -- is written here before the next
step runs. Each entry commits to its predecessor:

    entry_hash = sha256(prev_hash || run_id || case_id || at || payload)

so editing or deleting any historical decision breaks every hash after it.
`verify()` walks the chain and says exactly where it broke. That is what
"audit trail" has to mean for anything touching money: not a log you can read,
a log you can *prove nobody rewrote*.

There is no update or delete path in this module by design.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

GENESIS = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev: str, run_id: str, case_id: str, at: str, payload_json: str) -> str:
    return hashlib.sha256(
        "|".join((prev, run_id, case_id, at, payload_json)).encode("utf-8")
    ).hexdigest()


def head(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    return row["entry_hash"] if row else GENESIS


def append(
    conn: sqlite3.Connection,
    run_id: str,
    case_id: str,
    at: datetime,
    payload: dict[str, Any],
) -> str:
    prev = head(conn)
    at_s = at.isoformat(timespec="seconds")
    payload_json = _canonical(payload)
    entry = _hash(prev, run_id, case_id, at_s, payload_json)
    conn.execute(
        "INSERT INTO audit_log (run_id, case_id, at, payload_json, prev_hash, entry_hash)"
        " VALUES (?,?,?,?,?,?)",
        (run_id, case_id, at_s, payload_json, prev, entry),
    )
    return entry


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    """Recompute the whole chain. Returns a verdict, not an exception."""
    prev = GENESIS
    checked = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY seq ASC"):
        if row["prev_hash"] != prev:
            return {
                "intact": False,
                "entries_checked": checked,
                "broken_at_seq": row["seq"],
                "reason": "predecessor hash does not match the previous entry",
            }
        expected = _hash(prev, row["run_id"], row["case_id"], row["at"], row["payload_json"])
        if expected != row["entry_hash"]:
            return {
                "intact": False,
                "entries_checked": checked,
                "broken_at_seq": row["seq"],
                "reason": "entry content does not match its recorded hash",
            }
        prev = row["entry_hash"]
        checked += 1
    return {"intact": True, "entries_checked": checked, "head": prev}


def for_case(conn: sqlite3.Connection, case_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE case_id = ? ORDER BY seq ASC", (case_id,)
    ).fetchall()
    return [
        {
            "seq": r["seq"],
            "run_id": r["run_id"],
            "at": r["at"],
            "entry_hash": r["entry_hash"],
            "prev_hash": r["prev_hash"],
            **json.loads(r["payload_json"]),
        }
        for r in rows
    ]


def recent(conn: sqlite3.Connection, limit: int = 100, run_id: Optional[str] = None) -> list[dict[str, Any]]:
    if run_id:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE run_id = ? ORDER BY seq DESC LIMIT ?", (run_id, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    return [
        {"seq": r["seq"], "case_id": r["case_id"], "at": r["at"], **json.loads(r["payload_json"])}
        for r in rows
    ]
