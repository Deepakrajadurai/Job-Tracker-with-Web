"""Profiles: the same tool serves different fields and people."""
import sys
from unittest.mock import MagicMock

import pytest

from jobtracker import init as init_cli
from jobtracker import run as run_module
from jobtracker.ai import AIClient
from jobtracker.config import ConfigError, load_config
from jobtracker.ingest import ingest
from jobtracker.presets import FIELDS, LEVELS, build_profile
from jobtracker.store import Store

TITLES = {
    "tech": "Werkstudent Machine Learning (m/w/d)",
    "mech": "Werkstudent Konstruktion CAD (m/w/d)",
    "senior_mech": "Senior Konstrukteur Maschinenbau",
    "other": "Werkstudent Marketing",
}


def _write(tmp_path, *argv):
    assert init_cli.main([*argv, "--dir", str(tmp_path / "profiles")]) == 0


def _accepts(cfg, title, n):
    store = Store(cfg["paths"]["db"])
    try:
        return ingest(store, cfg, {"link": f"https://e.com/{n}", "position": title, "source": "t",
                                   "apply_filter": True}) == "new"
    finally:
        store.close()


def test_two_people_two_fields_two_trackers(tmp_path):
    _write(tmp_path, "--name", "deepak", "--field", "software_ai", "--level", "working_student", "thesis")
    _write(tmp_path, "--name", "friend", "--field", "mechanical", "--level", "working_student",
           "--location", "Germany", "Austria")
    tech = load_config(tmp_path / "profiles" / "deepak.yaml")
    mech = load_config(tmp_path / "profiles" / "friend.yaml")

    assert tech["paths"]["db"] != mech["paths"]["db"]                 # separate databases
    assert tech["paths"]["csv"].endswith("data/deepak/jobs.csv")
    assert mech["paths"]["csv"].endswith("data/friend/jobs.csv")
    assert mech["profile"]["field"] == "Mechanical engineering"
    assert "Werkstudent Maschinenbau" in mech["search"]["queries"]
    assert not any("Machine Learning" in q for q in mech["search"]["queries"])
    assert "Wien" in mech["filters"]["location_match"]

    accepted = {who: [key for n, (key, title) in enumerate(TITLES.items()) if _accepts(cfg, title, n)]
                for who, cfg in (("tech", tech), ("mech", mech))}
    assert accepted == {"tech": ["tech"], "mech": ["mech"]}


def test_levels_shape_queries_and_filters():
    student = build_profile("a", "mechanical", ["working_student", "thesis"], ["Germany"])
    assert "Werkstudent" in student["filters"]["title_require_any"]
    assert "Masterarbeit" in student["filters"]["title_require_any"]
    assert "Senior" in student["filters"]["title_exclude_any"]
    assert any(q.startswith("Abschlussarbeit ") for q in student["search"]["queries"])
    assert len(student["search"]["queries"]) <= 6

    senior = build_profile("b", "mechanical", ["experienced"], ["Germany"])
    assert senior["filters"]["title_require_any"] == [] == senior["filters"]["title_exclude_any"]
    assert "Maschinenbau" in senior["search"]["queries"]

    mixed = build_profile("c", "electrical", ["working_student", "entry"], ["Munich"])
    assert mixed["filters"]["title_require_any"] == []      # entry jobs carry no title marker
    assert mixed["filters"]["location_match"] == ["Munich"]


def test_custom_field_from_keywords(tmp_path):
    _write(tmp_path, "--name", "Sam Arch", "--field", "custom", "--keywords", "Architektur, BIM, Bauingenieur")
    cfg = load_config(tmp_path / "profiles" / "sam_arch.yaml")
    assert cfg["filters"]["title_include_any"] == ["Architektur", "BIM", "Bauingenieur"]
    assert cfg["search"]["queries"] == ["Architektur", "BIM", "Bauingenieur"]
    assert cfg["sources"]["career_pages"]["companies"] == []


def test_custom_field_needs_keywords(tmp_path, capsys):
    assert init_cli.main(["--name", "x", "--field", "custom", "--dir", str(tmp_path)]) == 2
    assert "keyword" in capsys.readouterr().err


def test_existing_profile_is_not_overwritten(tmp_path):
    _write(tmp_path, "--name", "a", "--field", "business")
    assert init_cli.main(["--name", "a", "--field", "mechanical", "--dir", str(tmp_path / "profiles")]) == 2
    assert load_config(tmp_path / "profiles" / "a.yaml")["profile"]["field"] == FIELDS["business"]["label"]


def test_description_is_turned_into_keywords_by_ai(tmp_path, monkeypatch):
    fake = MagicMock()
    fake.suggest_profile.return_value = {"keywords": ["Biotech", "Zellkultur", "Labor"],
                                         "search_terms": ["Biotechnologie", "Laborant"]}
    monkeypatch.setattr(init_cli, "suggest_from_description", lambda text, ai=None: fake.suggest_profile(text))
    _write(tmp_path, "--name", "kim", "--describe", "biotech lab work", "--level", "internship")
    cfg = load_config(tmp_path / "profiles" / "kim.yaml")
    assert cfg["profile"]["field"] == "biotech lab work"
    assert cfg["search"]["queries"] == ["Praktikum Biotechnologie", "Praktikum Laborant"]
    assert cfg["filters"]["title_include_any"] == ["Biotech", "Zellkultur", "Labor"]


def test_ai_prompts_carry_the_users_field(cfg, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_TEST_AI_KEY", "k")
    client = AIClient(cfg["ai"], cfg["paths"]["ai_cache"], session=MagicMock())
    seen = []
    monkeypatch.setattr(client, "complete_json", lambda prompt, grounded=False: seen.append(prompt) or [])
    client.discover_companies({"queries": ["Werkstudent Maschinenbau"], "locations": ["Germany"]},
                              field="Mechanical engineering")
    assert "employers in the field of Mechanical engineering" in seen[0]
    assert "technology" not in seen[0]

    monkeypatch.setattr(client, "complete_json", lambda prompt, grounded=False: {
        "keywords": ["Pflege", "Pflege", "x", "Krankenhaus"], "search_terms": []})
    assert client.suggest_profile("nursing") == {"keywords": ["Pflege", "Krankenhaus"],
                                                 "search_terms": ["Pflege", "Krankenhaus"]}


def test_no_field_is_assumed_without_a_profile(tmp_path):
    empty = tmp_path / "config.yaml"
    empty.write_text("search: {locations: [Germany]}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="jobtracker.init"):
        load_config(empty)


def test_all_profiles_run_separately_and_one_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    for name, field in (("a", "software_ai"), ("b", "mechanical"), ("c", "business")):
        _write(tmp_path, "--name", name, "--field", field)
    (profiles / "ignored.example.yaml").write_text("x: 1", encoding="utf-8")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=1 if cmd[4].endswith("b.yaml") else 0)

    monkeypatch.setattr(run_module.subprocess, "run", fake_run)
    assert run_module.main(["--all-profiles", "--profiles-dir", str(profiles), "--only", "arbeitnow"]) == 1
    assert [c[4].rsplit("/", 1)[-1] for c in calls] == ["a.yaml", "b.yaml", "c.yaml"]
    assert calls[0][:4] == [sys.executable, "-m", "jobtracker.run", "--config"]
    assert calls[0][5:] == ["--only", "arbeitnow"]


def test_every_preset_builds_a_valid_profile(tmp_path):
    for field in FIELDS:
        for level in LEVELS:
            profile = build_profile(f"{field}-{level}", field, [level], ["Germany"])
            assert profile["search"]["queries"] and profile["filters"]["title_include_any"]
