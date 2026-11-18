"""New sources (Method C) and the collection methods A and B, all without network."""
import time

from parsel import Selector

from jobtracker.browser import BrowserUnavailable
from jobtracker.extract import extract_postings
from jobtracker.links import detect_ats
from jobtracker.run import METHODS, ALL_SOURCES, build_plan, run_browser_phase
from jobtracker.spiders.arbeitsagentur import offer_types, parse_arbeitsagentur, search_places
from jobtracker.spiders.career_pages import parse_recruitee, parse_workable
from jobtracker.spiders.indeed import parse_indeed_cards
from jobtracker.spiders.jobicy import JobicySpider, parse_jobicy

FILTERS = {"title_include_any": ["Data", "Machine Learning"], "title_exclude_any": ["Senior"]}
JS_SHELL = '<html><body><div id="app"></div><a href="/imprint">Impressum und Kontakt</a></body></html>'
RENDERED = ('<html><body><a href="/jobs/1">Werkstudent Data Engineering</a>'
            '<a href="/jobs/2">Senior Data Architect</a><a href="/about">About the company</a></body></html>')


def test_bundesagentur_parsing_handles_both_api_versions():
    jobs = parse_arbeitsagentur({"stellenangebote": [
        {"titel": "Werkstudent KI", "refnr": "10000-1-S", "arbeitgeber": "A GmbH",
         "aktuelleVeroeffentlichungsdatum": "2026-11-10", "arbeitsort": {"ort": "Jena", "region": "Thüringen"}},
        {"stellenangebotsTitel": "Praktikum ML", "referenznummer": "10000-2-S", "arbeitsort": {"ort": "Berlin"}},
        {"titel": "no reference number"}]})
    assert [j["link"] for j in jobs] == ["https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1-S",
                                         "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-2-S"]
    assert jobs[0]["location"] == "Jena, Thüringen" and jobs[1]["position"] == "Praktikum ML"
    assert parse_arbeitsagentur({}) == []


def test_bundesagentur_search_follows_the_profile():
    assert search_places(["Germany", "Poland", "Austria"]) == [""]          # Germany wide, other countries left out
    assert search_places(["Erfurt", "Deutschland", "Jena"]) == ["Erfurt", "", "Jena"]
    assert search_places(["Austria"]) == []
    profile = lambda levels: {"profile": {"levels": levels}}                  # noqa: E731
    assert offer_types(profile(["working_student"]), {}) == [1]
    assert offer_types(profile(["working_student", "internship"]), {}) == [1, 34]
    assert offer_types(profile([]), {}) == [1, 34]
    assert offer_types(profile(["entry"]), {"offer_types": [4]}) == [4]


def test_jobicy_parsing_keeps_the_canonical_link():
    jobs = parse_jobicy({"jobs": [{"id": 1, "url": "https://jobicy.com/jobs/1-ml-engineer", "jobTitle": "ML Engineer",
                                   "companyName": "Remote Co", "jobGeo": "Europe", "pubDate": "2026-11-11T08:00:00+00:00"}]})
    assert jobs == [{"link": "https://jobicy.com/jobs/1-ml-engineer", "position": "ML Engineer",
                     "date": "2026-11-11T08:00:00+00:00", "company": "Remote Co", "location": "Europe", "remote": True}]


def test_jobicy_is_asked_at_most_once_per_hour(cfg):
    import asyncio
    from unittest.mock import MagicMock

    spider = JobicySpider(cfg)
    spider.crawler = MagicMock()

    async def collect():
        return [r async for r in spider.requests()]

    first = asyncio.run(collect())
    assert len(first) == 1 and "geo=europe" in first[0].url and first[0].meta["dont_obey_robotstxt"] is True
    assert asyncio.run(collect()) == []                                       # second run within the hour
    spider.stamp().write_text(str(time.time() - 3700))
    assert len(asyncio.run(collect())) == 1


def test_recruitee_and_workable_boards():
    recruitee = parse_recruitee({"offers": [{"title": "Data Analyst", "careers_url": "https://acme.recruitee.com/o/data-analyst",
                                             "published_at": "2026-11-09 10:00:00 UTC", "city": "Berlin", "country": "Germany",
                                             "remote": False}]})
    assert recruitee == [{"link": "https://acme.recruitee.com/o/data-analyst", "position": "Data Analyst",
                          "date": "2026-11-09 10:00:00 UTC", "location": "Berlin, Germany", "remote": False}]
    workable = parse_workable({"name": "Acme", "jobs": [{"title": "ML Intern", "shortlink": "https://apply.workable.com/j/ABC123",
                                                         "published_on": "2026-11-08", "city": "Munich", "country": "Germany",
                                                         "telecommuting": True}]})
    assert workable == [{"link": "https://apply.workable.com/j/ABC123", "position": "ML Intern", "date": "2026-11-08",
                         "location": "Munich, Germany", "remote": True}]
    assert detect_ats("https://acme.recruitee.com/o/data-analyst") == ("recruitee", "acme")
    assert detect_ats("https://apply.workable.com/acme/j/ABC123/") == ("workable", "acme")
    assert detect_ats("https://acme.workable.com") == ("workable", "acme")
    assert detect_ats("https://www.workable.com/pricing") is None


