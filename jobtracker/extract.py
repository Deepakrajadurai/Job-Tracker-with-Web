"""Structured extraction helpers for listing pages (JSON-LD first, anchors second)."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

from parsel import Selector


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _first_text(value, *keys) -> str:
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and value:
            value = value[0].get(key) if isinstance(value[0], dict) else None
        else:
            return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return value if isinstance(value, str) else ""


def jsonld_jobs(html: str, base_url: str) -> list[dict]:
    """Return postings found in schema.org JobPosting JSON-LD blocks."""
    jobs = []
    for block in Selector(text=html or "<html/>").css('script[type="application/ld+json"]::text').getall():
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" not in types:
                continue
            title = node.get("title") or node.get("name") or ""
            url = node.get("url") or base_url
            if not isinstance(title, str) or not isinstance(url, str) or not title.strip():
                continue
            jobs.append({
                "link": urljoin(base_url, url),
                "position": title,
                "date": node.get("datePosted") or "",
                "company": _first_text(node, "hiringOrganization", "name"),
                "location": _first_text(node, "jobLocation", "address", "addressLocality"),
            })
    return jobs


def anchor_candidates(html: str, base_url: str, link_pattern: str | None = None, limit: int = 400) -> list[dict]:
    """Return unique {text, href} pairs for the links on a page."""
    pattern = re.compile(link_pattern) if link_pattern else None
    seen, out = set(), []
    for anchor in Selector(text=html or "<html/>").css("a[href]"):
        href = (anchor.attrib.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        if pattern and not pattern.search(urlsplit(url).path):
            continue
        heading = anchor.css("h1::text, h2::text, h3::text, h4::text").get()
        text = heading or anchor.attrib.get("aria-label") or " ".join(anchor.css("::text").getall())
        text = " ".join((text or "").split())
        if not (4 <= len(text) <= 160) or url in seen:
            continue
        seen.add(url)
        out.append({"text": text, "href": url})
        if len(out) >= limit:
            break
    return out


def extract_postings(html: str, url: str, filters: dict, link_pattern: str | None = None) -> tuple[list[dict], list[dict]]:
    """Method A on one listing page. Returns (postings, all link candidates).

    1. schema.org JobPosting JSON-LD.
    2. Links matching `link_pattern` (when the site's job URL shape is known).
    3. Links whose text passes the title filters.
    """
    from .ingest import title_excluded, title_wanted

    jobs = jsonld_jobs(html, url)
    if jobs:
        return jobs, []
    anchors = anchor_candidates(html, url, link_pattern)
    use_heuristic = bool(filters.get("title_include_any"))
    for anchor in anchors:
        if link_pattern or (use_heuristic and title_wanted(anchor["text"], filters)
                            and not title_excluded(anchor["text"], filters)):
            jobs.append({"link": anchor["href"], "position": anchor["text"], "date": "",
                         "company": "", "location": ""})
    return jobs, anchors
