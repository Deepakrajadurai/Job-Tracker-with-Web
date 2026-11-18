"""Company career pages: public ATS APIs where possible, generic HTML otherwise."""
from __future__ import annotations

from urllib.parse import urlencode

from ..links import detect_ats
from ..store import Store, company_key
from .base import SourceSpider


def parse_greenhouse(data: dict) -> list[dict]:
    return [{"link": j.get("absolute_url"), "position": j.get("title"),
             "date": j.get("first_published") or j.get("updated_at") or "",
             "location": (j.get("location") or {}).get("name") or ""}
            for j in data.get("jobs") or []]


def parse_lever(data: list) -> list[dict]:
    out = []
    for j in data if isinstance(data, list) else []:
        categories = j.get("categories") or {}
        out.append({"link": j.get("hostedUrl"), "position": j.get("text"),
                    "date": j.get("createdAt") or "", "location": categories.get("location") or "",
                    "remote": (j.get("workplaceType") or "").lower() == "remote"})
    return out


def parse_ashby(data: dict) -> list[dict]:
    return [{"link": j.get("jobUrl"), "position": j.get("title"), "date": j.get("publishedAt") or "",
             "location": j.get("location") or "", "remote": bool(j.get("isRemote"))}
            for j in data.get("jobs") or [] if j.get("isListed", True)]


def parse_smartrecruiters(data: dict, slug: str) -> list[dict]:
    out = []
    for j in data.get("content") or []:
        location = j.get("location") or {}
        company = (j.get("company") or {}).get("identifier") or slug
        out.append({"link": f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}",
                    "position": j.get("name"), "date": j.get("releasedDate") or "",
                    "location": ", ".join(v for v in (location.get("city"), location.get("country")) if v),
                    "remote": bool(location.get("remote"))})
    return out


def parse_recruitee(data: dict) -> list[dict]:
    out = []
    for o in data.get("offers") or []:
        place = o.get("location") or ", ".join(v for v in (o.get("city"), o.get("country")) if v)
        out.append({"link": o.get("careers_url"), "position": o.get("title"),
                    "date": o.get("published_at") or o.get("created_at") or "",
                    "location": place, "remote": bool(o.get("remote"))})
    return out


