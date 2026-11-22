"""End-to-end run through real Scrapy with a fake network.

Covers: a failing source does not abort the run, deduplication, status preservation and the
'no longer available' transition across a full run. Twisted's reactor starts once per process,
so everything that needs a crawl lives in this single test.
"""
import csv
import json

from scrapy.http import HtmlResponse, TextResponse

from jobtracker import STATUS_APPLIED, STATUS_GONE, STATUS_NOT_APPLIED
from jobtracker.run import run
from jobtracker.spiders.arbeitnow import API_URL, ArbeitnowSpider
from jobtracker.spiders.arbeitsagentur import ArbeitsagenturSpider
from jobtracker.spiders.base import SourceSpider
from jobtracker.spiders.career_pages import CareerPagesSpider
from jobtracker.spiders.recheck import RecheckSpider
from jobtracker.store import Store

KEPT = "https://jobs.acme.example/ml-engineer-1"       # stored, applied, still listed
NEW = "https://jobs.acme.example/data-werkstudent-2"   # new in this run
REMOVED = "https://old.example.com/jobs/removed"                            # stored, now 404
APPLIED_REMOVED = "https://old.example.com/jobs/applied-and-removed"        # applied, now 404
BLOCKED = "https://blocked.example.com/jobs/1"                              # site answers 403

FEED = {"data": [
    {"title": "Machine Learning Engineer", "url": KEPT + "?utm_source=feed", "company_name": "Acme",
     "location": "Berlin", "remote": False, "created_at": 1793779200, "tags": [], "job_types": []},
    {"title": "Werkstudent Data Science", "url": NEW, "company_name": "Acme", "location": "Erfurt",
     "remote": False, "created_at": "not a date", "tags": [], "job_types": []},
    {"title": "Senior Data Architect", "url": "https://www.arbeitnow.com/jobs/x/senior-3",
     "company_name": "Acme", "location": "Berlin", "remote": False, "created_at": 1793779200},
], "links": {"next": None}, "meta": {}}


GREENHOUSE = {"jobs": [
    {"absolute_url": "https://boards.greenhouse.io/acme/jobs/11", "title": "Data Engineer",
     "first_published": "2026-11-09T10:00:00-05:00", "location": {"name": "Berlin, Germany"}},
    {"absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "title": "Data Engineer",
     "updated_at": "2026-11-09", "location": {"name": "Austin, TX"}},
]}


ARBEITSAGENTUR = {"maxErgebnisse": 1, "ergebnisliste": [
    {"stellenangebotsTitel": "Werkstudent (m/w/d) Data Engineering", "referenznummer": "10001-1002716922-S",
     "firma": "Beispiel AG", "veroeffentlichungszeitraum": {"von": "2026-11-12"},
     "stellenlokationen": [{"adresse": {"ort": "Erfurt", "region": "Thüringen", "plz": "99084"}}]}]}


class FakeNetwork:
    """Downloader middleware that answers every request locally. Unknown URLs fail loudly."""

    requested: list[str] = []
    ba_queries: list[str] = []

    def process_request(self, request):
        url = request.url
        FakeNetwork.requested.append(url)
        if url == "https://noscrape.example.com/robots.txt":
            return TextResponse(url, body=b"User-agent: *\nDisallow: /", request=request)
        if url.endswith("/robots.txt"):
            return TextResponse(url, status=404, request=request)
        if url == "https://refuse.example.com/jobs?q=x":
            return HtmlResponse(url, status=403, request=request)      # bot protection
        if url.startswith("https://rest.arbeitsagentur.de/") and "/pc/v4/" in url:
            return TextResponse(url, status=403, body=b" ", request=request)   # retired path, as live
        if url.startswith("https://rest.arbeitsagentur.de/"):
            assert request.headers.get("X-API-Key") == b"jobboerse-jobsuche"
            FakeNetwork.ba_queries.append(url.split("?", 1)[1])
            return TextResponse(url, body=json.dumps(ARBEITSAGENTUR).encode(), encoding="utf-8", request=request)
        if url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs":
            return TextResponse(url, body=json.dumps(GREENHOUSE).encode(), encoding="utf-8", request=request)
        if url.startswith("https://api.lever.co/v0/postings/gone"):
            return TextResponse(url, status=404, request=request)
        if url.startswith(API_URL) and FakeNetwork.requested.count(url) == 1:
            return TextResponse(url, status=503, request=request)   # first try fails, retry succeeds
        if url.startswith(API_URL):
            return TextResponse(url, body=json.dumps(FEED).encode(), encoding="utf-8", request=request,
                                headers={"Content-Type": "application/json"})
        if url in (REMOVED, APPLIED_REMOVED):
            return HtmlResponse(url, status=404, request=request)
        if url == BLOCKED:
            return HtmlResponse(url, status=403, request=request)
        if url == KEPT:
            return HtmlResponse(url, body=b"<html><body>Apply now</body></html>", request=request)
        if url == "https://boom.example.com/page":
            return HtmlResponse(url, body=b"<html></html>", request=request)
        raise AssertionError(f"unexpected network access in test: {url}")


