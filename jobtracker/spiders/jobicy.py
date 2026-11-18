"""Jobicy: documented public API for remote jobs (Method C, network/API).

Jobicy's rules: poll at most once per hour, keep Jobicy as the source and keep the canonical
link. Both are respected: the link stored is Jobicy's own URL, and a run that comes less than
an hour after the previous one skips this source.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlencode

from .base import SourceSpider

API_URL = "https://jobicy.com/api/v2/remote-jobs"
MIN_SECONDS_BETWEEN_CALLS = 3600


def parse_jobicy(data: dict) -> list[dict]:
    return [{"link": j.get("url"), "position": j.get("jobTitle"), "date": j.get("pubDate") or "",
             "company": j.get("companyName") or "", "location": j.get("jobGeo") or "Remote", "remote": True}
            for j in data.get("jobs") or []]


class JobicySpider(SourceSpider):
    name = "jobicy"

    def stamp(self) -> Path:
        return Path(self.cfg["paths"]["db"]).parent / "jobicy_last_call.txt"

    async def requests(self):
        try:
            age = time.time() - float(self.stamp().read_text())
        except (OSError, ValueError):
            age = MIN_SECONDS_BETWEEN_CALLS
        if age < MIN_SECONDS_BETWEEN_CALLS:
            self.log.info("Jobicy was queried less than an hour ago, skipped this time",
                          extra={"source": self.name})
            return
        self.stamp().parent.mkdir(parents=True, exist_ok=True)
        self.stamp().write_text(str(time.time()))
        for geo in self.scfg.get("geo") or ["europe"]:
            params = {"count": int(self.scfg.get("count", 100)), "geo": geo}
            if self.scfg.get("industry"):
                params["industry"] = self.scfg["industry"]
            yield self.req(f"{API_URL}?{urlencode(params)}", self.parse, api=True)

    def parse(self, response):
        for job in parse_jobicy(response.json()):
            yield self.posting(job["link"], job["position"], job["date"], job["company"], job["location"],
                               remote=True, apply_filter=True)
