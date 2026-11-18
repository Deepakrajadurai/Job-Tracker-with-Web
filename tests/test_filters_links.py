from jobtracker.ingest import ingest, location_wanted, title_wanted
from jobtracker.links import detect_ats, normalize_link


def test_normalize_link_rejects_non_http():
    for bad in (None, "", "mailto:x@y.z", "javascript:void(0)", "/relative/path", "ftp://x/y"):
        assert normalize_link(bad) == ""


def test_normalize_link_canonical_forms():
    assert normalize_link("HTTP://Example.com/Jobs/1/?utm_medium=a&id=7#top") == "https://example.com/Jobs/1?id=7"
    assert normalize_link("https://de.indeed.com/rc/clk?jk=abc123def4567890&from=ja") == \
        "https://de.indeed.com/viewjob?jk=abc123def4567890"
    assert normalize_link("https://boards.greenhouse.io/acme/jobs/1?gh_jid=1&gh_src=x") == \
        "https://boards.greenhouse.io/acme/jobs/1?gh_jid=1"


def test_detect_ats():
    assert detect_ats("https://boards.greenhouse.io/acme") == ("greenhouse", "acme")
    assert detect_ats("https://job-boards.greenhouse.io/acme/jobs/1") == ("greenhouse", "acme")
    assert detect_ats("https://jobs.lever.co/acme/") == ("lever", "acme")
    assert detect_ats("https://jobs.ashbyhq.com/Acme") == ("ashby", "Acme")
    assert detect_ats("https://acme.jobs.personio.de/") == ("personio", "acme")
    assert detect_ats("https://jobs.smartrecruiters.com/Acme") == ("smartrecruiters", "Acme")
    assert detect_ats("https://acme.com/careers") is None


def test_short_terms_match_whole_words_only(cfg):
    filters = cfg["filters"]
    assert title_wanted("Werkstudent AI Research", filters)
    assert title_wanted("AI/ML Engineer (m/w/d)", filters)
    assert not title_wanted("Maintenance Technician", filters)   # contains "ai" inside a word
    assert not title_wanted("Database Administrator", filters)   # "Data" must be its own word
    assert title_wanted("Machine Learning Praktikum", filters)   # longer terms match as substrings
    assert not title_wanted("International Sales", {"title_include_any": ["Intern"]})
    assert title_wanted("Werkstudentin Konstruktion", {"title_include_any": ["Werkstudent"]})


def test_require_any_narrows_results():
    filters = {"title_include_any": ["Data"], "title_require_any": ["Werkstudent", "Intern"]}
    assert title_wanted("Werkstudentin Data Science", filters)
    assert not title_wanted("Data Scientist", filters)


def test_location_filter(cfg):
    filters = cfg["filters"]
    assert location_wanted("Berlin", False, filters)
    assert location_wanted("Munich, Germany", False, filters)
    assert not location_wanted("Austin, TX", False, filters)
    assert location_wanted("Austin, TX", True, filters)      # remote roles are kept
    assert location_wanted("", False, filters)               # unknown location is kept


def test_ingest_filters(store, cfg):
    base = {"source": "t", "date": "", "apply_filter": True, "filter_location": True}
    assert ingest(store, cfg, {**base, "link": "https://e.com/1", "position": "Senior Data Engineer"}) == "filtered"
    assert ingest(store, cfg, {**base, "link": "https://e.com/2", "position": "Office Manager"}) == "filtered"
    assert ingest(store, cfg, {**base, "link": "https://e.com/3", "position": "Data Engineer",
                               "location": "Austin, TX"}) == "filtered"
    assert ingest(store, cfg, {**base, "link": "https://e.com/4", "position": "Data Engineer",
                               "location": "Berlin"}) == "new"
    # Search based sources skip the include filter, the exclude list still applies.
    assert ingest(store, cfg, {"source": "t", "link": "https://e.com/5", "position": "Office Manager"}) == "new"
    assert ingest(store, cfg, {"source": "t", "link": "not a url", "position": "x"}) == "invalid"


def test_example_config_loads():
    from pathlib import Path

    from jobtracker.config import load_config

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert cfg["sources"]["linkedin"]["guest_search"] is False
    assert cfg["ai"]["max_calls_per_run"] == 8
    assert len(cfg["sources"]["career_pages"]["companies"]) >= 10


def test_env_file_is_loaded_without_overriding_real_variables(tmp_path, monkeypatch):
    from jobtracker.config import load_env_file

    monkeypatch.setenv("JT_TEST_KEEP", "from-shell")
    for name in ("JT_TEST_A", "JT_TEST_B", "JT_TEST_EMPTY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\nJT_TEST_A=abc123\nJT_TEST_B="quoted value"\nJT_TEST_EMPTY=\n'
                   "JT_TEST_KEEP=from-file\nnot a setting\n", encoding="utf-8")
    import os

    assert load_env_file(env) == 2
    assert os.environ["JT_TEST_A"] == "abc123" and os.environ["JT_TEST_B"] == "quoted value"
    assert os.environ["JT_TEST_KEEP"] == "from-shell" and "JT_TEST_EMPTY" not in os.environ
    for name in ("JT_TEST_A", "JT_TEST_B"):
        monkeypatch.delenv(name)
    assert load_env_file(tmp_path / "missing.env") == 0
