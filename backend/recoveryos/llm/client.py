"""The language layer.

Deliberately small, and deliberately powerless. The LLM may:

* restate a diagnosis in plain English
* explain, in prose, a decision that has already been made
* draft the body of a customer message
* pull a date and an amount out of a free-text reply
* offer a non-binding second opinion on the chosen action

It may not: choose an action, compute a number, authorise a payment, override
a guardrail, or decide when to stop. Those are in `engine/` and `policy/`,
which do not import this package.

The practical consequence is a test, not a promise: `test_llm_is_not_load_bearing`
runs the whole book with a narrator that returns deliberate garbage and asserts
every decision and every rupee is unchanged. That is the difference between
claiming bounded autonomy and demonstrating it.

Transport is any OpenAI-compatible endpoint, set by three environment
variables. Hugging Face Inference Providers, Groq, OpenRouter, Together and a
local Ollama all work with no code change.

Responses are cached on disk by prompt hash, so a demo never depends on the
network, free inference credits are not burned re-running the same batch, and
a reviewer can reproduce the exact output with no token at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from ..config import settings
from ..schemas import ActionType, CaseContext, PolicyVerdict, ScoredAction, rupees

log = logging.getLogger("recoveryos.llm")

SYSTEM = (
    "You are the explanation layer of a payment-recovery decision system for an "
    "Indian merchant. The decision has already been made by a deterministic "
    "engine. Never contradict it, never invent amounts, dates, customer names or "
    "policy rules, and never suggest that a payment was taken. Write plainly, in "
    "British English, for an operations analyst. No marketing language."
)


# --------------------------------------------------------------------------- #
# Structured outputs the model is allowed to produce
# --------------------------------------------------------------------------- #


class PromiseExtraction(BaseModel):
    """A commitment pulled out of free text."""

    is_promise: bool
    days_from_now: int = Field(ge=0, le=90)
    amount_rupees: Optional[float] = Field(default=None, ge=0)
    confidence: float = Field(ge=0.0, le=1.0)


class ActionOpinion(BaseModel):
    """A non-binding second opinion. Advisory only; never executed."""

    action: str
    confidence: float = Field(ge=0.0, le=1.0)
    one_line_reason: str = ""


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


class LLMClient:
    """Thin, cached, failure-tolerant wrapper. Returns None instead of raising."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or settings.llm_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0

    def _key(self, prompt: str) -> Path:
        digest = hashlib.sha256(f"{settings.llm_model}|{prompt}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _lazy_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key or "unused",
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
            )
        return self._client

    def complete(self, prompt: str, max_tokens: Optional[int] = None) -> Optional[str]:
        cache = self._key(prompt)
        if cache.exists():
            self.cache_hits += 1
            return json.loads(cache.read_text(encoding="utf-8"))["text"]

        if settings.llm_cache_only or not settings.llm_enabled or not settings.llm_api_key:
            return None

        try:
            self.calls += 1
            response = self._lazy_client().chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens or settings.llm_max_tokens,
                temperature=0.2,
            )
            text = (response.choices[0].message.content or "").strip()
        except Exception as exc:  # network, auth, rate limit, provider outage
            self.failures += 1
            log.warning("LLM call failed, continuing without narration: %s", exc)
            return None

        cache.write_text(json.dumps({"text": text}), encoding="utf-8")
        return text


# --------------------------------------------------------------------------- #
# Parsing: assume the model will get it wrong, because sometimes it will
# --------------------------------------------------------------------------- #

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: Optional[str]) -> Optional[dict[str, Any]]:
    """Pull the first JSON object out of a model response.

    Open-weights models wrap JSON in prose, in code fences, or in both, and
    occasionally emit something that is not JSON at all. None of that is allowed
    to reach the rest of the system, so every failure mode lands here and
    returns None.
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.find("\n") + 1:] if "\n" in candidate else candidate
    match = _JSON_BLOCK.search(candidate)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# --------------------------------------------------------------------------- #
# The narrator the agent actually holds
# --------------------------------------------------------------------------- #


class Narrator:
    """Everything the agent is allowed to ask a language model for."""

    def __init__(self, client: Optional[LLMClient] = None):
        self.client = client or LLMClient()
        self.rejected_outputs = 0

    # -- explanation --------------------------------------------------------- #

    def explain_decision(
        self,
        ctx: CaseContext,
        diagnosis: str,
        scored: Optional[ScoredAction],
        verdict: PolicyVerdict,
        new_state,
    ) -> Optional[str]:
        if scored is None:
            return None
        blocks = "; ".join(f"{v.rule_id}: {v.message}" for v in verdict.blocking) or "none"
        prompt = f"""A recovery decision has already been made. Write two sentences explaining it to an analyst.

