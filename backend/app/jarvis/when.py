"""Resolve the time phrases the model was trained to pass into the user's zone.

The phrases are the ones in the training data ("tomorrow at 10", "next Monday",
"this weekend") plus ISO 8601.

Deliberately small and strict: a phrase it does not understand raises
:class:`WhenError`, which the tool turns into ``invalid_arguments`` so the model
asks the user. Guessing a date would book the wrong meeting.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

__all__ = ["WhenError", "parse_day", "parse_instant", "parse_window"]

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_PARTS = {
    "morning": (9, 12),
    "afternoon": (12, 17),
    "evening": (17, 23),
    "tonight": (17, 24),
    "night": (17, 24),
}
_CLOCK = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")


class WhenError(ValueError):
    """The phrase is not one this parser can resolve unambiguously."""


def _day_phrase(text: str, today: date) -> tuple[date, date] | None:
    """Return the first and last day the phrase names, or None."""
    t = text
    if re.search(r"\btoday\b|\btonight\b|\bthis (morning|afternoon|evening)\b", t):
        return today, today
    if "day after tomorrow" in t:
        d = today + timedelta(days=2)
        return d, d
    if re.search(r"\btomorrow\b", t):
        d = today + timedelta(days=1)
        return d, d
    m = re.search(r"\bin (\d{1,3}|one|two|three|four|five|six|seven) days?\b", t)
    if m:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        d = today + timedelta(days=int(words.get(m.group(1), m.group(1))))
        return d, d
    if re.search(r"\bthis weekend\b", t):
        sat = today + timedelta(days=(5 - today.weekday()) % 7)
        return sat, sat + timedelta(days=1)
    if re.search(r"\bnext week\b", t):
        mon = today + timedelta(days=7 - today.weekday())
        return mon, mon + timedelta(days=6)
    if re.search(r"\bthis week\b", t):
        return today, today + timedelta(days=6 - today.weekday())
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}\b", t):
            ahead = (i - today.weekday()) % 7
            if "next" in t and ahead == 0:
                ahead = 7
            d = today + timedelta(days=ahead)
            return d, d
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError as exc:
            raise WhenError(f"{m.group(1)!r} is not a real date") from exc
        return d, d
    return None


def _clock(text: str) -> time | None:
    rest = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    if "first thing" in rest:
        return time(9, 0)
    if "noon" in rest or "midday" in rest:
        return time(12, 0)
    m = _CLOCK.search(rest)
    if not m or not (m.group(3) or "at" in m.group(0) or m.group(2)):
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and hour < 12:
        hour += 12
    if m.group(3) == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise WhenError(f"{m.group(0).strip()!r} is not a valid time of day")
    return time(hour, minute)


def _iso(text: str, tz: ZoneInfo) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if "T" not in text and " " not in text.strip():
        return None  # a bare date is a day, not an instant
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def parse_instant(text: str, tz: ZoneInfo, now: datetime) -> datetime:
    """A start time for an event. A day without a time of day is refused."""
    iso = _iso(text, tz)
    if iso:
        return iso
    t = text.lower().strip()
    days = _day_phrase(t, now.astimezone(tz).date())
    clock = _clock(t)
    if clock is None:
        part = next((p for p in _PARTS if re.search(rf"\b{p}\b", t)), None)
        if part:
            clock = time(_PARTS[part][0], 0)
    if days is None or clock is None or days[0] != days[1]:
        raise WhenError(
            f"could not resolve {text!r} to one date and time; give e.g. 'tomorrow at 10' or 2026-10-14T10:00"
        )
    return datetime.combine(days[0], clock, tzinfo=tz)


def parse_day(text: str, tz: ZoneInfo, now: datetime) -> date:
    """A due date for a task."""
    t = text.lower().strip()
    days = _day_phrase(t, now.astimezone(tz).date())
    if days is None:
        raise WhenError(
            f"could not resolve {text!r} to a date; give e.g. 'tomorrow', 'next Monday' or 2026-10-14"
        )
    return days[1] if "week" in t else days[0]


def parse_window(text: str, tz: ZoneInfo, now: datetime) -> tuple[datetime, datetime]:
    """A half-open [start, end) window for reading the calendar."""
    t = text.lower().strip()
    days = _day_phrase(t, now.astimezone(tz).date())
    if days is None:
        raise WhenError(
            f"could not resolve {text!r} to a calendar range; give e.g. 'today', 'this week' or 2026-10-14"
        )
    start = datetime.combine(days[0], time(0), tzinfo=tz)
    end = datetime.combine(days[1] + timedelta(days=1), time(0), tzinfo=tz)
    if days[0] == days[1]:
        clock = _clock(t)
        part = next((p for p in _PARTS if re.search(rf"\b{p}\b", t)), None)
        if clock is not None:
            start = datetime.combine(days[0], clock, tzinfo=tz)
            end = start + timedelta(hours=1)
        elif part:
            lo, hi = _PARTS[part]
            start = datetime.combine(days[0], time(lo), tzinfo=tz)
            end = datetime.combine(days[0], time(0), tzinfo=tz) + timedelta(hours=hi)
    return start, end
