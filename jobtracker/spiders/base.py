"""Common behaviour for all source spiders."""
from __future__ import annotations

import logging

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.spidermiddlewares.httperror import HttpError

from ..extract import extract_postings


REFUSED = {401, 403, 429, 451, 999}   # login wall, bot protection, rate limit


class SourceSpider(scrapy.Spider):
    """One isolated source. Subclasses yield plain dicts built with self.posting()."""

    def __init__(self, cfg: dict, pending_ai: list | None = None, pending_render: list | None = None, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.scfg = cfg["sources"].get(self.name, {})
        self.pending_ai = pending_ai if pending_ai is not None else []
        self.pending_render = pending_render
        self.log = logging.getLogger(f"jobtracker.source.{self.name}")
        self._robots_logged = False
        self._refusal_logged = False

    async def start(self):
        """Wraps requests() so an error while building requests is logged with the source name."""
        try:
            async for request in self.requests():
                yield request
        except Exception as exc:  # noqa: BLE001 - reported, the other sources keep running
            self.crawler.stats.inc_value("jobtracker/errors")
            self.log.error("source failed while creating requests",
                           extra={"source": self.name, "error": repr(exc)})

    async def requests(self):
        """Subclasses yield their start requests here."""
        return
        yield  # pragma: no cover - makes this an async generator

    # ---------------------------------------------------------------- helpers
    def req(self, url: str, callback, api: bool = False, **kwargs):
        """Build a request with the shared error handler. api=True marks a documented
        public API endpoint, which is meant for programs, so robots.txt is not consulted."""
        meta = kwargs.pop("meta", {}) or {}
        if api:
            meta["dont_obey_robotstxt"] = True
        return scrapy.Request(url, callback=callback, errback=self.on_error, meta=meta, **kwargs)

    def posting(self, link, position, date="", company="", location="", remote=False,
                apply_filter=False, filter_location=False) -> dict:
        self.crawler.stats.inc_value("jobtracker/seen")
        return {"source": self.name, "link": link, "position": position, "date": date,
                "company": company, "location": location, "remote": remote,
                "apply_filter": apply_filter, "filter_location": filter_location}

    def on_error(self, failure):
        request = getattr(failure, "request", None)
        if failure.check(IgnoreRequest) and not failure.check(HttpError):
            # The site's robots.txt forbids this page. That is a refusal, not a malfunction.
            self.crawler.stats.inc_value("jobtracker/robots_blocked")
            if not self._robots_logged:
                self._robots_logged = True
                self.log.warning("robots.txt forbids automated access, source skipped for these pages",
                                 extra={"source": self.name, "url": getattr(request, "url", "")})
            return
        status = getattr(getattr(failure.value, "response", None), "status", None)
        if failure.check(HttpError) and status in REFUSED:
            # The site answered, but refuses automated requests. Nothing is worked around.
            self.crawler.stats.inc_value("jobtracker/access_refused")
            self.crawler.stats.set_value("jobtracker/refused_status", status)
            if not self._refusal_logged:
                self._refusal_logged = True
                self.log.warning("site refused the automated request", extra={
                    "source": self.name, "url": getattr(request, "url", ""), "http_status": status})
            return
        self.crawler.stats.inc_value("jobtracker/errors")
        self.log.error("request failed", extra={
            "source": self.name, "url": getattr(request, "url", ""),
            "error": f"{failure.type.__name__}: {failure.getErrorMessage()}"})

    def extract_listing(self, response, company: str = "", link_pattern: str | None = None,
                        apply_filter: bool = False):
        """Method A on a listing page. Empty pages go to Method B (browser) when it is switched
        on, otherwise to the AI extraction."""
        jobs, anchors = extract_postings(response.text, response.url, self.cfg["filters"], link_pattern)
        for job in jobs:
            yield self.posting(job["link"], job["position"], job["date"], company=job["company"] or company,
                               location=job["location"], apply_filter=apply_filter)
        if jobs:
            return
        page = {"url": response.url, "source": self.name, "company": company,
                "link_pattern": link_pattern, "apply_filter": apply_filter, "anchors": anchors}
        if self.cfg.get("browser", {}).get("enabled") and self.pending_render is not None:
            self.pending_render.append(page)
            self.log.info("no postings in the static page, queued for browser rendering",
                          extra={"source": self.name, "url": response.url})
        elif anchors and not link_pattern:
            self.pending_ai.append(page)
            self.log.info("no structured postings found, queued page for AI extraction",
                          extra={"source": self.name, "url": response.url})
        else:
            self.log.warning("no postings found on page (layout changed or rendered by JavaScript)",
                             extra={"source": self.name, "url": response.url})
