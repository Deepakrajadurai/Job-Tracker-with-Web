"""Shared path for every source: normalize, filter, dedupe, store."""
from __future__ import annotations

import logging
import re

from .dates import normalize_date
from .links import normalize_link

logger = logging.getLogger(__name__)


def _term_matches(term: str, text: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    if len(term) <= 6:  # short terms ("AI", "CAD", "Intern", "Lead") must stand alone as a word
        return re.search(rf"(?<![a-z0-9äöüß]){re.escape(term)}(?![a-z0-9äöüß])", text) is not None
    return term in text


def any_term(terms, text: str) -> bool:
    text = (text or "").lower()
    return any(_term_matches(t, text) for t in terms or [])


def title_excluded(title: str, filters: dict) -> bool:
    return any_term(filters.get("title_exclude_any"), title)


def title_wanted(title: str, filters: dict) -> bool:
    include = filters.get("title_include_any") or []
    require = filters.get("title_require_any") or []
    if include and not any_term(include, title):
        return False
    if require and not any_term(require, title):
        return False
    return True


def location_wanted(location: str, remote: bool, filters: dict) -> bool:
    terms = filters.get("location_match") or []
    if not terms or not (location or "").strip():
        return True
    if remote and filters.get("include_remote", True):
        return True
    low = location.lower()
    return any(t.lower() in low for t in terms)


def ingest(store, cfg: dict, raw: dict) -> str:
    """Store one raw posting. Returns 'new', 'duplicate', 'filtered' or 'invalid'.

    raw keys: link, position, date, source, company, location, remote,
    apply_filter (title include/require filter), filter_location.
    """
    filters = cfg.get("filters", {})
    source = raw.get("source", "")
    link = normalize_link(raw.get("link"))
    position = " ".join(str(raw.get("position") or "").split())
    if not link or not position:
        logger.debug("dropping posting without link or title", extra={"source": source})
        return "invalid"
    if title_excluded(position, filters):
        return "filtered"
    if raw.get("apply_filter") and not title_wanted(position, filters):
        return "filtered"
    if raw.get("filter_location") and not location_wanted(
        raw.get("location") or "", bool(raw.get("remote")), filters
    ):
        return "filtered"
    if store.get(link) is not None:
        return "duplicate"
    raw_date = raw.get("date")
    date_published = normalize_date(raw_date)
    if not date_published:
        if raw_date not in (None, ""):
            logger.warning("unparseable publication date, storing empty",
                           extra={"source": source, "link": link, "raw_date": str(raw_date)[:80]})
        else:
            logger.info("posting has no publication date, storing empty",
                        extra={"source": source, "link": link})
    added = store.add_job(link, position[:300], date_published, source=source,
                          company=str(raw.get("company") or "")[:200],
                          location=str(raw.get("location") or "")[:200])
    return "new" if added else "duplicate"
