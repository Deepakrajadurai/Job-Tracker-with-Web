"""Fallback source: job alert emails from LinkedIn, Indeed and Xing, read over IMAP.

Job alerts are something each site sends on request, so reading them is the
compliant way to cover the sources that block automated search.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlsplit

from parsel import Selector

logger = logging.getLogger(__name__)

SENDER_DOMAINS = {"linkedin": "linkedin.com", "indeed": "indeed.com", "xing": "xing.com"}
_GENERIC_TEXT = re.compile(
    r"^(view job|see job|apply( now)?|jetzt bewerben|job ansehen|zum job|view all jobs|"
    r"see all jobs|alle jobs anzeigen|mehr anzeigen|unsubscribe|abbestellen)\b", re.I)
_LINKEDIN = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})", re.I)
_XING = re.compile(r"xing\.com/jobs/([a-z0-9-]*-\d{5,})", re.I)
_INDEED_JK = re.compile(r"[?&]jk=([0-9a-f]{8,32})", re.I)


def imap_credentials(email_cfg: dict) -> tuple[str, str, str] | None:
    host = os.environ.get(email_cfg["host_env"], "")
    user = os.environ.get(email_cfg["user_env"], "")
    password = os.environ.get(email_cfg["password_env"], "")
    return (host, user, password) if host and user and password else None


def _unwrap(href: str) -> str:
    """Tracking redirects often carry the real URL in a query parameter."""
    candidates = [href, unquote(href)]
    try:
        for values in parse_qs(urlsplit(href).query).values():
            candidates.extend(unquote(v) for v in values if "http" in v)
    except ValueError:
        pass
    return " ".join(candidates)


def parse_alert(source: str, html: str, indeed_domain: str = "de.indeed.com") -> list[dict]:
    """Extract {link, position} pairs from one alert email body."""
    best: dict[str, str] = {}
    for anchor in Selector(text=html or "<html/>").css("a[href]"):
        blob = _unwrap(anchor.attrib.get("href", ""))
        link = ""
        if source == "linkedin":
            match = _LINKEDIN.search(blob)
            link = f"https://www.linkedin.com/jobs/view/{match.group(1)}" if match else ""
        elif source == "xing":
            match = _XING.search(blob)
            link = f"https://www.xing.com/jobs/{match.group(1)}" if match else ""
        elif source == "indeed":
            match = _INDEED_JK.search(blob) if "indeed." in blob else None
            link = f"https://{indeed_domain}/viewjob?jk={match.group(1)}" if match else ""
        if not link:
            continue
        text = " ".join(" ".join(anchor.css("::text").getall()).split())
        if _GENERIC_TEXT.match(text):
            text = ""
        best.setdefault(link, "")
        if not best[link] and 4 <= len(text) <= 160:  # the first labelled link is the title
            best[link] = text
    return [{"link": link, "position": title} for link, title in best.items() if title]


def _html_body(message) -> str:
    plain = ""
    for part in message.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html":
            return text
        plain = plain or text
    return plain


def fetch_alert_messages(host: str, user: str, password: str, folder: str, days_back: int,
                         sources, imap_factory=None) -> list[dict]:
    """Read recent alert emails (read-only, nothing is marked as seen)."""
    since = (date.today() - timedelta(days=max(1, int(days_back)))).strftime("%d-%b-%Y")
    messages = []
    conn = (imap_factory or imaplib.IMAP4_SSL)(host)
    try:
        conn.login(user, password)
        conn.select(folder, readonly=True)
        for source in sources:
            status, data = conn.search(None, f'(SINCE {since} FROM "{SENDER_DOMAINS[source]}")')
            if status != "OK":
                raise RuntimeError(f"IMAP search failed for {source}: {status}")
            for num in (data[0] or b"").split():
                status, parts = conn.fetch(num, "(RFC822)")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                message = email.message_from_bytes(parts[0][1])
                try:
                    sent = parsedate_to_datetime(message.get("Date")).date().isoformat()
                except (TypeError, ValueError):
                    sent = ""
                subject = str(make_header(decode_header(message.get("Subject", ""))))
                messages.append({"source": source, "date": sent, "subject": subject,
                                 "html": _html_body(message)})
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 - closing a broken connection must not mask the error
            pass
    return messages


def collect(cfg: dict, sources) -> dict[str, list[dict]]:
    """Return raw postings per source. Raises when IMAP is unusable so callers can log it."""
    creds = imap_credentials(cfg["email"])
    if creds is None:
        raise RuntimeError(
            "IMAP credentials are not set "
            f"({cfg['email']['host_env']}, {cfg['email']['user_env']}, {cfg['email']['password_env']})"
        )
    messages = fetch_alert_messages(*creds, cfg["email"]["folder"], cfg["email"]["days_back"], sources)
    out: dict[str, list[dict]] = {s: [] for s in sources}
    for message in messages:
        source = message["source"]
        domain = cfg["sources"]["indeed"].get("domain", "de.indeed.com")
        for job in parse_alert(source, message["html"], domain):
            # The email date is the best publication date an alert offers.
            out[source].append({**job, "date": message["date"], "source": source, "apply_filter": False})
    return out