def parse_workable(data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs") or []:
        place = j.get("location") if isinstance(j.get("location"), dict) else {}
        city, country = j.get("city") or place.get("city"), j.get("country") or place.get("country")
        out.append({"link": j.get("url") or j.get("shortlink") or j.get("application_url"),
                    "position": j.get("title"),
                    "date": j.get("published_on") or j.get("published") or j.get("created_at") or "",
                    "location": ", ".join(v for v in (city, country) if v),
                    "remote": bool(j.get("telecommuting") or j.get("remote"))})
    return out


def parse_personio(selector, base_url: str) -> list[dict]:
    out = []
    for pos in selector.xpath("//position"):
        job_id = pos.xpath("./id/text()").get()
        if not job_id:
            continue
        out.append({"link": f"{base_url}/job/{job_id}", "position": pos.xpath("./name/text()").get(),
                    "date": pos.xpath("./createdAt/text()").get() or "",
                    "location": pos.xpath("./office/text()").get() or ""})
    return out


class CareerPagesSpider(SourceSpider):
    """Crawls the companies selected for this run and records each visit in the pool,
    so the rotation knows when a company was last crawled."""

    name = "career_pages"

    def __init__(self, cfg, companies=None, **kwargs):
        super().__init__(cfg, **kwargs)
        self.companies = list(companies if companies is not None else self.scfg.get("companies") or [])
        self.store: Store | None = None

    async def requests(self):
        self.store = Store(self.cfg["paths"]["db"])
        seen = set()
        for company in self.companies:
            name = company.get("name") or company.get("slug") or company.get("url")
            key = company.get("key") or company_key(company)
            ats, slug = company.get("ats"), company.get("slug")
            if not ats and company.get("url"):
                detected = detect_ats(company["url"])
                if detected:
                    ats, slug = detected
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                request = self.build_request(name, key, ats, slug, company)
            except Exception as exc:  # noqa: BLE001 - one bad entry must not stop the others
                self.crawler.stats.inc_value("jobtracker/errors")
                self.mark(key, False)
                self.log.error("bad career page entry", extra={
                    "source": self.name, "company": name, "error": repr(exc)})
                continue
            self.crawler.stats.inc_value("jobtracker/companies_requested")
            yield request

    def closed(self, reason):
        if self.store is not None:
            self.store.close()

    def mark(self, key, ok: bool, postings: int = 0):
        if self.store is not None and key:
            self.store.mark_company(key, ok, postings)
            self.crawler.stats.inc_value("jobtracker/companies_ok" if ok else "jobtracker/companies_failed")

    def on_error(self, failure):
        request = getattr(failure, "request", None)
        if request is not None and request.cb_kwargs.get("offset", 0) == 0:
            self.mark(request.cb_kwargs.get("key"), False)
        super().on_error(failure)

    def build_request(self, name, key, ats, slug, company):
        kw = {"company": name, "key": key}
        params = company.get("params") or {}
        if ats == "greenhouse":
            return self.req(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                            self.parse_api, api=True, cb_kwargs={**kw, "ats": ats, "slug": slug})
        if ats in ("lever", "lever-eu"):
            host = "api.eu.lever.co" if ats == "lever-eu" else "api.lever.co"
            return self.req(f"https://{host}/v0/postings/{slug}?mode=json",
                            self.parse_api, api=True, cb_kwargs={**kw, "ats": "lever", "slug": slug})
        if ats == "ashby":
            return self.req(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                            self.parse_api, api=True, cb_kwargs={**kw, "ats": ats, "slug": slug})
        if ats == "smartrecruiters":
            query = urlencode({"limit": 100, "offset": 0, **params})
            return self.req(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?{query}",
                            self.parse_api, api=True,
                            cb_kwargs={**kw, "ats": ats, "slug": slug, "offset": 0, "params": params})
        if ats == "recruitee":
            return self.req(f"https://{slug}.recruitee.com/api/offers/",
                            self.parse_api, api=True, cb_kwargs={**kw, "ats": ats, "slug": slug})
        if ats == "workable":
            return self.req(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                            self.parse_api, api=True, cb_kwargs={**kw, "ats": ats, "slug": slug})
        if ats == "personio":
            return self.req(f"https://{slug}.jobs.personio.de/xml", self.parse_personio,
                            api=True, cb_kwargs=kw)
        if ats:
            raise ValueError(f"unknown ats {ats!r}")
        return self.req(company["url"], self.parse_generic, cb_kwargs=kw)

    def emit(self, jobs, company):
        for job in jobs:
            yield self.posting(job.get("link"), job.get("position"), job.get("date"), company,
                               job.get("location", ""), job.get("remote", False),
                               apply_filter=True, filter_location=True)

    def parse_api(self, response, company, key, ats, slug, offset=0, params=None):
        data = response.json()
        if ats == "greenhouse":
            jobs = parse_greenhouse(data)
        elif ats == "lever":
            jobs = parse_lever(data)
        elif ats == "ashby":
            jobs = parse_ashby(data)
        elif ats == "recruitee":
            jobs = parse_recruitee(data)
        elif ats == "workable":
            jobs = parse_workable(data)
        else:
            jobs = parse_smartrecruiters(data, slug)
        if offset == 0:
            self.mark(key, True, len(jobs))
        self.log.info("career page fetched", extra={
            "source": self.name, "company": company, "ats": ats, "postings": len(jobs)})
        yield from self.emit(jobs, company)
        if ats == "smartrecruiters":
            nxt = offset + 100
            max_pages = int(self.scfg.get("max_pages_per_company", 3))
            if nxt < int(data.get("totalFound") or 0) and nxt < max_pages * 100:
                query = urlencode({"limit": 100, "offset": nxt, **(params or {})})
                yield self.req(
                    f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?{query}",
                    self.parse_api, api=True,
                    cb_kwargs={"company": company, "key": key, "ats": ats, "slug": slug,
                               "offset": nxt, "params": params})

    def parse_personio(self, response, company, key):
        jobs = parse_personio(response, response.url.rsplit("/xml", 1)[0])
        self.mark(key, True, len(jobs))
        yield from self.emit(jobs, company)

    def parse_generic(self, response, company, key):
        found = list(self.extract_listing(response, company=company, apply_filter=True))
        self.mark(key, True, len(found))
        self.log.info("career page fetched", extra={
            "source": self.name, "company": company, "ats": "html", "postings": len(found)})
        yield from found
