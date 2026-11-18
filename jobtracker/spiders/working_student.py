"""Working student jobs: Arbeitnow filtered to student roles, plus configured boards."""
from __future__ import annotations

from urllib.parse import quote_plus

from ..ingest import any_term
from .arbeitnow import API_URL, ArbeitnowSpider


class WorkingStudentSpider(ArbeitnowSpider):
    name = "working_student"

    async def requests(self):
        if self.scfg.get("arbeitnow_filter", True):
            yield self.req(f"{API_URL}?page=1", self.parse, api=True, cb_kwargs={"page": 1})
        queries = self.cfg["search"]["queries"]
        locations = self.cfg["search"]["locations"] or [""]
        for board in self.scfg.get("boards") or []:
            template = board["url"]
            if "{query}" in template or "{location}" in template:
                urls = {template.replace("{query}", quote_plus(q)).replace("{location}", quote_plus(l))
                        for q in queries for l in locations}
            else:
                urls = {template}
            for url in sorted(urls):
                yield self.req(url, self.parse_board,
                               cb_kwargs={"board": board.get("name", url),
                                          "pattern": board.get("link_pattern")})

    def accept(self, job: dict) -> bool:
        return any_term(self.scfg.get("terms"), f"{job['position']} {job['labels']}")

    def parse_board(self, response, board: str, pattern):
        yield from self.extract_listing(response, company="", link_pattern=pattern)
