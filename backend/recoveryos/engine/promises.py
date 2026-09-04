"""Turning "I will pay on Friday" into a date, deterministically.

Why this exists at all: an earlier version handed promise extraction straight
to the language model, and `test_llm_is_not_load_bearing` immediately caught
the consequence. A hallucinated date became a real scheduling decision -- the
model could park a case in WAITING until 2099 and the customer would never be
chased again. That is the language model reaching into business state through
the side door.

So the order is now:

1. This parser, which handles the shapes people actually write.
2. Failing that, the model -- whose answer is **clamped** into a policy window
   before it is allowed anywhere near the state machine.
3. Failing that, a conservative default.

The model extends coverage. It does not get to choose the date unsupervised.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# A promise is a commitment, not an indefinite deferral. Anything beyond this
# is not a promise to pay, it is a way of not being contacted.
MAX_PROMISE_HORIZON_DAYS = 21
MIN_PROMISE_HORIZON_HOURS = 12
DEFAULT_PROMISE_DAYS = 3

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_DAY_OF_MONTH = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)\b", re.I)
_NEGATION = re.compile(r"\b(cannot|can not|can't|won-t|will not|unable|refuse|dispute)\b", re.I)
# These arrive as replies to a payment chase, so intent is often carried by the
# deferral rather than by a verb: "give me till Monday" is a promise to pay,
# and a system that cannot read it will keep chasing someone who answered.
_INTENT = re.compile(
    r"\b(pay|payment|settle|clear|transfer|remit|process|till|until|by|give me|need|allow)\w*\b",
    re.I,
)


def _next_weekday(now: datetime, target: int) -> datetime:
    ahead = (target - now.weekday()) % 7
    return (now + timedelta(days=ahead or 7)).replace(hour=10, minute=0, second=0, microsecond=0)


def _next_day_of_month(now: datetime, day: int) -> Optional[datetime]:
    if not 1 <= day <= 31:
        return None
    candidate = now
    for _ in range(2):
        try:
            dated = candidate.replace(day=day, hour=10, minute=0, second=0, microsecond=0)
        except ValueError:
            dated = None
        if dated and dated > now:
            return dated
        # Roll into the following month.
        candidate = (candidate.replace(day=28) + timedelta(days=7)).replace(day=1)
    return None


def parse_promise_text(text: str, now: datetime) -> Optional[dict]:
    """Deterministic extraction. Returns None when it genuinely cannot tell."""
    if not text:
        return None
    lowered = text.lower()
    if _NEGATION.search(lowered) or not _INTENT.search(lowered):
        return None

    when: Optional[datetime] = None
    confidence = 0.5

    if "day after tomorrow" in lowered:
        when, confidence = now + timedelta(days=2), 0.85
    elif "tomorrow" in lowered:
        when, confidence = now + timedelta(days=1), 0.85
    elif "today" in lowered or "tonight" in lowered:
        when, confidence = now + timedelta(hours=12), 0.8
    elif "end of the week" in lowered or "end of week" in lowered:
        when, confidence = _next_weekday(now, 4), 0.7
    elif "next week" in lowered:
        when, confidence = _next_weekday(now, 0) + timedelta(days=7), 0.6

    if when is None:
        for name, index in WEEKDAYS.items():
            if re.search(rf"\b{name}\b", lowered):
                when, confidence = _next_weekday(now, index), 0.8
                break

    if when is None:
        match = _DAY_OF_MONTH.search(lowered)
        if match:
            when = _next_day_of_month(now, int(match.group(1)))
            confidence = 0.75 if when else 0.0

    if when is None:
        return None
    return {"promised_for": when, "amount_paise": None, "confidence": confidence, "source": "parser"}


def clamp_promise_date(when: Optional[datetime], now: datetime) -> Optional[datetime]:
    """Force any promise date -- however it was produced -- into a sane window.

    This is the boundary the language model cannot cross. A model that returns
    a date in 2099, in the past, or in the wrong century gets a date the policy
    considers a promise, or gets rejected.
    """
    if when is None:
        return None
    earliest = now + timedelta(hours=MIN_PROMISE_HORIZON_HOURS)
    latest = now + timedelta(days=MAX_PROMISE_HORIZON_DAYS)
    if when < now:
        return None
    return min(max(when, earliest), latest)


def default_promise_date(now: datetime) -> datetime:
    return now + timedelta(days=DEFAULT_PROMISE_DAYS)
