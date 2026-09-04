"""Payment provider boundary, and the simulated world behind it.

`PaymentProvider` is the seam where a real PSP would attach. A Razorpay or
Stripe implementation would satisfy the same three methods and the rest of
RecoveryOS would not change a line. Only `MockPaymentProvider` exists here, on
purpose: no merchant activation, no KYC, no live money, no real customer ever
receives anything.

Reproducibility
---------------
Outcomes use **common random numbers**: the draw for a given
(case, action, iteration) is derived by hashing those keys with the run seed,
so it is identical no matter which policy asked for it. That is what makes the
policy comparison a fair fight rather than a lottery, and it is what makes the
counterfactual replay in `evaluation/oracle.py` legitimate.

Potential outcomes
------------------
A case has one latent `self_cure` draw. A treated case recovers if it would
have self-cured *or* if the incremental draw lands:

    P(recover | action) = p_self + (1 - p_self) * incremental
                        = p_treated

so treatment normally only adds recoveries. The single exception, and it is
deliberate, is opt-out: a customer contacted past their tolerance walks away
even from a payment they were going to make. That makes over-contacting
genuinely destructive rather than merely wasteful, which is the entire reason
the decision engine prices customer attention at all.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime
from typing import Protocol

from ..schemas import (
    ActionType,
    CandidateAction,
    CaseContext,
    ExecutionResult,
    LinkVariant,
    OutcomeKind,
    Promise,
    PromiseState,
)
from .truth import GroundTruth, response_probability

# Chance a nudged overdue invoice comes back as a commitment rather than money.
PROMISE_REPLY_PROB = 0.35
# Free-text replies the promise extractor has to cope with. Deliberately messy.
PROMISE_TEXTS = [
    "Sorry for the delay, I will make the payment on Friday.",
    "can pay by 5th, salary credit pending",
    "Payment will be processed next Tuesday once our client clears our invoice.",
    "give me till monday please",
    "Will settle this by the end of the week, apologies.",
]


def _rng(seed: int, *parts: object) -> random.Random:
    """Deterministic stream keyed by the semantic identity of the draw."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16) ^ seed)


class PaymentProvider(Protocol):
    """The seam a real PSP would implement."""

    def retry_debit(self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int) -> ExecutionResult: ...

    def deliver_link(self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int) -> ExecutionResult: ...

    def send_message(self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int, body: str) -> ExecutionResult: ...


