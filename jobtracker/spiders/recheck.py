"""Re-check stored links and mark removed postings as 'no longer available'."""
from __future__ import annotations

import logging

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.spidermiddlewares.httperror import HttpError

from ..availability import classify
from ..store import Store

HANDLED = [400, 401, 403, 404, 407, 410, 429, 451, 500, 502, 503, 504, 999]


class RecheckSpider(scrapy.Spider):
    name = "recheck"

    def __init__(self, cfg: dict, **kwargs):
        super().__init__(**kwargs)
        self.cfg = cfg
        self.rcfg = cfg["recheck"]
        self.log = logging.getLogger("jobtracker.source.recheck")
        self.store: Store | None = None

    async def start(self):
        self.store = Store(self.cfg["paths"]["db"])
        links = self.store.links_to_check(self.rcfg["max_per_run"], self.rcfg["preserve_applied"])
        self.log.info("re-checking stored links", extra={"source": self.name, "links": len(links)})
        linkedin_opt_in = bool(self.cfg["sources"]["linkedin"].get("guest_search"))
        for link in links:
            meta = {"job_link": link, "handle_httpstatus_list": HANDLED}
            if linkedin_opt_in and "linkedin.com/" in link:
                meta["dont_obey_robotstxt"] = True  # same opt-in as the LinkedIn guest search
            yield scrapy.Request(link, callback=self.parse, errback=self.on_error,
                                 dont_filter=True, meta=meta)

    def closed(self, reason):
        if self.store is not None:
            self.store.close()

    def apply(self, link: str, outcome: str, detail: str = ""):
        self.crawler.stats.inc_value(f"jobtracker/check_{outcome}")
        changed = self.store.record_check(link, outcome, self.rcfg["unreachable_threshold"],
                                          self.rcfg["preserve_applied"])
        if changed:
            self.crawler.stats.inc_value("jobtracker/marked_gone")
            self.log.info("posting marked as no longer available",
                          extra={"source": self.name, "link": link, "reason": detail or outcome})

    def parse(self, response):
        link = response.meta["job_link"]
        text = ""
        if response.status == 200 and hasattr(response, "xpath"):
            text = " ".join(response.xpath(
                "//body//text()[not(ancestor::script) and not(ancestor::style)]").getall())
        outcome = classify(response.status, link, response.url, text, self.rcfg["closed_markers"])
        self.apply(link, outcome, f"HTTP {response.status}")

    def on_error(self, failure):
        link = failure.request.meta["job_link"]
        if failure.check(IgnoreRequest, HttpError):
            self.apply(link, "unknown")  # robots.txt or an unexpected status: no verdict
            return
        self.log.warning("stored link unreachable", extra={
            "source": self.name, "link": link,
            "error": f"{failure.type.__name__}: {failure.getErrorMessage()}"})
        self.apply(link, "unreachable", failure.type.__name__)
