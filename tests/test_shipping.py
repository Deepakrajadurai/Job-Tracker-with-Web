"""Backups, version endpoint, logos and the CI/CD files."""
import re
import sqlite3
from pathlib import Path

import yaml

from jobtracker.backup import backup_databases

ROOT = Path(__file__).resolve().parents[1]


def test_backup_copies_every_database_and_keeps_the_newest(tmp_path):
    data = tmp_path / "data"
    (data / "user1").mkdir(parents=True)
    for path in (data / "app.db", data / "user1" / "jobs.db"):
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.commit()
        conn.close()
    for n in range(4):
        target = backup_databases(data, keep=3, stamp=f"2026-11-2{n}")
    conn = sqlite3.connect(target / "user1" / "jobs.db")
    assert conn.execute("SELECT x FROM t").fetchone() == (42,)
    conn.close()
    assert sorted(p.name for p in (data / "backups").iterdir()) == ["2026-11-21", "2026-11-22", "2026-11-23"]
    assert not (target / "backups").exists()                    # backups are never backed up again
    assert backup_databases(tmp_path / "empty") is None


def test_healthz_reports_the_deployed_version(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobtracker.web.app import create_app

    monkeypatch.setenv("JOBTRACKER_SECRET_KEY", "s")
    monkeypatch.setenv("JOBTRACKER_VERSION", "abc1234")
    with TestClient(create_app(tmp_path, start_scheduler=False)) as client:
        assert client.get("/healthz").json() == {"ok": True, "version": "abc1234"}


def test_sso_buttons_show_official_logo_files_when_present(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from jobtracker.web import app as app_module

    monkeypatch.setenv("JOBTRACKER_SECRET_KEY", "s")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "a")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "b")
    static = tmp_path / "static"
    (static / "logos").mkdir(parents=True)
    (static / "style.css").write_text("")
    templates = app_module.HERE / "templates"
    monkeypatch.setattr(app_module, "HERE", tmp_path)
    (tmp_path / "templates").symlink_to(templates)
    with TestClient(app_module.create_app(tmp_path / "home", start_scheduler=False)) as client:
        assert "<img" not in client.get("/login").text           # no file, no broken image
    (static / "logos" / "github.svg").write_text("<svg/>")
    with TestClient(app_module.create_app(tmp_path / "home", start_scheduler=False)) as client:
        page = client.get("/login").text
        assert 'src="/static/logos/github.svg"' in page and "Continue with GitHub" in page


def test_pipeline_tests_every_push_and_deploys_only_main_on_the_home_server():
    pipeline = yaml.safe_load((ROOT / ".github/workflows/pipeline.yml").read_text())
    triggers = pipeline[True]                                     # YAML reads the key "on" as True
    assert "pull_request" in triggers and "push" in triggers
    test, deploy = pipeline["jobs"]["test"], pipeline["jobs"]["deploy"]
    assert test["runs-on"] == "ubuntu-latest"
    commands = " ".join(step.get("run", "") for step in test["steps"])
    assert "pytest" in commands and "ruff check" in commands and "docker build" in commands
    assert deploy["needs"] == "test" and "self-hosted" in deploy["runs-on"]
    assert "refs/heads/main" in deploy["if"] and "push" in deploy["if"]
    assert "scripts/ci-deploy.sh" in " ".join(step.get("run", "") for step in deploy["steps"])
    daily = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text())
    assert list(daily[True]) == ["workflow_dispatch"]            # the server does the daily run now


def test_deploy_script_backs_up_checks_version_and_rolls_back():
    script = (ROOT / "scripts/ci-deploy.sh").read_text()
    order = [script.index(word) for word in ("jobtracker.backup", "jobtracker:previous", "compose build",
                                             "healthz", "rolling back")]
    assert order == sorted(order)
    assert re.search(r'version.*APP_VERSION', script)
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["name"] == "jobtracker"
    assert compose["services"]["jobtracker"]["build"]["args"]["APP_VERSION"] == "${APP_VERSION:-dev}"
