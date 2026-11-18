"""Xing public job search page. Runs only as far as Xing's robots.txt allows."""
from __future__ import annotations

from urllib.parse import urlencode

from .base import SourceSpider

SEARCH_URL = "https://www.xing.com/jobs/search"
JOB_LINK_PATTERN = r"^/jobs/[a-z0-9-]+-\d{5,}$"


class XingSpider(SourceSpider):
    name = "xing"

    async def requests(self):
        for query in self.cfg["search"]["queries"]:
            for location in self.cfg["search"]["locations"] or [""]:
                url = f"{SEARCH_URL}?{urlencode({'keywords': query, 'location': location})}"
                yield self.req(url, self.parse)

    def parse(self, response):
        yield from self.extract_listing(response, link_pattern=JOB_LINK_PATTERN)
