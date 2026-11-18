"""Source parsers, run against canned payloads (no network)."""
from parsel import Selector

from jobtracker.availability import classify
from jobtracker.email_alerts import parse_alert
from jobtracker.extract import anchor_candidates, jsonld_jobs
from jobtracker.spiders.arbeitnow import parse_arbeitnow_job
from jobtracker.spiders.career_pages import (parse_ashby, parse_greenhouse, parse_lever,
                                             parse_personio, parse_smartrecruiters)
from jobtracker.spiders.linkedin import parse_linkedin_cards
from jobtracker.spiders.xing import JOB_LINK_PATTERN

MARKERS = ["no longer accepting applications", "stelle ist nicht mehr verfügbar"]


def test_arbeitnow_job():
    job = parse_arbeitnow_job({
        "slug": "ml-werkstudent-berlin-1", "company_name": "Acme", "title": "Werkstudent ML",
        "remote": False, "url": "https://www.arbeitnow.com/jobs/companies/acme/ml-werkstudent-berlin-1",
        "tags": ["IT"], "job_types": ["Werkstudent"], "location": "Berlin", "created_at": 1793779200})
    assert job["position"] == "Werkstudent ML" and job["date"] == 1793779200
    assert "Werkstudent" in job["labels"] and job["location"] == "Berlin"


def test_ats_parsers():
    gh = parse_greenhouse({"jobs": [{"absolute_url": "https://boards.greenhouse.io/a/jobs/1",
                                     "title": "ML Engineer", "updated_at": "2026-11-02T10:00:00-05:00",
                                     "location": {"name": "Berlin, Germany"}}]})
    assert gh == [{"link": "https://boards.greenhouse.io/a/jobs/1", "position": "ML Engineer",
                   "date": "2026-11-02T10:00:00-05:00", "location": "Berlin, Germany"}]
    lever = parse_lever([{"hostedUrl": "https://jobs.lever.co/a/1", "text": "Data Engineer",
                          "createdAt": 1793779200000, "categories": {"location": "Munich"},
                          "workplaceType": "remote"}])
    assert lever[0]["remote"] is True and lever[0]["date"] == 1793779200000
    ashby = parse_ashby({"jobs": [
        {"jobUrl": "https://jobs.ashbyhq.com/a/1", "title": "AI Intern", "publishedAt": "2026-11-01T00:00:00Z",
         "location": "Cologne", "isListed": True},
        {"jobUrl": "https://jobs.ashbyhq.com/a/2", "title": "Hidden", "isListed": False}]})
    assert [j["position"] for j in ashby] == ["AI Intern"]
    sr = parse_smartrecruiters({"content": [{"id": "743", "name": "Working Student AI",
                                             "releasedDate": "2026-10-30T09:00:00.000Z",
                                             "company": {"identifier": "BoschGroup"},
                                             "location": {"city": "Stuttgart", "country": "de"}}]}, "BoschGroup")
    assert sr[0]["link"] == "https://jobs.smartrecruiters.com/BoschGroup/743"
    assert sr[0]["location"] == "Stuttgart, de"
    xml = Selector(text="<workzag-jobs><position><id>55</id><name>Werkstudent Data</name>"
                        "<office>Erfurt</office><createdAt>2026-11-05T08:00:00+01:00</createdAt>"
                        "</position></workzag-jobs>", type="xml")
    assert parse_personio(xml, "https://acme.jobs.personio.de") == [{
        "link": "https://acme.jobs.personio.de/job/55", "position": "Werkstudent Data",
        "date": "2026-11-05T08:00:00+01:00", "location": "Erfurt"}]


def test_linkedin_cards():
    html = """<ul><li><div class="base-card">
      <a class="base-card__full-link" href="https://de.linkedin.com/jobs/view/werkstudent-ki-at-acme-4011122233?refId=x">
        <span class="sr-only">Werkstudent KI</span></a>
      <h3 class="base-search-card__title"> Werkstudent KI </h3>
      <h4 class="base-search-card__subtitle"><a> Acme GmbH </a></h4>
      <span class="job-search-card__location"> Erfurt, Thuringia, Germany </span>
      <time class="job-search-card__listdate" datetime="2026-11-10">4 days ago</time>
    </div></li><li><div>no job here</div></li></ul>"""
    cards = parse_linkedin_cards(Selector(text=html))
    assert cards == [{"link": "https://de.linkedin.com/jobs/view/werkstudent-ki-at-acme-4011122233?refId=x",
                      "position": "Werkstudent KI", "date": "2026-11-10", "company": "Acme GmbH",
                      "location": "Erfurt, Thuringia, Germany"}]


