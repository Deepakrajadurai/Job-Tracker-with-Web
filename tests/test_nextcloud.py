"""Nextcloud copy of the results (WebDAV, mocked) and the Docker deployment files."""
import re
from unittest.mock import MagicMock

import pytest
import yaml

from jobtracker import nextcloud
from jobtracker.run import sync_nextcloud

SETTINGS = {"url": "http://100.64.146.94:8080/", "user": "deepak", "password": "app-pass", "folder": "Job Tracker/2026"}


def fake_session(mkcol=201, put=201):
    session = MagicMock()
    session.request.return_value = MagicMock(status_code=mkcol)
    session.put.return_value = MagicMock(status_code=put)
    return session


def test_settings_need_url_user_and_password():
    assert nextcloud.settings_from_env({}) is None
    env = {"JOBTRACKER_NEXTCLOUD_URL": "http://nc", "JOBTRACKER_NEXTCLOUD_USER": "a",
           "JOBTRACKER_NEXTCLOUD_PASSWORD": "b"}
    assert nextcloud.settings_from_env(env)["folder"] == "Job Tracker"
    assert nextcloud.settings_from_env({**env, "JOBTRACKER_NEXTCLOUD_PASSWORD": ""}) is None


def test_upload_creates_folders_and_puts_files(tmp_path):
    files = [tmp_path / "jobs.csv", tmp_path / "jobs.xlsx"]
    for f in files:
        f.write_bytes(b"x")
    session = fake_session()
    remote = nextcloud.upload(files, SETTINGS, session=session)
    base = "http://100.64.146.94:8080/remote.php/dav/files/deepak"
    assert [c.args for c in session.request.call_args_list] == [
        ("MKCOL", f"{base}/Job%20Tracker"), ("MKCOL", f"{base}/Job%20Tracker/2026")]
    assert [c.args[0] for c in session.put.call_args_list] == [
        f"{base}/Job%20Tracker/2026/jobs.csv", f"{base}/Job%20Tracker/2026/jobs.xlsx"]
    assert session.put.call_args.kwargs["auth"] == ("deepak", "app-pass")
    assert remote == ["Job Tracker/2026/jobs.csv", "Job Tracker/2026/jobs.xlsx"]
    # Existing folders answer 405, which is fine.
    nextcloud.upload(files, SETTINGS, session=fake_session(mkcol=405, put=204))


@pytest.mark.parametrize("mkcol, put, message", [(401, 201, "rejected the login"), (500, 201, "could not create"),
                                                  (201, 507, "HTTP 507")])
def test_upload_errors_are_clear(tmp_path, mkcol, put, message):
    (tmp_path / "jobs.csv").write_bytes(b"x")
    with pytest.raises(nextcloud.NextcloudError, match=message):
        nextcloud.upload([tmp_path / "jobs.csv"], SETTINGS, session=fake_session(mkcol, put))


def test_run_copies_csv_and_excel_and_never_fails_on_sync_problems(cfg, store, monkeypatch):
    store.add_job("https://example.com/j/1", "Werkstudent KI", "2026-11-20")
    store.export_csv(cfg["paths"]["csv"])
    assert sync_nextcloud(cfg, store) is None                        # not configured, nothing happens
    for key, value in (("URL", "http://nc"), ("USER", "deepak"), ("PASSWORD", "pw")):
        monkeypatch.setenv(f"JOBTRACKER_NEXTCLOUD_{key}", value)
    sent = []
    result = sync_nextcloud(cfg, store, upload=lambda paths, settings: sent.extend(paths) or ["Job Tracker/jobs.csv"])
    assert result["status"] == "ok" and sent[0].endswith("jobs.csv") and sent[1].endswith("jobs.xlsx")

    def broken(paths, settings):
        raise nextcloud.NextcloudError("Nextcloud rejected the login")

    result = sync_nextcloud(cfg, store, upload=broken)
    assert result["status"] == "failed" and "rejected the login" in result["detail"]


def test_web_settings_store_nextcloud_login_per_user(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobtracker.web.app import create_app

    monkeypatch.setenv("JOBTRACKER_SECRET_KEY", "s")
    monkeypatch.setenv("JOBTRACKER_NEXTCLOUD_PASSWORD", "server-level")
    client = TestClient(create_app(tmp_path, start_scheduler=False), follow_redirects=False)
    token = lambda p: re.search(r'name="csrf" value="([^"]+)"', client.get(p).text).group(1)   # noqa: E731
    client.post("/signup", data={"csrf": token("/signup"), "email": "a@example.com", "password": "a long password"})
    bad = client.post("/settings", data={"csrf": token("/settings"), "nc_url": "100.64.146.94:8080"})
    assert "must start with http" in client.get(bad.headers["location"]).text
    client.post("/settings", data={"csrf": token("/settings"), "nc_url": "http://100.64.146.94:8080/",
                                   "nc_user": "deepak", "nc_password": "nc-app-pass", "nc_folder": "/Jobs/"})
    page = client.get("/settings").text
    assert "http://100.64.146.94:8080" in page and "nc-app-pass" not in page
    assert b"nc-app-pass" not in (tmp_path / "data" / "app.db").read_bytes()
    env = client.app.state.runner.env_for(1)
    assert env["JOBTRACKER_NEXTCLOUD_PASSWORD"] == "nc-app-pass" and env["JOBTRACKER_NEXTCLOUD_FOLDER"] == "Jobs"
    client.post("/settings", data={"csrf": token("/settings"), "forget_nextcloud": "1"})
    assert "JOBTRACKER_NEXTCLOUD_PASSWORD" not in client.app.state.runner.env_for(1)   # never the server's


def test_compose_file_keeps_the_app_off_the_network():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    service = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]["jobtracker"]
    assert service["network_mode"] == "host" and service["environment"]["JOBTRACKER_HOST"] == "127.0.0.1"
    assert service["environment"]["JOBTRACKER_COOKIE_SECURE"] == "1"
    assert all(v.endswith((":/srv/jobtracker/data", ":/srv/jobtracker/profiles", ":/srv/jobtracker/logs"))
               for v in service["volumes"])
    assert ".env" in (root / ".dockerignore").read_text().split()
