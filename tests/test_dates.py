import logging
from datetime import date, datetime

import pytest

from jobtracker.dates import normalize_date
from jobtracker.ingest import ingest

TODAY = date(2026, 11, 14)


@pytest.mark.parametrize("raw, expected", [
    ("2026-11-03", "2026-11-03"),
    ("2026-11-03T08:15:00Z", "2026-11-03"),
    ("2026-11-03T08:15:00+01:00", "2026-11-03"),
    ("2026-11-03T08:15:00.000Z", "2026-11-03"),
    (1793779200, "2026-11-04"),            # epoch seconds (Arbeitnow)
    (1793779200000, "2026-11-04"),         # epoch milliseconds (Lever)
    ("1793779200", "2026-11-04"),
    ("03.11.2026", "2026-11-03"),
    ("3.11.26", "2026-11-03"),
    ("3. November 2026", "2026-11-03"),
    ("3. Dezember 2025", "2025-12-03"),
    ("November 3, 2026", "2026-11-03"),
    ("Nov 3, 2026", "2026-11-03"),
    ("3 days ago", "2026-11-11"),
    ("Posted 2 weeks ago", "2026-10-31"),
    ("30+ days ago", "2026-10-15"),
    ("vor 3 Tagen", "2026-11-11"),
    ("vor 5 Stunden", "2026-11-14"),
    ("today", "2026-11-14"),
    ("Heute", "2026-11-14"),
    ("Gestern", "2026-11-13"),
    (date(2026, 1, 2), "2026-01-02"),
    (datetime(2026, 1, 2, 10, 0), "2026-01-02"),
])
def test_dates_are_normalized_to_iso(raw, expected):
    assert normalize_date(raw, today=TODAY) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "soon", "n/a", "32.13.2026", "a while ago",
                                 "1890-01-01", "2090-01-01", 12, True])
def test_unparseable_dates_become_empty(raw):
    assert normalize_date(raw, today=TODAY) == ""


def test_unparseable_date_is_stored_empty_and_logged(store, cfg, caplog):
    with caplog.at_level(logging.WARNING, logger="jobtracker.ingest"):
        outcome = ingest(store, cfg, {"link": "https://example.com/j/9", "source": "test",
                                      "position": "Data Engineer", "date": "sometime"})
    assert outcome == "new"
    assert store.get("https://example.com/j/9")["date_published"] == ""
    record = next(r for r in caplog.records if "unparseable" in r.getMessage())
    assert record.raw_date == "sometime" and record.source == "test"
