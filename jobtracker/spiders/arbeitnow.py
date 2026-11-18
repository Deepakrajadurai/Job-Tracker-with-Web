"""Arbeitnow: official public job board API (no key needed)."""
from __future__ import annotations

from .base import SourceSpider

API_URL = "https://www.arbeitnow.com/api/job-board-api"


def parse_arbeitnow_job(job: dict) -> dict:
    return {
        "link": job.get("url") or "",
        "position": job.get("title") or "",
        "date": job.get("created_at"),  # unix timestamp
        "company": job.get("company_name") or "",
        "location": job.get("location") or "",
        "remote": bool(job.get("remote")),
        "labels": " ".join(str(v) for v in (job.get("job_types") or []) + (job.get("tags") or [])),
    }


class ArbeitnowSpider(SourceSpider):
    name = "arbeitnow"

    async def requests(self):
        yield self.req(f"{API_URL}?page=1", self.parse, api=True, cb_kwargs={"page": 1})

    def accept(self, job: dict) -> bool:
        return True

    def parse(self, response, page: int):
        data = response.json()
        jobs = data.get("data") or []
        for raw in jobs:
            job = parse_arbeitnow_job(raw)
            if self.accept(job):
                yield self.posting(job["link"], job["position"], job["date"], job["company"],
                                   job["location"], job["remote"], apply_filter=True,
                                   filter_location=bool(self.scfg.get("filter_location")))
        has_next = bool((data.get("links") or {}).get("next"))
        if jobs and has_next and page < int(self.scfg.get("max_pages", 5)):
            yield self.req(f"{API_URL}?page={page + 1}", self.parse, api=True,
                           cb_kwargs={"page": page + 1})