def test_indeed_cards():
    html = """<a data-jk="0123456789abcdef" href="/rc/clk?jk=0123456789abcdef"><span title="Werkstudent KI">Werkstudent KI</span></a>
              <a data-jk="0123456789abcdef" href="#">duplicate card</a><a href="/companies">Companies</a>"""
    assert parse_indeed_cards(Selector(text=html), "de.indeed.com") == [
        {"link": "https://de.indeed.com/viewjob?jk=0123456789abcdef", "position": "Werkstudent KI"}]


def test_method_a_prefers_structured_data_then_links():
    jsonld = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Data Engineer","url":"/j/9",'
              '"datePosted":"2026-11-01"}</script><a href="/j/other">Data Scientist</a>')
    jobs, _ = extract_postings(jsonld, "https://acme.com/careers", FILTERS)
    assert [(j["link"], j["date"]) for j in jobs] == [("https://acme.com/j/9", "2026-11-01")]
    jobs, anchors = extract_postings(RENDERED, "https://acme.com/careers", FILTERS)
    assert [j["position"] for j in jobs] == ["Werkstudent Data Engineering"] and len(anchors) == 3
    jobs, anchors = extract_postings(JS_SHELL, "https://acme.com/careers", FILTERS)
    assert jobs == [] and len(anchors) == 1


def test_method_b_renders_only_queued_pages_and_extracts_again(cfg, store):
    cfg["browser"].update(enabled=True, max_pages_per_run=1)
    cfg["filters"]["location_match"] = []
    page = lambda n: {"url": f"https://acme{n}.com/careers", "source": "career_pages", "company": f"Acme {n}",  # noqa: E731
                      "link_pattern": None, "apply_filter": True, "anchors": [{"text": "x", "href": "y"}]}
    calls, pending_ai = [], []

    def fake_render(urls, browser_cfg, user_agent):
        calls.append(urls)
        return {url: RENDERED for url in urls}

    result = run_browser_phase(cfg, store, [page(1), page(2)], pending_ai, render=fake_render)
    assert calls == [["https://acme1.com/careers"]]                # capped by max_pages_per_run
    assert (result["status"], result["new"], result["pages_rendered"]) == ("ok", 1, 1)   # the senior title is dropped
    row = store.get("https://acme1.com/jobs/1")
    assert row["company"] == "Acme 1" and row["source"] == "career_pages"
    assert run_browser_phase(cfg, store, [], pending_ai) is None   # nothing queued, no browser started


def test_method_b_falls_back_to_ai_when_no_browser_is_installed(cfg, store):
    def no_browser(urls, browser_cfg, user_agent):
        raise BrowserUnavailable("Playwright is not installed")

    pending_ai = []
    page = {"url": "https://acme.com/careers", "source": "career_pages", "company": "Acme", "link_pattern": None,
            "apply_filter": True, "anchors": [{"text": "Open roles", "href": "https://acme.com/roles"}]}
    result = run_browser_phase(cfg, store, [page], pending_ai, render=no_browser)
    assert result["status"] == "skipped" and "not installed" in result["detail"]
    assert pending_ai == [page] and store.count() == 0

    still_empty = lambda urls, b, ua: {u: JS_SHELL for u in urls}   # noqa: E731
    pending_ai.clear()
    run_browser_phase(cfg, store, [page], pending_ai, render=still_empty)
    assert len(pending_ai) == 1 and pending_ai[0]["anchors"][0]["text"] == "Impressum und Kontakt"


def test_plan_covers_new_sources_and_keeps_restricted_ones_opt_in(cfg):
    names = lambda: [n for n, _, _ in build_plan(cfg, set(ALL_SOURCES), [], [], [])[0]]   # noqa: E731
    assert {"arbeitnow", "arbeitsagentur", "jobicy", "working_student", "recheck"} <= set(names())
    assert not {"linkedin", "indeed", "xing"} & set(names())        # nothing without an explicit opt-in
    cfg["sources"]["indeed"]["public_search"] = True
    cfg["sources"]["linkedin"]["guest_search"] = True
    cfg["sources"]["xing"]["public_search"] = True
    assert {"linkedin", "indeed", "xing"} <= set(names())
    assert set(METHODS) == set(ALL_SOURCES)
