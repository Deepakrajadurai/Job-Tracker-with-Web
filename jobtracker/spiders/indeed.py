"""Indeed public search page (Method A, HTTP + HTML). Opt-in.

Indeed's robots.txt is obeyed and no bot protection is worked around. When Indeed refuses the
request, the source is reported as blocked and job alert emails remain the working method.
"""
from __future__ import annotations

from urllib.parse import urlencode

from .base import SourceSpider


def parse_indeed_cards(selector, domain: str) -> list[dict]:
    jobs, seen = [], set()
    for anchor in selector.css("a[data-jk]"):
        jk = anchor.attrib.get("data-jk")
        title = anchor.css("span::attr(title)").get() or " ".join(anchor.css("::text").getall())
        title = " ".join((title or "").split())
        if not jk or not title or jk in seen:
            continue
        seen.add(jk)
        jobs.append({"link": f"https://{domain}/viewjob?jk={jk}", "position": title})
    return jobs


class IndeedSpider(SourceSpider):
    name = "indeed"

    async def requests(self):
        domain = self.scfg.get("domain", "de.indeed.com")
        for query in self.cfg["search"]["queries"]:
            for location in self.cfg["search"]["locations"] or [""]:
                params = {"q": query, "l": location, "fromage": int(self.scfg.get("max_age_days", 7))}
                yield self.req(f"https://{domain}/jobs?{urlencode(params)}", self.parse)

    def parse(self, response):
        cards = parse_indeed_cards(response, self.scfg.get("domain", "de.indeed.com"))
        for job in cards:
            yield self.posting(job["link"], job["position"])
        if not cards:
            yield from self.extract_listing(response, link_pattern=r"^/(viewjob|rc/clk)")
