"""Normalize publication dates to ISO 8601 (YYYY-MM-DD)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from dateutil import parser as du_parser

_GERMAN_MONTHS = {
    "januar": "january", "jänner": "january", "februar": "february", "märz": "march",
    "maerz": "march", "mai": "may", "juni": "june", "juli": "july", "oktober": "october",
    "dezember": "december", "mrz": "mar", "okt": "oct", "dez": "dec",
}
_UNITS = {
    "minute": 0, "min": 0, "hour": 0, "hr": 0, "stunde": 0, "sekunde": 0, "second": 0,
    "day": 1, "tag": 1, "week": 7, "woche": 7, "month": 30, "monat": 30, "year": 365, "jahr": 365,
}
_RELATIVE = re.compile(
    r"(\d+)\s*\+?\s*(sekunde|second|minute|min|stunde|hour|hr|tag|day|woche|week|monat|month|jahr|year)",
    re.IGNORECASE,
)
_DOTTED = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*$")
_TODAY = ("today", "heute", "just now", "just posted", "gerade", "soeben", "now")
_YESTERDAY = ("yesterday", "gestern")


def _valid(d: date, today: date) -> str:
    if d.year < 2000 or d > today + timedelta(days=366):
        return ""
    return d.isoformat()


def normalize_date(value, today: date | None = None) -> str:
    """Return YYYY-MM-DD, or "" when the value is missing or cannot be parsed."""
    today = today or date.today()
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _valid(value.date(), today)
    if isinstance(value, date):
        return _valid(value, today)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_epoch(float(value), today)
    text = str(value).strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{9,14}(\.\d+)?", text):
        return _from_epoch(float(text), today)
    low = text.lower()
    if any(low == t or low.startswith(t) for t in _TODAY):
        return today.isoformat()
    if any(t in low for t in _YESTERDAY):
        return (today - timedelta(days=1)).isoformat()
    if "ago" in low or low.startswith("vor ") or " vor " in low:
        match = _RELATIVE.search(low)
        if match:
            days = int(match.group(1)) * _UNITS[match.group(2).lower()]
            return _valid(today - timedelta(days=days), today)
        return ""
    dotted = _DOTTED.match(text)
    if dotted:
        day, month, year = (int(g) for g in dotted.groups())
        year += 2000 if year < 100 else 0
        try:
            return _valid(date(year, month, day), today)
        except ValueError:
            return ""
    try:
        return _valid(datetime.fromisoformat(text.replace("Z", "+00:00")).date(), today)
    except ValueError:
        pass
    if not re.search(r"\d", text):
        return ""
    for german, english in _GERMAN_MONTHS.items():
        low = re.sub(rf"\b{german}\b", english, low)
    low = re.sub(r"(\d{1,2})\.\s+(?=[a-z])", r"\1 ", low)  # "3. november" -> "3 november"
    try:
        parsed = du_parser.parse(low, dayfirst=True, default=datetime(today.year, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return ""
    return _valid(parsed.date(), today)


def _from_epoch(number: float, today: date) -> str:
    if number >= 1e11:  # milliseconds
        number /= 1000.0
    if number < 946684800:  # before 2000-01-01, not a plausible timestamp
        return ""
    try:
        return _valid(datetime.fromtimestamp(number, tz=timezone.utc).date(), today)
    except (OverflowError, OSError, ValueError):
        return ""