class MockPaymentProvider:
    """The simulated world. Holds the answer key; the agent never sees it."""

    def __init__(self, truths: dict[str, GroundTruth], seed: int = 42):
        self._truths = truths
        self.seed = seed

    # -- helpers ---------------------------------------------------------- #

    def truth(self, case_id: str) -> GroundTruth:
        return self._truths[case_id]

    def self_cures(self, case_id: str) -> bool:
        """Would this case have recovered untouched? One draw, shared by every policy."""
        t = self.truth(case_id)
        return _rng(self.seed, case_id, "self_cure").random() < t.self_cure_prob

    def _resolve(
        self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int
    ) -> tuple[bool, GroundTruth]:
        t = self.truth(ctx.case.case_id)
        p_treated = response_probability(
            t, action, ctx.retryability, ctx.hours_since_failure, ctx.contacts_7d, at
        )
        if self.self_cures(ctx.case.case_id):
            return True, t
        p_self = t.self_cure_prob
        incremental = 0.0 if p_self >= 1.0 else max(0.0, (p_treated - p_self) / (1 - p_self))
        draw = _rng(self.seed, ctx.case.case_id, action.label, iteration).random()
        return draw < incremental, t

    def _opted_out(self, ctx: CaseContext, t: GroundTruth, iteration: int) -> bool:
        if ctx.contacts_7d < 2:
            return False
        excess = ctx.contacts_7d - 1
        p = min(0.5, t.opt_out_prob_per_excess_contact * excess)
        return _rng(self.seed, ctx.case.case_id, "opt_out", iteration).random() < p

    def _maybe_promise(self, ctx: CaseContext, iteration: int, channel_desc: str):
        """Some customers answer a chase with a commitment instead of a payment.

        The reply arrives as unstructured free text. Turning it into a date and
        an amount is one of the few jobs in this system an LLM is genuinely the
        right tool for -- and the deterministic fallback in `agent/nodes.py`
        means the workflow survives the model getting it wrong.
        """
        if ctx.promise.state is not PromiseState.NONE:
            return None
        rng = _rng(self.seed, ctx.case.case_id, "promise", iteration)
        if rng.random() >= PROMISE_REPLY_PROB:
            return None
        text = rng.choice(PROMISE_TEXTS)
        return ExecutionResult(
            outcome=OutcomeKind.PROMISE_MADE,
            detail=f"{channel_desc} delivered; customer replied: {text!r}",
            contact_made=True,
            promise=Promise(case_id=ctx.case.case_id, state=PromiseState.NONE, source_text=text),
        )

    # -- provider surface ------------------------------------------------- #

    def retry_debit(
        self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int
    ) -> ExecutionResult:
        ok, _ = self._resolve(ctx, action, at, iteration)
        if ok:
            return ExecutionResult(
                outcome=OutcomeKind.RECOVERED,
                recovered_paise=ctx.case.amount_paise,
                detail=f"Debit re-presented at {at:%d %b %H:%M} and cleared.",
            )
        return ExecutionResult(
            outcome=OutcomeKind.FAILED_AGAIN,
            detail=f"Debit re-presented at {at:%d %b %H:%M} and failed again.",
        )

    def deliver_link(
        self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int
    ) -> ExecutionResult:
        ok, t = self._resolve(ctx, action, at, iteration)
        kind = "instrument-update" if action.variant is LinkVariant.UPDATE_INSTRUMENT else "collection"
        # Opt-out is resolved before the outcome: a customer pushed past their
        # tolerance walks away even from a payment they were going to make. This
        # is the one path by which an intervention destroys value rather than
        # merely wasting money, and it is why customer attention is priced at all.
        if self._opted_out(ctx, t, iteration):
            return ExecutionResult(
                outcome=OutcomeKind.OPTED_OUT,
                detail="Customer opted out of further commercial contact.",
                contact_made=True,
            )
        if ok:
            return ExecutionResult(
                outcome=OutcomeKind.RECOVERED,
                recovered_paise=ctx.case.amount_paise,
                detail=f"Customer opened the {kind} link and paid.",
                contact_made=True,
            )
        promised = self._maybe_promise(ctx, iteration, f"{kind} link")
        if promised is not None:
            return promised
        return ExecutionResult(
            outcome=OutcomeKind.NO_RESPONSE,
            detail=f"{kind.capitalize()} link delivered; no action taken by the customer.",
            contact_made=True,
        )

    def send_message(
        self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int, body: str = ""
    ) -> ExecutionResult:
        ok, t = self._resolve(ctx, action, at, iteration)
        channel = action.channel.value if action.channel else "WHATSAPP"
        if self._opted_out(ctx, t, iteration):
            return ExecutionResult(
                outcome=OutcomeKind.OPTED_OUT,
                detail="Customer opted out of further commercial contact.",
                contact_made=True,
            )
        if ok:
            return ExecutionResult(
                outcome=OutcomeKind.RECOVERED,
                recovered_paise=ctx.case.amount_paise,
                detail=f"{channel} reminder delivered; customer paid.",
                contact_made=True,
            )
        promised = self._maybe_promise(ctx, iteration, f"{channel} reminder")
        if promised is not None:
            return promised
        return ExecutionResult(
            outcome=OutcomeKind.NO_RESPONSE,
            detail=f"{channel} reminder delivered; no reply.",
            contact_made=True,
        )

    def escalate(self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int) -> ExecutionResult:
        ok, _ = self._resolve(ctx, action, at, iteration)
        if ok:
            return ExecutionResult(
                outcome=OutcomeKind.RECOVERED,
                recovered_paise=ctx.case.amount_paise,
                detail="Collections owner worked the case and collected.",
            )
        return ExecutionResult(
            outcome=OutcomeKind.TRANSFERRED,
            detail="Case handed to a collections owner; outside automated recovery from here.",
        )

    def settle_untouched(self, ctx: CaseContext) -> ExecutionResult:
        """Close a case nobody acted on. This is the counterfactual arm."""
        if self.self_cures(ctx.case.case_id):
            return ExecutionResult(
                outcome=OutcomeKind.RECOVERED,
                recovered_paise=ctx.case.amount_paise,
                detail="Recovered with no intervention. Nothing was spent and nobody was contacted.",
            )
        return ExecutionResult(
            outcome=OutcomeKind.NO_RESPONSE,
            detail="Not recovered, and no intervention was judged worth its cost.",
        )

    def execute(
        self, ctx: CaseContext, action: CandidateAction, at: datetime, iteration: int, body: str = ""
    ) -> ExecutionResult:
        if action.action is ActionType.NO_ACTION:
            return self.settle_untouched(ctx)
        if action.action is ActionType.DELAYED_RETRY:
            return self.retry_debit(ctx, action, at, iteration)
        if action.action is ActionType.PAYMENT_LINK:
            return self.deliver_link(ctx, action, at, iteration)
        if action.action is ActionType.CUSTOMER_MESSAGE:
            return self.send_message(ctx, action, at, iteration, body)
        return self.escalate(ctx, action, at, iteration)


def load_truths(conn) -> dict[str, GroundTruth]:
    import json

    rows = conn.execute("SELECT case_id, truth_json FROM truths").fetchall()
    return {r["case_id"]: GroundTruth(**json.loads(r["truth_json"])) for r in rows}
