"""Decide whether a stored posting is still online."""
from __future__ import annotations

from urllib.parse import urlsplit

BLOCKED_STATUSES = {401, 403, 407, 429, 451, 999}


def classify(status: int, original_url: str, final_url: str, text: str, markers) -> str:
    """Return 'available', 'gone' or 'unknown' for a completed HTTP response.

    'unknown' means the site refused to tell us (blocked, rate limited, server error),
    so the stored status must stay as it is.
    """
    if status in (404, 410):
        return "gone"
    if status in BLOCKED_STATUSES or status >= 500:
        return "unknown"
    if status != 200:
        return "unknown"
    final = urlsplit(final_url or original_url)
    original = urlsplit(original_url)
    final_low = (final_url or "").lower()
    if any(part in final_low for part in ("authwall", "/login", "/signin", "/checkpoint")):
        return "unknown"
    if "error=true" in (final.query or ""):  # Greenhouse redirect for closed jobs
        return "gone"
    if original.path not in ("", "/") and final.path in ("", "/") and not final.query:
        return "gone"  # redirected to the homepage
    low = (text or "").lower()
    if any(marker.lower() in low for marker in markers or []):
        return "gone"
    return "available"
