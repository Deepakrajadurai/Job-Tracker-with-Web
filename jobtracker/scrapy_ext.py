"""Scrapy glue: settings, retry with exponential backoff, storage pipeline."""
from __future__ import annotations

import asyncio
import logging
import random

from scrapy import Request
from scrapy.downloadermiddlewares.retry import RetryMiddleware

from .ingest import ingest
from .store import Store

logger = logging.getLogger(__name__)


def backoff_delay(retry_number: int, base: float, maximum: float, jitter: float = 0.0) -> float:
    """Delay before retry N (1-based): base * 2^(N-1), capped, plus optional jitter."""
    delay = min(float(maximum), float(base) * (2 ** max(0, retry_number - 1)))
    return delay + (random.uniform(0, jitter) if jitter else 0.0)


class BackoffRetryMiddleware(RetryMiddleware):
    """Scrapy's retry middleware, but each retry waits exponentially longer."""

    def __init__(self, settings):
        super().__init__(settings)
        self.backoff_base = settings.getfloat("RETRY_BACKOFF_BASE", 2.0)
        self.backoff_max = settings.getfloat("RETRY_BACKOFF_MAX", 60.0)

    async def _wait(self, result, response=None):
        if isinstance(result, Request):
            delay = backoff_delay(result.meta.get("retry_times", 1), self.backoff_base,
                                  self.backoff_max, jitter=1.0)
            if response is not None:
                try:
                    retry_after = float(response.headers.get("Retry-After", b"").decode() or 0)
                    delay = max(delay, min(retry_after, self.backoff_max))
                except ValueError:
                    pass
            logger.info("retrying after backoff",
                        extra={"url": result.url, "delay": round(delay, 1),
                               "retry": result.meta.get("retry_times")})
            await asyncio.sleep(delay)
        return result

    async def process_response(self, request, response):
        return await self._wait(super().process_response(request, response), response)

    async def process_exception(self, request, exception):
        return await self._wait(super().process_exception(request, exception))


class StorePipeline:
    """Normalize, filter, dedupe and store every scraped posting."""

    def __init__(self, crawler):
        self.crawler = crawler
        self.cfg = crawler.settings.get("JOBTRACKER_CFG")
        self.store: Store | None = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def open_spider(self):
        self.store = Store(self.cfg["paths"]["db"])

    def close_spider(self):
        if self.store is not None:
            self.store.close()

    def process_item(self, item):
        outcome = ingest(self.store, self.cfg, dict(item))
        self.crawler.stats.inc_value(f"jobtracker/{outcome}")
        return item


def build_settings(cfg: dict) -> dict:
    s = cfg["scraping"]
    return {
        "BOT_NAME": "jobtracker",
        "JOBTRACKER_CFG": cfg,
        "USER_AGENT": s["user_agent"],
        "ROBOTSTXT_OBEY": True,
        "COOKIES_ENABLED": False,
        "TELNETCONSOLE_ENABLED": False,
        "MEMUSAGE_ENABLED": False,   # not available on Windows, not needed here
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        },
        # Polite crawling
        "DOWNLOAD_DELAY": s["download_delay"],
        "CONCURRENT_REQUESTS": 8,
        "CONCURRENT_REQUESTS_PER_DOMAIN": s["concurrency_per_domain"],
        "DOWNLOAD_TIMEOUT": s["timeout"],
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": s["download_delay"],
        "AUTOTHROTTLE_MAX_DELAY": s["autothrottle_max_delay"],
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        # Retries with exponential backoff
        "RETRY_ENABLED": True,
        "RETRY_TIMES": s["retry_times"],
        "RETRY_HTTP_CODES": [408, 429, 500, 502, 503, 504, 522, 524],
        "RETRY_BACKOFF_BASE": s["backoff_base"],
        "RETRY_BACKOFF_MAX": s["backoff_max"],
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
            "jobtracker.scrapy_ext.BackoffRetryMiddleware": 550,
        },
        "ITEM_PIPELINES": {"jobtracker.scrapy_ext.StorePipeline": 300},
        # Logging is configured by jobtracker.logging_setup, Scrapy must not add its own handler.
        "LOG_INSTALL_ROOT_HANDLER": False,
        "LOG_LEVEL": cfg["logging"]["scrapy_level"],
    }