class CrashOnInit(SourceSpider):
    name = "crash_on_init"

    def __init__(self, *args, **kwargs):
        raise RuntimeError("cannot even be created")


class CrashOnStart(SourceSpider):
    name = "crash_on_start"

    async def requests(self):
        raise RuntimeError("config for this source is broken")
        yield


class RobotsBlocked(SourceSpider):
    name = "robots_blocked"

    async def requests(self):
        yield self.req("https://noscrape.example.com/jobs/search?q=x", self.parse)

    def parse(self, response):
        raise AssertionError("must never be fetched")


class Refused(SourceSpider):
    name = "refused"

    async def requests(self):
        yield self.req("https://refuse.example.com/jobs?q=x", self.parse)

    def parse(self, response):
        raise AssertionError("a refused page must not be parsed")


class CrashOnParse(SourceSpider):
    name = "crash_on_parse"

    async def requests(self):
        yield self.req("https://boom.example.com/page", self.parse)

    def parse(self, response):
        raise ValueError("site changed its layout")


def test_full_run_with_failing_sources(cfg, caplog):
    seed = Store(cfg["paths"]["db"])
    seed.add_job(KEPT, "Machine Learning Engineer", "2026-11-01", status=STATUS_APPLIED)
    seed.add_job(REMOVED, "Data Engineer", "2026-10-01")
    seed.add_job(APPLIED_REMOVED, "AI Engineer", "2026-10-02", status=STATUS_APPLIED)
    seed.add_job(BLOCKED, "Data Analyst", "2026-10-03")
    seed.close()
    cfg["recheck"]["preserve_applied"] = False   # re-check applied rows too in this scenario
    base = {"cfg": cfg, "pending_ai": []}
    seed = Store(cfg["paths"]["db"])
    companies = [{"name": "Acme", "ats": "greenhouse", "slug": "acme"},
                 {"name": "Gone Inc", "ats": "lever", "slug": "gone"}]
    for company in companies:
        company["key"] = seed.upsert_company(company)
    seed.close()
    plan = [
        ("crash_on_init", CrashOnInit, base),
        ("crash_on_start", CrashOnStart, base),
        ("crash_on_parse", CrashOnParse, base),
        ("arbeitnow", ArbeitnowSpider, base),
        ("robots_blocked", RobotsBlocked, base),
        ("refused", Refused, base),
        ("arbeitsagentur", ArbeitsagenturSpider, base),
        ("career_pages", CareerPagesSpider, {**base, "companies": companies}),
        ("recheck", RecheckSpider, {"cfg": cfg}),
    ]
    overrides = {
        "DOWNLOAD_DELAY": 0, "AUTOTHROTTLE_ENABLED": False, "RETRY_BACKOFF_BASE": 0.01,
        "DOWNLOADER_MIDDLEWARES": {
            "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
            "jobtracker.scrapy_ext.BackoffRetryMiddleware": 550,
            "tests.test_run_isolation.FakeNetwork": 900,
        },
    }
    with caplog.at_level("INFO"):
        results = run(cfg, plan_override=plan, settings_overrides=overrides)

    # The three broken sources are reported as failed, each with its own log line.
    for name in ("crash_on_init", "crash_on_start", "crash_on_parse"):
        assert results[name]["status"] == "failed", results[name]
        assert any(getattr(r, "source", None) == name and r.levelname == "ERROR" for r in caplog.records), name

    # The healthy sources still completed. Arbeitnow needed one retry after a 503.
    assert FakeNetwork.requested.count(API_URL + "?page=1") == 2
    # The documented API is called without a robots.txt lookup, scraped pages are checked first.
    assert "https://www.arbeitnow.com/robots.txt" not in FakeNetwork.requested
    assert "https://boom.example.com/robots.txt" in FakeNetwork.requested
    assert results["arbeitnow"] == {"status": "ok", "new": 1, "duplicate": 1, "filtered": 1,
                                    "errors": 0, "detail": "", "method": "C (official API)", "seen": 3}
    assert results["recheck"]["status"] == "ok"

    # A robots.txt refusal is reported as blocked, and the page is never requested.
    assert results["robots_blocked"]["status"] == "blocked"
    assert "https://noscrape.example.com/jobs/search?q=x" not in FakeNetwork.requested

    # HTTP 403 from bot protection is a refusal too, not a crash, and nothing is retried around it.
    assert results["refused"]["status"] == "blocked" and "403" in results["refused"]["detail"]
    assert FakeNetwork.requested.count("https://refuse.example.com/jobs?q=x") == 1

    # Bundesagentur: one request per query, all of Germany (no "wo"), with the API key header.
    assert results["arbeitsagentur"]["status"] == "ok" and results["arbeitsagentur"]["new"] == 1
    assert len(FakeNetwork.ba_queries) == 4 and all("wo=" not in q for q in FakeNetwork.ba_queries)
    assert not any("/pc/v4/" in u for u in FakeNetwork.requested)          # current v6 path only
    assert {q.split("angebotsart=")[1].split("&")[0] for q in FakeNetwork.ba_queries} == {"1", "34"}

    # Career pages: one company answered, one did not. Both visits are recorded for the rotation.
    career = results["career_pages"]
    assert (career["status"], career["new"], career["filtered"]) == ("partial", 1, 1)
    assert (career["companies_crawled"], career["companies_failed"]) == (1, 1)

    store = Store(cfg["paths"]["db"])
    try:
        assert store.due_companies(50, revisit_days=7) == []        # nobody is due again this week
        pool = {r["key"]: dict(r) for r in store.conn.execute("SELECT * FROM companies")}
        assert pool["greenhouse:acme"]["last_ok"] == 1 and pool["greenhouse:acme"]["last_postings"] == 2
        assert pool["lever:gone"]["fail_count"] == 1
        assert store.get("https://boards.greenhouse.io/acme/jobs/11")["date_published"] == "2026-11-09"
        ba = store.get("https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1002716922-S")
        assert (ba["company"], ba["location"], ba["date_published"]) == ("Beispiel AG", "Erfurt, Thüringen", "2026-11-12")
        store.conn.execute("DELETE FROM jobs WHERE link LIKE '%greenhouse%' OR link LIKE '%arbeitsagentur%'")
        store.conn.commit()
        assert store.count() == 5                                  # four seeded + one new, no duplicates
        assert store.get(NEW)["status"] == STATUS_NOT_APPLIED       # new posting appended
        assert store.get(NEW)["date_published"] == ""               # unparseable date stored empty
        assert store.get(KEPT)["status"] == STATUS_APPLIED          # status not overwritten
        assert store.get(KEPT)["date_published"] == "2026-11-01"    # row untouched by the new run
        assert store.get(REMOVED)["status"] == STATUS_GONE          # 404 -> no longer available
        assert store.get(REMOVED)["position"] == "Data Engineer"    # only the status changed
        assert store.get(APPLIED_REMOVED)["status"] == STATUS_GONE  # preserve_applied turned off here
        assert store.get(BLOCKED)["status"] == STATUS_NOT_APPLIED   # 403 is no verdict
    finally:
        store.close()

    with open(cfg["paths"]["csv"], encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 7
    assert set(rows[0]) == {"link", "position", "date of publication", "status"}
    assert {r["link"]: r["status"] for r in rows}[REMOVED] == STATUS_GONE
    assert any("unparseable" in r.getMessage() for r in caplog.records)
