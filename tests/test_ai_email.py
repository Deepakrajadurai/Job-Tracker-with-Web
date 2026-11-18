"""AI client (cap, cache, validation) and IMAP fallback, with all network mocked."""
import json
from email.message import EmailMessage
from unittest.mock import MagicMock

import pytest
import requests

from jobtracker import email_alerts
from jobtracker.ai import AIClient, AIUnavailable, parse_json_text
from jobtracker.run import run_email_alerts


def gemini_response(payload, status=200):
    response = MagicMock(status_code=status)
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


@pytest.fixture
def ai(cfg, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TEST_AI_KEY", "test-key")
    session = MagicMock()
    client = AIClient(cfg["ai"], cfg["paths"]["ai_cache"], session=session, sleep=lambda s: None)
    return client, session


def test_ai_is_off_without_key(cfg, monkeypatch):
    monkeypatch.delenv("JOBTRACKER_TEST_AI_KEY", raising=False)
    client = AIClient(cfg["ai"], cfg["paths"]["ai_cache"], session=MagicMock())
    assert not client.available
    with pytest.raises(AIUnavailable):
        client.complete_json("x")
    client.session.post.assert_not_called()


def test_discovery_validates_and_caches(ai, cfg):
    client, session = ai
    session.post.return_value = gemini_response([
        {"name": "Acme", "careers_url": "https://boards.greenhouse.io/acme"},
        {"name": "Acme again", "careers_url": "https://boards.greenhouse.io/acme/"},
        {"name": "No URL", "careers_url": "careers page"},
        "garbage"])
    first = client.discover_companies(cfg["search"])
    assert [c["name"] for c in first] == ["Acme"]
    assert session.post.call_args.kwargs["headers"]["x-goog-api-key"] == "test-key"
    # A second client (the next daily run) reads the cache and makes no request.
    again = AIClient(cfg["ai"], cfg["paths"]["ai_cache"], session=MagicMock())
    assert again.discover_companies(cfg["search"]) == first
    again.session.post.assert_not_called()
    assert again.calls_made == 0


def test_call_cap_is_enforced(ai):
    client, session = ai
    session.post.return_value = gemini_response([])
    client.complete_json("one")
    client.complete_json("two")
    with pytest.raises(AIUnavailable):
        client.complete_json("three")
    assert session.post.call_count == 2 == client.calls_made


def test_rate_limit_retries_count_against_the_cap(ai):
    client, session = ai
    session.post.side_effect = [gemini_response([], status=429), gemini_response([{"a": 1}])]
    assert client.complete_json("x") == [{"a": 1}]
    assert client.calls_made == 2


def test_extraction_is_batched_and_rejects_invented_links(ai, cfg):
    client, session = ai
    pages = [{"url": f"https://c{n}.com/careers", "source": "career_pages", "company": f"C{n}",
              "anchors": [{"text": "ML Engineer", "href": f"https://c{n}.com/jobs/1"}]} for n in range(3)]
    session.post.return_value = gemini_response([
        {"page": 0, "url": "https://c0.com/jobs/1", "title": "ML Engineer", "date": ""},
        {"page": 1, "url": "https://c1.com/jobs/INVENTED", "title": "Fake", "date": ""},
        {"page": 9, "url": "https://c0.com/jobs/1", "title": "Bad index", "date": ""}])
    jobs = client.extract_jobs(pages, cfg["search"])
    assert session.post.call_count == 1          # three pages, one request
    assert [(j["link"], j["company"]) for j in jobs] == [("https://c0.com/jobs/1", "C0")]


def test_parse_json_text_handles_fences_and_prose():
    assert parse_json_text('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json_text('Here you go: [{"a": 1}] Hope it helps') == [{"a": 1}]


# ----------------------------------------------------------------- email alerts
class FakeIMAP:
    def __init__(self, host):
        self.host = host
        self.selected = None

    def login(self, user, password):
        return "OK", []

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return "OK", []

    def search(self, charset, criteria):
        return "OK", [b"1"] if "linkedin.com" in criteria else [b""]

    def fetch(self, num, spec):
        message = EmailMessage()
        message["From"] = "jobalerts-noreply@linkedin.com"
        message["Subject"] = "3 new jobs"
        message["Date"] = "Thu, 12 Nov 2026 07:00:00 +0100"
        message.set_content("plain")
        message.add_alternative(
            '<a href="https://www.linkedin.com/comm/jobs/view/4099988877">Working Student NLP</a>',
            subtype="html")
        return "OK", [(b"1 (RFC822)", message.as_bytes())]

    def logout(self):
        return "BYE", []


def test_email_alerts_are_ingested(cfg, store, monkeypatch):
    for var in ("HOST", "USER", "PASSWORD"):
        monkeypatch.setenv(f"JOBTRACKER_IMAP_{var}", "x")
    monkeypatch.setattr(email_alerts.imaplib, "IMAP4_SSL", FakeIMAP)
    results = run_email_alerts(cfg, store, {"linkedin", "indeed", "xing"})
    assert results["linkedin"]["new"] == 1 and results["indeed"]["new"] == 0
    row = store.get("https://www.linkedin.com/jobs/view/4099988877")
    assert row["position"] == "Working Student NLP" and row["date_published"] == "2026-11-12"


def test_email_failure_is_reported_per_source_and_does_not_raise(cfg, store, monkeypatch):
    for var in ("HOST", "USER", "PASSWORD"):
        monkeypatch.setenv(f"JOBTRACKER_IMAP_{var}", "x")

    def boom(*args, **kwargs):
        raise OSError("imap down")

    monkeypatch.setattr(email_alerts, "fetch_alert_messages", boom)
    results = run_email_alerts(cfg, store, {"linkedin", "indeed", "xing"})
    assert {r["status"] for r in results.values()} == {"failed"}
    assert set(results) == {"linkedin", "indeed", "xing"}


def test_missing_imap_credentials_are_reported_not_hidden(cfg, store, monkeypatch):
    for var in ("HOST", "USER", "PASSWORD"):
        monkeypatch.delenv(f"JOBTRACKER_IMAP_{var}", raising=False)
    results = run_email_alerts(cfg, store, {"linkedin", "indeed", "xing"})
    assert all(r["status"] == "skipped" and "IMAP" in r["detail"] for r in results.values())
