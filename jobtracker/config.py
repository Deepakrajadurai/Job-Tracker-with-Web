"""Load config.yaml and merge it over the defaults."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULTS: dict[str, Any] = {
    # Who this search belongs to. "field" is free text and is used in the AI prompts.
    "profile": {"name": "default", "field": "", "levels": []},
    "search": {
        # Search strings sent to sources that support searching (LinkedIn, Xing, boards).
        # There is no default: the field is the user's choice (see `python -m jobtracker.init`).
        "queries": [],
        "locations": ["Germany"],
    },
    "filters": {
        # Applied to sources that return everything (Arbeitnow, ATS career APIs).
        "title_include_any": [],
        "title_require_any": [],
        # Applied to every source.
        "title_exclude_any": [],
        "location_match": [],
        "include_remote": True,
    },
    "sources": {
        "arbeitnow": {"enabled": True, "max_pages": 5, "filter_location": False},
        "arbeitsagentur": {
            "enabled": True,
            "api_version": "v6",
            "max_pages": 2,
            "page_size": 50,
            "max_age_days": 7,
            "radius_km": 25,
            "offer_types": [],       # empty: chosen from the profile levels (1 work, 34 internship/thesis)
            "title_filter": False,
        },
        "jobicy": {"enabled": True, "geo": ["europe"], "count": 100, "industry": ""},
        "linkedin": {
            "enabled": True,
            "email_alerts": True,
            "guest_search": False,
            "max_pages": 2,
            "max_age_days": 7,
        },
        "indeed": {"enabled": True, "email_alerts": True, "public_search": False,
                   "domain": "de.indeed.com", "max_age_days": 7},
        "xing": {"enabled": True, "email_alerts": True, "public_search": False},
        "working_student": {
            "enabled": True,
            "arbeitnow_filter": True,
            "max_pages": 8,
            "terms": [
                "werkstudent",
                "working student",
                "studentische",
                "student assistant",
                "hiwi",
            ],
            "boards": [],
        },
        "career_pages": {
            "enabled": True,
            "max_pages_per_company": 3,
            "companies_per_run": 50,   # target number of companies crawled per run
            "revisit_days": 7,         # a company is not crawled again before this many days
            "max_failures": 3,         # failed crawls in a row before a company is retired
            "companies": [],
        },
    },
    "email": {
        "host_env": "JOBTRACKER_IMAP_HOST",
        "user_env": "JOBTRACKER_IMAP_USER",
        "password_env": "JOBTRACKER_IMAP_PASSWORD",
        "folder": "INBOX",
        "days_back": 3,
    },
    "ai": {
        "enabled": True,
        "provider": "gemini",  # gemini | groq
        "model": "gemini-3.8-flash",
        "api_key_env": "GEMINI_API_KEY",
        "max_calls_per_run": 8,
        "cache_ttl_days": 7,
        "discover_companies": True,
        "max_companies": 60,        # most companies requested from the AI in one run
        "use_search_grounding": False,
        "pages_per_batch": 10,
        "timeout": 60,
    },
    # Method B. Renders pages that had no job data in their static HTML. Needs Playwright.
    "browser": {"enabled": False, "max_pages_per_run": 10, "timeout": 30, "settle_seconds": 1.5, "channel": ""},
    "recheck": {
        "enabled": True,
        "max_per_run": 300,
        "unreachable_threshold": 3,
        "preserve_applied": True,
        "closed_markers": [
            "no longer accepting applications",
            "job is no longer available",
            "position is no longer available",
            "this position has been filled",
            "job has expired",
            "job posting has expired",
            "stelle ist nicht mehr verfügbar",
            "anzeige ist nicht mehr verfügbar",
            "stellenanzeige ist nicht mehr",
            "stelle wurde bereits besetzt",
            "stellenangebot ist nicht mehr",
        ],
    },
    "scraping": {
        "user_agent": DEFAULT_USER_AGENT,
        "download_delay": 2.0,
        "concurrency_per_domain": 2,
        "timeout": 30,
        "retry_times": 3,
        "backoff_base": 2.0,
        "backoff_max": 60.0,
        "autothrottle_max_delay": 30.0,
    },
    "paths": {
        "db": "data/jobs.db",
        "csv": "data/jobs.csv",
        "log": "logs/jobtracker.log",
        "ai_cache": "data/ai_cache.json",
    },
    "logging": {"level": "INFO", "scrapy_level": "WARNING"},
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    """Return the merged config. Relative paths resolve against the config file's folder."""
    data: dict = {}
    base_dir = Path.cwd()
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ConfigError("config file must contain a YAML mapping")
        base_dir = p.resolve().parent
    cfg = _merge(DEFAULTS, data)
    if overrides:
        cfg = _merge(cfg, overrides)
    for key, value in cfg["paths"].items():
        vp = Path(value)
        cfg["paths"][key] = str(vp if vp.is_absolute() else base_dir / vp)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    for section in ("queries", "locations"):
        value = cfg["search"].get(section)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"search.{section} must be a list of strings")
    if not cfg["search"]["queries"]:
        raise ConfigError(
            "search.queries is empty. Create a profile with `python -m jobtracker.init` "
            "or list your search phrases under search.queries")
    if cfg["ai"]["provider"] not in ("gemini", "groq"):
        raise ConfigError("ai.provider must be 'gemini' or 'groq'")
    if int(cfg["ai"]["max_calls_per_run"]) < 0:
        raise ConfigError("ai.max_calls_per_run must be >= 0")
    for company in cfg["sources"]["career_pages"].get("companies") or []:
        if not isinstance(company, dict) or not (company.get("url") or company.get("slug")):
            raise ConfigError(f"career page entry needs 'url' or 'ats' + 'slug': {company!r}")


def load_env_file(path: str | Path = ".env") -> int:
    """Read KEY=VALUE lines from a .env file into the environment (works the same on every OS).

    Variables that are already set are left alone, so real environment variables win.
    Returns the number of variables that were set.
    """
    import os

    file = Path(path)
    if not file.is_file():
        return 0
    count = 0
    for line in file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def project_home() -> Path:
    """Where profiles, data and .env live: JOBTRACKER_HOME, else the project folder."""
    import os

    return Path(os.environ.get("JOBTRACKER_HOME") or Path(__file__).resolve().parents[1])
