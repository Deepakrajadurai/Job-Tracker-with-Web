"""Web interface: accounts, SSO, per-user data, status edits, downloads. No network, no real runs."""
import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from jobtracker import STATUS_APPLIED
from jobtracker.config import load_config
from jobtracker.store import Store
from jobtracker.web import app as app_module
from jobtracker.web import runner as runner_module
from jobtracker.web.app import create_app
from jobtracker.web.users import UserDB, check_password, hash_password

PASSWORD = "correct horse battery"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


def client_for(home):
    return TestClient(create_app(home, start_scheduler=False), follow_redirects=False)


def csrf(client, path="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def signup(client, email, name="Test"):
    response = client.post("/signup", data={"csrf": csrf(client, "/signup"), "email": email, "name": name,
                                            "password": PASSWORD})
    assert response.status_code == 303, response.text
    return response


def save_profile(client, field="software_ai", **extra):
    data = {"csrf": csrf(client, "/profile"), "field": field, "levels": ["working_student"],
            "locations": "Germany", **extra}
    response = client.post("/profile", data=data)
    assert response.headers["location"] == "/dashboard", client.get("/profile").text
    return response


def seed_jobs(home, user_id, jobs):
    cfg = load_config(home / "profiles" / f"user{user_id}.yaml")
    store = Store(cfg["paths"]["db"])
    for link, title in jobs:
        store.add_job(link, title, "2026-11-10", source="arbeitnow", company="Acme GmbH")
    store.close()
    return cfg


def test_passwords_are_hashed():
    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored and check_password(PASSWORD, stored)
    assert not check_password("wrong password!", stored) and not check_password(PASSWORD, None)


def test_pages_require_login(home):
    client = client_for(home)
    for path in ("/dashboard", "/profile", "/settings", "/export.csv"):
        response = client.get(path)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_signup_onboarding_dashboard_flow(home):
    client = client_for(home)
    assert signup(client, "deepak@example.com").headers["location"] == "/profile"   # new users set up a search
    save_profile(client)
    cfg = seed_jobs(home, 1, [("https://example.com/j/1", "Werkstudent Machine Learning")])
    assert cfg["profile"]["field"] == "Software, AI and data"
    page = client.get("/dashboard").text
    assert "Werkstudent Machine Learning" in page and "Acme GmbH" in page and "Search now" in page


def test_signup_rejects_bad_input_and_duplicates(home):
    client = client_for(home)
    token = csrf(client, "/signup")
    short = client.post("/signup", data={"csrf": token, "email": "a@example.com", "password": "short"})
    assert short.status_code == 400 and "at least 10" in short.text
    bad = client.post("/signup", data={"csrf": token, "email": "not-an-email", "password": PASSWORD})
    assert bad.status_code == 400
    signup(client, "a@example.com")
    other = client_for(home)
    again = other.post("/signup", data={"csrf": csrf(other, "/signup"), "email": "A@Example.com", "password": PASSWORD})
    assert again.status_code == 400 and "already exists" in again.text


def test_login_logout_and_lockout(home):
    client = client_for(home)
    signup(client, "a@example.com")
    client.post("/logout", data={"csrf": csrf(client, "/profile")})
    assert client.get("/dashboard").headers["location"] == "/login"
    token = csrf(client)
    assert client.post("/login", data={"csrf": token, "email": "a@example.com", "password": PASSWORD}).status_code == 303
    client.post("/logout", data={"csrf": csrf(client, "/profile")})
    for _ in range(5):
        wrong = client.post("/login", data={"csrf": csrf(client), "email": "a@example.com", "password": "nope nope nope"})
        assert wrong.status_code == 401
    locked = client.post("/login", data={"csrf": csrf(client), "email": "a@example.com", "password": PASSWORD})
    assert locked.status_code == 429


def test_forms_without_csrf_token_are_rejected(home):
    client = client_for(home)
    assert client.post("/signup", data={"email": "a@example.com", "password": PASSWORD}).status_code == 403
    signup(client, "a@example.com")
    assert client.post("/jobs/status", data={"link": "x", "status": "applied"}).status_code == 403
    assert client.post("/run", data={"csrf": "forged"}).status_code == 403


def test_sso_creates_account_and_links_only_verified_emails(home, monkeypatch):
    identity = {"subject": "g-123", "email": "sso@example.com", "name": "Sso User", "email_verified": True}

    async def fake_identity(oauth, provider, request):
        return identity

    monkeypatch.setattr(app_module, "fetch_identity", fake_identity)
    client = client_for(home)
    assert "Continue with Google" in client.get("/login").text
    assert "Continue with GitHub" not in client.get("/login").text        # not configured, not offered
    assert client.get("/auth/google/callback").headers["location"] == "/profile"
    assert "Sso User" in client.get("/profile").text
    assert client.get("/auth/github/callback").status_code == 404

    # Second login with the same identity lands in the same account.
    second = client_for(home)
    second.get("/auth/google/callback")
    users = UserDB(home / "data" / "app.db", "test-secret")
    assert users.all_ids() == [1]

    # A password account is not taken over by an unverified email from a provider.
    signup(client_for(home), "victim@example.com")
    identity.update(subject="g-999", email="victim@example.com", email_verified=False)
    attacker = client_for(home)
    assert attacker.get("/auth/google/callback").headers["location"] == "/login"
    assert attacker.get("/dashboard").headers["location"] == "/login"
    # With a verified email the provider login joins the existing account.
    identity["email_verified"] = True
    assert client_for(home).get("/auth/google/callback").status_code == 303
    assert users.all_ids() == [1, 2]
    users.close()


def test_users_only_see_their_own_jobs(home):
    alice, bob = client_for(home), client_for(home)
    signup(alice, "alice@example.com")
    save_profile(alice)
    signup(bob, "bob@example.com")
    save_profile(bob, field="mechanical")
    seed_jobs(home, 1, [("https://example.com/j/alice", "Werkstudent Data Science")])
    seed_jobs(home, 2, [("https://example.com/j/bob", "Werkstudent Konstruktion")])
    assert "Werkstudent Data Science" in alice.get("/dashboard").text
    assert "Werkstudent Konstruktion" not in alice.get("/dashboard").text
    assert b"j/alice" not in bob.get("/export.csv").content
    # Bob cannot change the status of Alice's posting.
    response = bob.post("/jobs/status", data={"csrf": csrf(bob, "/dashboard"), "link": "https://example.com/j/alice",
                                              "status": "applied"})
    assert response.status_code == 404


def test_status_change_is_saved_and_survives_the_next_run(home):
    client = client_for(home)
    signup(client, "a@example.com")
    save_profile(client)
    cfg = seed_jobs(home, 1, [("https://example.com/j/1", "Werkstudent AI")])
    response = client.post("/jobs/status", data={"csrf": csrf(client, "/dashboard"), "link": "https://example.com/j/1",
                                                 "status": STATUS_APPLIED})
    assert response.status_code == 303
    bad = client.post("/jobs/status", data={"csrf": csrf(client, "/dashboard"), "link": "https://example.com/j/1",
                                            "status": "interviewing"})
    assert bad.status_code == 400
    store = Store(cfg["paths"]["db"])
    try:
        assert store.get("https://example.com/j/1")["status"] == STATUS_APPLIED
        # The next run starts by importing the CSV. It must agree with the edit made in the browser.
        assert store.import_csv(cfg["paths"]["csv"])["status_updates"] == 0
        assert store.get("https://example.com/j/1")["status"] == STATUS_APPLIED
    finally:
        store.close()


def test_downloads_in_three_formats(home):
    client = client_for(home)
    signup(client, "a@example.com")
    save_profile(client)
    seed_jobs(home, 1, [("https://example.com/j/1", "Werkstudent KI (m/w/d) München"),
                        ("https://example.com/j/2", "Thesis NLP 自然言語")])
    csv_file = client.get("/export.csv")
    assert csv_file.headers["content-disposition"] == 'attachment; filename="jobs.csv"'
    lines = csv_file.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "link,position,date of publication,status" and len(lines) == 3

    book = load_workbook(io.BytesIO(client.get("/export.xlsx").content))
    rows = list(book.active.iter_rows(values_only=True))
    assert rows[0][:4] == ("link", "position", "date of publication", "status")
    assert {r[1] for r in rows[1:]} == {"Werkstudent KI (m/w/d) München", "Thesis NLP 自然言語"}

    pdf = client.get("/export.pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert len(client.get("/export.csv?q=Thesis").content.decode("utf-8-sig").splitlines()) == 2   # filter applies
    assert client.get("/export.docx").status_code == 404


def test_search_now_starts_one_run_with_the_users_own_mailbox(home, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_IMAP_PASSWORD", "server-wide-secret")
    started = []

    class FakeProcess:
        def __init__(self, cmd, **kwargs):
            started.append((cmd, kwargs["env"]))

        def poll(self):
            return None      # still running

    monkeypatch.setattr(runner_module.subprocess, "Popen", FakeProcess)
    client = client_for(home)
    signup(client, "a@example.com")
    save_profile(client)
    client.post("/settings", data={"csrf": csrf(client, "/settings"), "imap_host": "imap.example.com",
                                   "imap_user": "a@example.com", "imap_password": "app-pass", "linkedin_guest": "1"})
    assert load_config(home / "profiles" / "user1.yaml")["sources"]["linkedin"]["guest_search"] is True
    assert "app-pass" not in client.get("/settings").text                  # never shown again
    assert b"app-pass" not in (home / "data" / "app.db").read_bytes()      # stored encrypted

    client.post("/run", data={"csrf": csrf(client, "/dashboard")})
    client.post("/run", data={"csrf": csrf(client, "/dashboard")})          # second click while running
    assert len(started) == 1
    cmd, env = started[0]
    assert cmd[1:4] == ["-m", "jobtracker.run", "--config"] and cmd[4].endswith("user1.yaml")
    assert env["JOBTRACKER_IMAP_PASSWORD"] == "app-pass" and env["JOBTRACKER_IMAP_HOST"] == "imap.example.com"
    assert "Searching" in client.get("/dashboard").text

    # A user without a mailbox never inherits the server's or another user's credentials.
    other = client_for(home)
    signup(other, "b@example.com")
    assert "JOBTRACKER_IMAP_PASSWORD" not in other.app.state.runner.env_for(2)


def test_dashboard_explains_source_results(home):
    client = client_for(home)
    signup(client, "a@example.com")
    save_profile(client)
    cfg = seed_jobs(home, 1, [])
    blank = {"new": 0, "duplicate": 0, "filtered": 0, "errors": 0, "detail": ""}
    (home / "data" / "user1" / "last_run.json").write_text(__import__("json").dumps({
        "finished_at": "2026-11-14T19:32:11+00:00", "sources": {
            "arbeitnow": {**blank, "status": "ok", "new": 2},
            "xing": {**blank, "status": "blocked"},
            "linkedin": {**blank, "status": "skipped"},
            "career_pages": {**blank, "status": "partial", "companies_crawled": 49, "detail": "1 of 50 companies could not be read",
                             "pool": {"pool": 212, "inactive": 3, "selected": 50, "target": 50}}}}), encoding="utf-8")
    page = client.get("/dashboard").text
    assert "Not allowed" in page and "Xing forbids automated access" in page
    assert "Not set up" in page and "job alert mailbox" in page
    assert "49 companies read in this run, 212 in the weekly rotation" in page
    assert cfg["paths"]["db"].endswith("user1/jobs.db")


def test_daily_schedule_time():
    from datetime import datetime

    seconds = runner_module.Runner.seconds_until
    assert seconds("04:00", datetime(2026, 11, 14, 3, 0)) == 3600
    assert seconds("04:00", datetime(2026, 11, 14, 4, 0)) == 86400
    assert seconds("04:00", datetime(2026, 11, 14, 20, 30)) == 7.5 * 3600


def test_default_home_is_the_project_folder_not_the_terminal_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("JOBTRACKER_HOME", raising=False)
    monkeypatch.setenv("JOBTRACKER_SECRET_KEY", "s")
    monkeypatch.chdir(tmp_path)                                         # start from some other folder
    captured = {}
    monkeypatch.setattr(app_module, "UserDB", lambda path, key: captured.setdefault("db", path) and UserDB(
        tmp_path / "app.db", key))
    app = create_app(start_scheduler=False)
    app.state.users.close()
    assert captured["db"] == app_module.PROJECT_DIR / "data" / "app.db"
    assert (app_module.PROJECT_DIR / "jobtracker").is_dir()


def test_password_account_and_sso_land_in_the_same_account(home, monkeypatch):
    async def fake_identity(oauth, provider, request):
        return {"subject": "g-1", "email": "deepak@example.com", "name": "Deepak", "email_verified": True}

    monkeypatch.setattr(app_module, "fetch_identity", fake_identity)
    first = client_for(home)
    signup(first, "deepak@example.com")
    save_profile(first)
    seed_jobs(home, 1, [("https://example.com/j/1", "Werkstudent KI")])
    later = client_for(home)                                            # another day, another browser
    assert later.get("/auth/google/callback").headers["location"] == "/dashboard"
    assert "Werkstudent KI" in later.get("/dashboard").text
    page = later.get("/settings").text
    assert "Email and password, Google" in page
    users = UserDB(home / "data" / "app.db", "test-secret")
    assert users.all_ids() == [1]
    users.close()