Case: {ctx.case.case_id}, {ctx.case.case_type.value}, INR {rupees(ctx.case.amount_paise)} at risk.
Failure: {ctx.case.failure_reason.value} ({ctx.retryability.value}).
Diagnosis: {diagnosis}
Chance of recovering with no intervention at all: {scored.p_self_cure:.0%}.
Chosen action: {scored.candidate.label}.
Expected incremental recovery: INR {rupees(scored.expected_incremental_paise)}.
Cost INR {rupees(scored.cost_paise)}, customer-fatigue price INR {rupees(scored.fatigue_penalty_paise)}, risk INR {rupees(scored.risk_penalty_paise)}.
Net utility: INR {rupees(scored.utility_paise)}.
Guardrail verdict: {verdict.decision.value}. Rules that fired: {blocks}
Resulting case state: {new_state.value}.

Explain why this action beat doing nothing, or why doing nothing won. Do not restate the numbers as a list. Two sentences."""
        text = self.client.complete(prompt, max_tokens=180)
        return text.strip() if text else None

    # -- customer-facing copy ------------------------------------------------ #

    def draft_message(self, ctx: CaseContext, diagnosis: str) -> str:
        prompt = f"""Draft one short WhatsApp message to a customer about a failed payment.

Customer first name: {ctx.customer.name.split()[0]}
Amount: INR {rupees(ctx.case.amount_paise)}
What went wrong: {diagnosis}

Rules: under 40 words. Polite, factual, no guilt, no urgency tricks, no emoji, no discount, no deadline you were not given. Do not claim the payment succeeded. End by telling them what one thing to do. Output only the message text."""
        text = self.client.complete(prompt, max_tokens=120)
        if not text or len(text) > 400:
            # Deterministic fallback. The system never depends on the model for
            # anything a customer would actually receive.
            self.rejected_outputs += 1 if text else 0
            return (
                f"Hi {ctx.customer.name.split()[0]}, we could not collect "
                f"INR {rupees(ctx.case.amount_paise)} for your last payment. "
                f"You can settle it using the link below whenever convenient."
            )
        return text.strip().strip('"')

    # -- extraction ---------------------------------------------------------- #

    def extract_promise(self, text: str, now: datetime) -> Optional[dict[str, Any]]:
        """Turn a free-text reply into a date and an amount, or give up cleanly."""
        prompt = f"""Extract any promise to pay from this customer reply.

Today is {now:%A %d %B %Y}.
Reply: {text!r}

Respond with only a JSON object:
{{"is_promise": true|false, "days_from_now": <integer 0-90>, "amount_rupees": <number or null>, "confidence": <0.0-1.0>}}"""
        parsed = parse_json_object(self.client.complete(prompt, max_tokens=120))
        if parsed is None:
            return None
        try:
            extraction = PromiseExtraction(**parsed)
        except ValidationError:
            # The model produced JSON, but not JSON that means anything here.
            # Rejected, counted, and the caller falls back to a conservative
            # default rather than acting on a hallucinated date.
            self.rejected_outputs += 1
            return None
        if not extraction.is_promise:
            return None
        return {
            "promised_for": now + timedelta(days=extraction.days_from_now),
            "amount_paise": int(extraction.amount_rupees * 100) if extraction.amount_rupees else None,
            "confidence": extraction.confidence,
        }

    # -- advisory second opinion --------------------------------------------- #

    def second_opinion(self, ctx: CaseContext, options: list[ScoredAction]) -> Optional[ActionOpinion]:
        """Ask the model what it would do. Recorded, compared, never executed.

        This exists to make a point that is easier to show than to argue: the
        model can propose anything it likes, including something forbidden, and
        the system is unaffected. See `scripts/demo.py rogue-llm`.
        """
        menu = "\n".join(
            f"- {s.candidate.label}: expected incremental INR {rupees(s.expected_incremental_paise)}"
            for s in options
        )
        prompt = f"""Which recovery action would you choose, and how sure are you?

Case: {ctx.case.case_type.value}, INR {rupees(ctx.case.amount_paise)} at risk, failure {ctx.case.failure_reason.value}.
Contacts to this customer in the last 7 days: {ctx.contacts_7d}.
Chance of recovering untouched: {options[0].p_self_cure:.0%}.

Options:
{menu}

Respond with only: {{"action": "<one option label>", "confidence": <0.0-1.0>, "one_line_reason": "<short>"}}"""
        parsed = parse_json_object(self.client.complete(prompt, max_tokens=120))
        if parsed is None:
            return None
        try:
            return ActionOpinion(**parsed)
        except ValidationError:
            self.rejected_outputs += 1
            return None

    def validate_opinion(self, opinion: Optional[ActionOpinion]) -> tuple[bool, str]:
        """Is the model even naming a real action? Usually the first thing it gets wrong."""
        if opinion is None:
            return False, "no usable output from the model"
        name = opinion.action.split(":")[0].split("+")[0].strip().upper()
        if name not in {a.value for a in ActionType}:
            return False, f"{opinion.action!r} is not an action this system has"
        return True, "names a real action"


def build_narrator() -> Optional[Narrator]:
    """The narrator, or None. Everything downstream must cope with None."""
    if not settings.llm_enabled:
        return None
    client = LLMClient()
    if not settings.llm_api_key and not settings.llm_cache_only:
        log.info("LLM_ENABLED is set but no key is configured; running on cached responses only.")
    return Narrator(client)
