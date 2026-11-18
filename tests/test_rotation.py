"""Weekly company rotation: 50 per run, no repeats within a week, no duplicate postings."""
from datetime import date, timedelta
from unittest.mock import MagicMock

from jobtracker import STATUS_APPLIED
from jobtracker.ai import AIUnavailable
from jobtracker.ingest import ingest
from jobtracker.run import select_companies
from jobtracker.store import company_key

DAY1 = date(2026, 11, 16)


def fill(store, count, start=0):
    return [store.upsert_company({"name": f"Co {n}", "ats": "greenhouse", "slug": f"co{n}"})
            for n in range(start, start + count)]


def crawl(store, day, limit=50):
    """Simulate one daily run: take the due companies and record the visit."""
    due = store.due_companies(limit, revisit_days=7, today=day)
    for company in due:
        store.mark_company(company["key"], True, postings=3, today=day)
    return [c["key"] for c in due]


def test_company_identity():
    assert company_key({"ats": "greenhouse", "slug": "Acme"}) == "greenhouse:acme"
    assert company_key({"url": "https://boards.greenhouse.io/acme"}) == "greenhouse:acme"
    assert company_key({"url": "https://Acme.com/careers/?utm_source=x"}) == "url:https://acme.com/careers"
    assert company_key({"name": "no link"}) == ""


def test_same_company_is_only_stored_once(store):
    store.upsert_company({"name": "Acme", "ats": "greenhouse", "slug": "acme"})
    store.upsert_company({"name": "ACME GmbH", "url": "https://boards.greenhouse.io/acme"}, "ai")
    assert store.company_stats()["pool"] == 1


def test_fifty_per_day_and_no_repeat_within_a_week(store):
    fill(store, 400)
    visited: dict[str, date] = {}
    for offset in range(7):
        day = DAY1 + timedelta(days=offset)
        keys = crawl(store, day)
        assert len(keys) == 50
        assert not set(keys) & set(visited), f"a company was repeated on day {offset + 1}"
        visited.update({key: day for key in keys})
    assert len(visited) == 350

    # Day 8: the 50 companies that never had a turn go first.
    day8 = crawl(store, DAY1 + timedelta(days=7))
    assert len(day8) == 50 and not set(day8) & set(visited)
    # Day 9: now the companies from day 1 are due again, exactly one week later or more.
    day9 = crawl(store, DAY1 + timedelta(days=8))
    assert {visited[key] for key in day9} == {DAY1}


def test_small_pool_is_not_recrawled_before_a_week(store):
    fill(store, 12)
    assert len(crawl(store, DAY1)) == 12
    for offset in range(1, 7):
        assert crawl(store, DAY1 + timedelta(days=offset)) == []
    assert len(crawl(store, DAY1 + timedelta(days=7))) == 12


def test_position_seen_again_after_a_week_is_not_duplicated(store, cfg):
    posting = {"link": "https://boards.greenhouse.io/co1/jobs/7?gh_src=abc", "position": "Data Engineer",
               "date": "2026-11-10", "source": "career_pages"}
    assert ingest(store, cfg, posting) == "new"
    store.set_status("https://boards.greenhouse.io/co1/jobs/7", STATUS_APPLIED)
    # One week later the company is crawled again and still lists the same position.
    assert ingest(store, cfg, {**posting, "link": "https://boards.greenhouse.io/co1/jobs/7?gh_src=xyz",
                               "date": "2026-11-17"}) == "duplicate"
    row = store.get("https://boards.greenhouse.io/co1/jobs/7")
    assert store.count() == 1 and row["status"] == STATUS_APPLIED and row["date_published"] == "2026-11-10"


def test_company_that_keeps_failing_is_retired(store):
    key = fill(store, 1)[0]
    for week in range(3):
        day = DAY1 + timedelta(days=7 * week)
        assert [c["key"] for c in store.due_companies(50, today=day)] == [key]
        store.mark_company(key, False, today=day)
    assert store.due_companies(50, today=DAY1 + timedelta(days=28)) == []
    assert store.company_stats() == {"pool": 1, "inactive": 1}


def test_success_resets_the_failure_count(store):
    key = fill(store, 1)[0]
    store.mark_company(key, False, today=DAY1)
    store.mark_company(key, False, today=DAY1 + timedelta(days=7))
    store.mark_company(key, True, 5, today=DAY1 + timedelta(days=14))
    store.mark_company(key, False, today=DAY1 + timedelta(days=21))
    assert len(store.due_companies(50, today=DAY1 + timedelta(days=28))) == 1


def test_ai_tops_up_the_pool_with_new_companies_only(store, cfg):
    cfg["sources"]["career_pages"]["companies"] = [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]
    cfg["profile"]["field"] = "Mechanical engineering"
    ai = MagicMock()
    ai.discover_companies.return_value = [
        {"name": f"New {n}", "url": f"https://jobs.lever.co/new{n}"} for n in range(49)
    ] + [{"name": "Acme duplicate", "url": "https://boards.greenhouse.io/acme"}]
    due, stats = select_companies(ai, cfg, store)

    args, kwargs = ai.discover_companies.call_args
    assert args[1] == "Mechanical engineering"
    assert kwargs["exclude"] == ["Acme"] and kwargs["limit"] == 59      # 49 missing + margin of 10
    assert len(due) == 50 and stats["pool"] == 50 and "shortfall" not in stats

    # Next day everything was crawled, so the AI is asked again and told what is already known.
    for company in due:
        store.mark_company(company["key"], True)
    ai.discover_companies.return_value = []
    due, stats = select_companies(ai, cfg, store)
    assert due == [] and stats["shortfall"]
    assert len(ai.discover_companies.call_args.kwargs["exclude"]) == 50


def test_without_ai_the_run_uses_what_is_due_and_says_so(store, cfg):
    cfg["sources"]["career_pages"]["companies"] = [
        {"name": f"C{n}", "ats": "ashby", "slug": f"c{n}"} for n in range(11)]
    ai = MagicMock()
    ai.discover_companies.side_effect = AIUnavailable("no key")
    due, stats = select_companies(ai, cfg, store)
    assert len(due) == 11 and stats["target"] == 50 and "no key" in stats["shortfall"]