def test_jsonld_job_postings():
    html = """<html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"Data Scientist",
     "url":"/jobs/77","datePosted":"2026-11-07","hiringOrganization":{"name":"Acme"},
     "jobLocation":{"address":{"addressLocality":"Leipzig"}}},{"@type":"Organization","name":"x"}]}
    </script><script type="application/ld+json">{broken</script></head></html>"""
    assert jsonld_jobs(html, "https://acme.com/careers") == [{
        "link": "https://acme.com/jobs/77", "position": "Data Scientist", "date": "2026-11-07",
        "company": "Acme", "location": "Leipzig"}]


def test_anchor_candidates_with_xing_pattern():
    html = """<a href="/jobs/berlin-werkstudent-machine-learning-123456789"><h2>Werkstudent Machine Learning</h2>
              <p>Acme</p></a><a href="/jobs/search?page=2">Next page</a><a href="/imprint">Impressum</a>
              <a href="mailto:a@b.c">Mail us please</a>"""
    found = anchor_candidates(html, "https://www.xing.com/jobs/search?keywords=ml", JOB_LINK_PATTERN)
    assert found == [{"text": "Werkstudent Machine Learning",
                      "href": "https://www.xing.com/jobs/berlin-werkstudent-machine-learning-123456789"}]
    assert len(anchor_candidates(html, "https://www.xing.com/jobs/search")) == 3


def test_alert_emails():
    linkedin = """<a href="https://www.linkedin.com/comm/jobs/view/4011122233/?trackingId=abc"><img></a>
      <a href="https://www.linkedin.com/comm/jobs/view/4011122233/?trackingId=abc">Werkstudent Data Science</a>
      <a href="https://www.linkedin.com/comm/jobs/view/4011122233/?trackingId=zzz">View job</a>
      <a href="https://www.linkedin.com/comm/jobs/search?keywords=ml">See all jobs</a>"""
    assert parse_alert("linkedin", linkedin) == [{
        "link": "https://www.linkedin.com/jobs/view/4011122233", "position": "Werkstudent Data Science"}]
    indeed = """<a href="https://de.indeed.com/rc/clk/dl?jk=0123456789abcdef&from=ja&qd=x">ML Engineer (m/w/d)</a>
      <a href="https://cts.indeed.com/v1/x?u=https%3A%2F%2Fde.indeed.com%2Fviewjob%3Fjk%3Dfedcba9876543210">KI Praktikum</a>
      <a href="https://de.indeed.com/unsubscribe">Unsubscribe</a>"""
    assert parse_alert("indeed", indeed) == [
        {"link": "https://de.indeed.com/viewjob?jk=0123456789abcdef", "position": "ML Engineer (m/w/d)"},
        {"link": "https://de.indeed.com/viewjob?jk=fedcba9876543210", "position": "KI Praktikum"}]
    xing = '<a href="https://www.xing.com/jobs/erfurt-ai-engineer-987654321?sc_o=x">AI Engineer</a>'
    assert parse_alert("xing", xing) == [{
        "link": "https://www.xing.com/jobs/erfurt-ai-engineer-987654321", "position": "AI Engineer"}]


def test_availability_classification():
    url = "https://example.com/jobs/1"
    assert classify(404, url, url, "", MARKERS) == "gone"
    assert classify(410, url, url, "", MARKERS) == "gone"
    assert classify(200, url, url, "Apply now", MARKERS) == "available"
    assert classify(200, url, url, "No longer accepting applications", MARKERS) == "gone"
    assert classify(200, url, url, "Diese Stelle ist nicht mehr verfügbar.", MARKERS) == "gone"
    assert classify(200, url, "https://example.com/", "Welcome", MARKERS) == "gone"
    assert classify(200, url, "https://boards.greenhouse.io/acme?error=true", "", MARKERS) == "gone"
    for blocked in (403, 429, 999, 503):
        assert classify(blocked, url, url, "", MARKERS) == "unknown"
    assert classify(200, url, "https://www.linkedin.com/authwall?trk=x", "", MARKERS) == "unknown"
