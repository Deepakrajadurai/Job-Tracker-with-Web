"""LinkedIn public (logged-out) job search. Opt-in, see README known limitations."""
from __future__ import annotations

from urllib.parse import urlencode

from .base import SourceSpider

GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def parse_linkedin_cards(selector) -> list[dict]:
    jobs = []
    for card in selector.css("li"):
        link = (card.css("a.base-card__full-link::attr(href)").get()
                or card.css('a[href*="/jobs/view/"]::attr(href)').get())
        title = (card.css("h3.base-search-card__title::text").get()
                 or card.css("a.base-card__full-link span::text").get() or "")
        if not link or not title.strip():
            continue
        jobs.append({
            "link": link,
            "position": title.strip(),
            "date": card.css("time::attr(datetime)").get() or "",
            "company": " ".join(card.css("h4.base-search-card__subtitle ::text").getall()).strip(),
            "location": (card.css("span.job-search-card__location::text").get() or "").strip(),
        })
    return jobs


class LinkedInSpider(SourceSpider):
    name = "linkedin"

    def search_url(self, query: str, location: str, start: int) -> str:
        params = {"keywords": query, "location": location, "start": start,
                  "f_TPR": f"r{int(self.scfg.get('max_age_days', 7)) * 86400}"}
        return f"{GUEST_URL}?{urlencode(params)}"

    async def requests(self):
        for query in self.cfg["search"]["queries"]:
            for location in self.cfg["search"]["locations"] or [""]:
                # LinkedIn's robots.txt disallows all automated access, so this endpoint only
                # works with the robots check off. It is used only when the user opted in.
                yield self.req(self.search_url(query, location, 0), self.parse, api=True,
                               cb_kwargs={"query": query, "location": location, "start": 0, "page": 1})

    def parse(self, response, query, location, start, page):
        cards = parse_linkedin_cards(response)
        for job in cards:
            yield self.posting(job["link"], job["position"], job["date"], job["company"], job["location"])
        if not cards and page == 1:
            self.log.warning("LinkedIn returned no job cards (blocked, no results, or layout changed)",
                             extra={"source": self.name, "query": query, "location": location})
        if cards and page < int(self.scfg.get("max_pages", 2)):
            nxt = start + len(cards)
            yield self.req(self.search_url(query, location, nxt), self.parse, api=True,
                           cb_kwargs={"query": query, "location": location, "start": nxt, "page": page + 1})
