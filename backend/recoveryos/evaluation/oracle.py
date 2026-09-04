"""The ceiling, and how far each policy falls short of it.

In the real world you can never know what the other action would have done.
Here you can: the simulator's draws are keyed by (case, action, iteration), so
replaying a case under a different action gives exactly the outcome that action
*would* have produced. That makes a genuine oracle computable.

The oracle gets the same number of lawful attempts a real policy is allowed,
plus perfect foresight about how the customer responds to each one. It is not
achievable by any real system -- that is the point. It converts "our policy recovered X" into "our policy captured Y% of
what was actually there to capture", which is a far more honest way to compare
two policies than comparing their raw totals.

Note it is a *lawful* oracle: it may only choose actions the guardrail engine
would have permitted. An oracle allowed to break the rules would be a
meaningless benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..db import build_context, load_cases
from ..engine.scoring import candidates
from ..policy.guardrails import MerchantPolicy, evaluate, load_policy
from ..schemas import ActionType, PolicyDecision


@dataclass
class OracleResult:
    recoverable_paise: int = 0
    recoverable_cases: int = 0
    total_at_risk_paise: int = 0
    self_cure_paise: int = 0
    self_cure_cases: int = 0
    best_action_by_case: dict[str, str] = None

    @property
    def winnable_paise(self) -> int:
        """Recoverable value that would *not* have arrived on its own."""
        return self.recoverable_paise - self.self_cure_paise


def compute_oracle(
    conn, provider, now: datetime, policy: MerchantPolicy | None = None
) -> OracleResult:
    """Must be run against a pristine database -- before any policy touches it.

    Approximation worth naming: every hypothetical attempt is evaluated at the
    opening clock, so the oracle does not get credit for perfect *timing* on top
    of perfect foresight. It slightly understates the true ceiling.
    """
    p = policy or load_policy()
    out = OracleResult(best_action_by_case={})

    for case in load_cases(conn):
        ctx = build_context(conn, case.case_id, now)
        out.total_at_risk_paise += case.amount_paise

        self_cures = provider.self_cures(case.case_id)
        if self_cures:
            out.self_cure_paise += case.amount_paise
            out.self_cure_cases += 1

        # The ceiling has to be a fair one. A real policy gets several rounds,
        # so the oracle gets the same budget of lawful attempts -- otherwise a
        # multi-round policy could "beat" the ceiling simply by drawing twice.
        best: str | None = None
        lawful = [
            c for c in candidates(ctx)
            if c.action is not ActionType.NO_ACTION
            and evaluate(ctx, c, p).decision is PolicyDecision.PASS
        ]
        if self_cures:
            best = ActionType.NO_ACTION.value
        else:
            for attempt in range(p.max_iterations_per_case):
                for cand in lawful:
                    if provider._resolve(ctx, cand, now, attempt)[0]:
                        best = cand.label
                        break
                if best:
                    break

        if best is not None:
            out.recoverable_paise += case.amount_paise
            out.recoverable_cases += 1
            out.best_action_by_case[case.case_id] = best

    return out


def capture_rate(recovered_paise: int, oracle: OracleResult) -> float:
    """Share of the theoretically available recovery a policy actually captured."""
    return recovered_paise / oracle.recoverable_paise if oracle.recoverable_paise else 0.0
