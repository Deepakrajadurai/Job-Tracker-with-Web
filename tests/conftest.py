import pytest

from jobtracker.config import load_config
from jobtracker.store import Store


@pytest.fixture
def cfg(tmp_path):
    return load_config(None, overrides={
        "paths": {"db": str(tmp_path / "jobs.db"), "csv": str(tmp_path / "jobs.csv"),
                  "log": str(tmp_path / "log.jsonl"), "ai_cache": str(tmp_path / "ai_cache.json")},
        "search": {"queries": ["Machine Learning Engineer", "Data Engineer"]},
        "filters": {"title_include_any": ["Machine Learning", "AI", "Data"],
                    "title_exclude_any": ["Senior"],
                    "location_match": ["Germany", "Berlin"]},
        "ai": {"api_key_env": "JOBTRACKER_TEST_AI_KEY", "max_calls_per_run": 2},
    })


@pytest.fixture
def store(cfg):
    s = Store(cfg["paths"]["db"])
    yield s
    s.close()
