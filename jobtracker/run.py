"""Entry point: python -m jobtracker.run --config config.yaml"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scrapy import signals
from scrapy.crawler import CrawlerProcess

from . import email_alerts
from .ai import AIClient, AIUnavailable
from .config import load_config, load_env_file
from .ingest import ingest
from .logging_setup import setup_logging
from .scrapy_ext import build_settings
from .spiders.arbeitnow import ArbeitnowSpider
from .spiders.arbeitsagentur import ArbeitsagenturSpider
from .spiders.indeed import IndeedSpider
from .spiders.jobicy import JobicySpider
from .spiders.career_pages import CareerPagesSpider
from .spiders.linkedin import LinkedInSpider
from .spiders.recheck import RecheckSpider
from .spiders.working_student import WorkingStudentSpider
from .spiders.xing import XingSpider
from .store import Store

logger = logging.getLogger("jobtracker.run")

EMAIL_SOURCES = ("linkedin", "indeed", "xing")
ALL_SOURCES = ("arbeitnow", "arbeitsagentur", "jobicy", "linkedin", "indeed", "xing", "working_student",
               "career_pages", "recheck")

# How each source is collected. A = HTTP + HTML/JSON-LD, B = browser rendering, C = API or feed.
METHODS = {
    "arbeitnow": "C (official API)",
    "arbeitsagentur": "C (Jobsuche endpoint)",
    "jobicy": "C (official API)",
    "working_student": "C (Arbeitnow API), then A and B for extra boards",
    "career_pages": "C (ATS APIs), then A, then B, then AI extraction",
    "linkedin": "job alert emails, optional A (guest search)",
    "indeed": "job alert emails, optional A (public search)",
    "xing": "job alert emails, optional A (public search)",
    "recheck": "A",
}


def _blank() -> dict:
    return {"status": "ok", "new": 0, "duplicate": 0, "filtered": 0, "errors": 0, "detail": ""}


def _combine_status(a: str, b: str) -> str:
    states = {a, b}
    if len(states) == 1:
        return a
    if "ok" in states and states <= {"ok", "skipped", "blocked"}:
        return "ok"  # one method worked, the other was not configured or not allowed
    if states <= {"skipped", "blocked"}:
        return "blocked"
    if "failed" in states and states <= {"failed", "skipped", "blocked"}:
        return "failed"
    return "partial"


def _merge_result(results: dict, name: str, part: dict) -> None:
    """Combine the outcome of two methods for one source (e.g. email alerts and search)."""
    current = results.get(name)
    if current is None:
        results[name] = part
        return
    for key in ("new", "duplicate", "filtered", "errors"):
        current[key] += part.get(key, 0)
    current["status"] = _combine_status(current["status"], part["status"])
    current["detail"] = "; ".join(d for d in (current["detail"], part.get("detail", "")) if d)


def _ensure_reactor() -> None:
    """Install the asyncio reactor up front.

    Scrapy installs it while starting the first spider. If that first spider fails to start,
    no reactor exists and every later spider fails too, which would break source isolation.
    """
    import sys as _sys

    if "twisted.internet.reactor" not in _sys.modules:
        from scrapy.utils.reactor import install_reactor

        install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")


def run_spiders(plan: list[tuple], settings: dict) -> dict:
    """Run every (name, spider_cls, kwargs) in one Scrapy process, isolated from each other.

    A spider that crashes or cannot even be created is logged and reported as failed.
    The remaining spiders keep running.
    """
    results: dict[str, dict] = {}
    if not plan:
        return results
    _ensure_reactor()
    process = CrawlerProcess(settings)
    # Scrapy resets its logger levels when the process is created, so apply ours again.
    for noisy in ("scrapy", "twisted"):
        logging.getLogger(noisy).setLevel(settings.get("LOG_LEVEL", "WARNING"))
    crawlers = {}

    def on_spider_error(failure, response, spider):
        spider.crawler.stats.inc_value("jobtracker/errors")
        logger.error("source raised while parsing a response", extra={
            "source": spider.name, "url": getattr(response, "url", ""),
            "error": f"{failure.type.__name__}: {failure.getErrorMessage()}"})

    for name, spider_cls, kwargs in plan:
        results[name] = _blank()

        def on_crash(failure, name=name):
            results[name]["status"] = "failed"
            results[name]["detail"] = f"{failure.type.__name__}: {failure.getErrorMessage()}"
            logger.error("source crashed", extra={"source": name, "error": results[name]["detail"]})

        try:
            crawler = process.create_crawler(spider_cls)
            crawler.signals.connect(on_spider_error, signal=signals.spider_error)
            process.crawl(crawler, **kwargs).addErrback(on_crash)
            crawlers[name] = crawler
        except Exception as exc:  # noqa: BLE001 - isolate each source
            results[name].update(status="failed", detail=repr(exc))
            logger.exception("source could not be started", extra={"source": name})
    try:
        process.start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("crawl process failed", extra={"error": repr(exc)})
    for name, crawler in crawlers.items():
        try:
            stats = crawler.stats.get_stats()
        except RuntimeError:  # the crawl never started, on_crash already recorded why
            stats = {}
        res = results[name]
        for key in ("new", "duplicate", "filtered"):
            res[key] = stats.get(f"jobtracker/{key}", 0)
        res["errors"] = stats.get("jobtracker/errors", 0)
        got_something = stats.get("jobtracker/seen", 0) > 0
        if res["status"] == "ok" and res["errors"]:
            res["status"] = "partial" if got_something else "failed"
            res["detail"] = "see log for the failed requests"
        blocked = stats.get("jobtracker/robots_blocked", 0)
        refused = stats.get("jobtracker/access_refused", 0)
        if blocked and res["status"] == "ok" and not got_something:
            res["status"] = "blocked"
            res["detail"] = "the site's robots.txt forbids automated access to its search pages"
        elif refused and res["status"] == "ok" and not got_something:
            res["status"] = "blocked"
            res["detail"] = (f"the site refused automated requests "
                             f"(HTTP {stats.get('jobtracker/refused_status')}), nothing is worked around")
        elif refused and res["status"] == "ok":
            res["status"] = "partial"
            res["detail"] = f"{refused} requests were refused by the site (rate limit or bot protection)"
        if name == "career_pages":
            res["companies_crawled"] = stats.get("jobtracker/companies_ok", 0)
            res["companies_failed"] = stats.get("jobtracker/companies_failed", 0)
            if res["status"] == "failed" and res["companies_crawled"]:
                res["status"] = "partial"   # some companies answered, even if no posting matched
            if res["status"] == "partial":
                res["detail"] = f"{res['companies_failed']} of " \
                    f"{res['companies_crawled'] + res['companies_failed']} companies could not be read"
        if name == "recheck":
            res["marked_gone"] = stats.get("jobtracker/marked_gone", 0)
            res["checked"] = sum(v for k, v in stats.items() if k.startswith("jobtracker/check_"))
    return results


def ingest_many(store: Store, cfg: dict, postings: list[dict]) -> dict:
    res = _blank()
    for posting in postings:
        outcome = ingest(store, cfg, posting)
        if outcome in res:
            res[outcome] += 1
    return res


def run_email_alerts(cfg: dict, store: Store, wanted: set[str]) -> dict:
    """Read job alert emails for the sources that have email_alerts enabled."""
    results: dict[str, dict] = {}
    sources = [s for s in EMAIL_SOURCES if s in wanted and cfg["sources"][s].get("enabled")
               and cfg["sources"][s].get("email_alerts")]
    if not sources:
        return results
    if email_alerts.imap_credentials(cfg["email"]) is None:
        for source in sources:
            results[source] = {**_blank(), "status": "skipped",
                               "detail": "email alerts enabled but IMAP credentials are not set"}
            logger.warning("email alert method not usable, IMAP credentials missing",
                           extra={"source": source})
        return results
    try:
        collected = email_alerts.collect(cfg, sources)
    except Exception as exc:  # noqa: BLE001 - mail trouble must not stop the run
        for source in sources:
            results[source] = {**_blank(), "status": "failed", "errors": 1,
                               "detail": f"email alerts: {exc!r}"}
            logger.error("email alert source failed", extra={"source": source, "error": repr(exc)})
        return results
    for source in sources:
        try:
            results[source] = ingest_many(store, cfg, collected.get(source, []))
        except Exception as exc:  # noqa: BLE001
            results[source] = {**_blank(), "status": "failed", "errors": 1, "detail": repr(exc)}
            logger.exception("email alert source failed", extra={"source": source})
    return results


def select_companies(ai: AIClient, cfg: dict, store: Store) -> tuple[list[dict], dict]:
    """Pick the companies to crawl in this run (the weekly rotation).

    1. Companies from the profile are added to the pool.
    2. Companies not crawled for `revisit_days` are due, longest waiting first.
    3. If fewer than `companies_per_run` are due, the AI is asked for new companies
       (never ones already in the pool), which are added and crawled right away.
    """
    scfg = cfg["sources"]["career_pages"]
    target, days, fails = int(scfg["companies_per_run"]), int(scfg["revisit_days"]), int(scfg["max_failures"])
    for entry in scfg.get("companies") or []:
        store.upsert_company(entry, "config")
    due = store.due_companies(target, days, fails)
    note = ""
    if len(due) < target and cfg["ai"].get("discover_companies"):
        try:
            found = ai.discover_companies(cfg["search"], cfg["profile"].get("field", ""),
                                          exclude=store.company_names(), limit=target - len(due) + 10)
            added = [store.upsert_company(c, "ai") for c in found]
            logger.info("AI suggested new companies", extra={
                "source": "ai", "count": len([k for k in added if k]), "companies": [c["name"] for c in found]})
            due = store.due_companies(target, days, fails)
        except AIUnavailable as exc:
            note = f"no new companies from AI ({exc})"
            logger.warning("AI company discovery skipped", extra={"source": "ai", "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            note = "AI company discovery failed"
            logger.error("AI company discovery failed", extra={"source": "ai", "error": repr(exc)})
    stats = {**store.company_stats(fails), "selected": len(due), "target": target, "revisit_days": days}
    if len(due) < target:
        stats["shortfall"] = note or "the pool has no more companies that are due"
        logger.warning("fewer companies than the target are due in this run", extra={"source": "career_pages", **stats})
    else:
        logger.info("companies selected for this run", extra={"source": "career_pages", **stats})
    return due, stats


def run_ai_extraction(ai: AIClient, cfg: dict, store: Store, pending: list[dict]) -> dict | None:
    if not pending:
        return None
    try:
        postings = ai.extract_jobs(pending, cfg["search"])
    except AIUnavailable as exc:
        logger.warning("AI extraction skipped", extra={
            "source": "ai", "reason": str(exc), "pages": [p["url"] for p in pending]})
        return {**_blank(), "status": "skipped", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("AI extraction failed", extra={"source": "ai", "error": repr(exc)})
        return {**_blank(), "status": "failed", "errors": 1, "detail": repr(exc)}
    return ingest_many(store, cfg, postings)


def run_browser_phase(cfg: dict, store: Store, pending_render: list[dict], pending_ai: list[dict],
                      render=None) -> dict | None:
    """Method B: render the pages that had no job data in their static HTML and extract again.

    Pages that are still empty after rendering are handed on to the AI extraction.
    """
    if not pending_render:
        return None
    from .browser import BrowserUnavailable, render_pages
    from .extract import extract_postings

    limit = int(cfg["browser"]["max_pages_per_run"])
    pages, skipped = pending_render[:limit], pending_render[limit:]
    if skipped:
        logger.warning("more pages need rendering than browser.max_pages_per_run allows",
                       extra={"source": "browser", "skipped": [p["url"] for p in skipped]})
    try:
        rendered = (render or render_pages)([p["url"] for p in pages], cfg["browser"], cfg["scraping"]["user_agent"])
    except BrowserUnavailable as exc:
        logger.warning("browser rendering skipped", extra={"source": "browser", "reason": str(exc)})
        pending_ai.extend(p for p in pending_render if p["anchors"] and not p["link_pattern"])
        return {**_blank(), "status": "skipped", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the browser must never stop the run
        logger.error("browser rendering failed", extra={"source": "browser", "error": repr(exc)})
        return {**_blank(), "status": "failed", "errors": 1, "detail": repr(exc)}
    res = _blank()
    res["pages_rendered"] = len(rendered)
    for page in pages:
        html = rendered.get(page["url"])
        if html is None:
            res["errors"] += 1
            continue
        jobs, anchors = extract_postings(html, page["url"], cfg["filters"], page["link_pattern"])
        if not jobs and anchors and not page["link_pattern"]:
            pending_ai.append({**page, "anchors": anchors})
        for job in jobs:
            outcome = ingest(store, cfg, {**job, "source": page["source"],
                                          "company": job.get("company") or page["company"],
                                          "apply_filter": page["apply_filter"]})
            if outcome in res:
                res[outcome] += 1
    if res["errors"]:
        res["status"] = "partial" if rendered else "failed"
        res["detail"] = f"{res['errors']} pages could not be rendered"
    logger.info("browser rendering finished", extra={"source": "browser", **res})
    return res


def build_plan(cfg: dict, wanted: set[str], companies: list[dict], pending_ai: list,
               pending_render: list | None = None) -> tuple[list, dict]:
    """Return the spiders to run plus results for sources that have nothing to run."""
    src = cfg["sources"]
    plan, notes = [], {}
    base = {"cfg": cfg, "pending_ai": pending_ai, "pending_render": pending_render}
    if "arbeitnow" in wanted and src["arbeitnow"]["enabled"]:
        plan.append(("arbeitnow", ArbeitnowSpider, base))
    if "arbeitsagentur" in wanted and src["arbeitsagentur"]["enabled"]:
        plan.append(("arbeitsagentur", ArbeitsagenturSpider, base))
    if "jobicy" in wanted and src["jobicy"]["enabled"]:
        plan.append(("jobicy", JobicySpider, base))
    if "indeed" in wanted and src["indeed"]["enabled"] and src["indeed"].get("public_search"):
        plan.append(("indeed", IndeedSpider, base))
    if "linkedin" in wanted and src["linkedin"]["enabled"] and src["linkedin"].get("guest_search"):
        plan.append(("linkedin", LinkedInSpider, base))
    if "xing" in wanted and src["xing"]["enabled"] and src["xing"].get("public_search"):
        plan.append(("xing", XingSpider, base))
    if "working_student" in wanted and src["working_student"]["enabled"]:
        plan.append(("working_student", WorkingStudentSpider, base))
    if "career_pages" in wanted and src["career_pages"]["enabled"]:
        if companies:
            plan.append(("career_pages", CareerPagesSpider, {**base, "companies": companies}))
        else:
            notes["career_pages"] = {**_blank(), "status": "skipped",
                                     "detail": "no company is due (all were crawled within the revisit period)"}
    if "recheck" in wanted and cfg["recheck"]["enabled"]:
        plan.append(("recheck", RecheckSpider, {"cfg": cfg}))
    return plan, notes


def run(cfg: dict, only: set[str] | None = None, plan_override: list | None = None,
        settings_overrides: dict | None = None) -> dict:
    """Run one full collection cycle and return the per-source results."""
    started = time.time()
    wanted = set(only or ALL_SOURCES)
    results: dict[str, dict] = {}
    store = Store(cfg["paths"]["db"])
    try:
        try:
            edits = store.import_csv(cfg["paths"]["csv"])
            logger.info("imported hand edits from CSV", extra=edits)
        except Exception as exc:  # noqa: BLE001 - a broken CSV must not stop the run
            logger.error("could not read CSV edits", extra={"error": repr(exc)})

        ai = AIClient(cfg["ai"], cfg["paths"]["ai_cache"])
        if cfg["ai"]["enabled"] and not ai.available:
            logger.warning("AI features are off for this run, API key variable is empty",
                           extra={"source": "ai", "env": cfg["ai"]["api_key_env"]})

        for name, part in run_email_alerts(cfg, store, wanted).items():
            _merge_result(results, name, part)

        pending_ai: list[dict] = []
        pending_render: list[dict] = []
        pool_stats: dict = {}
        if plan_override is not None:
            plan, notes = plan_override, {}
        else:
            companies = []
            if "career_pages" in wanted and cfg["sources"]["career_pages"]["enabled"]:
                companies, pool_stats = select_companies(ai, cfg, store)
            plan, notes = build_plan(cfg, wanted, companies, pending_ai, pending_render)
        for name, part in notes.items():
            _merge_result(results, name, part)
        settings = {**build_settings(cfg), **(settings_overrides or {})}
        for name, part in run_spiders(plan, settings).items():
            _merge_result(results, name, part)

        browser_part = run_browser_phase(cfg, store, pending_render, pending_ai)
        if browser_part is not None:
            results["browser_rendering"] = browser_part

        ai_part = run_ai_extraction(ai, cfg, store, pending_ai)
        if ai_part is not None:
            results["ai_extraction"] = ai_part

        # A configured source that had no usable method is reported, never dropped silently.
        for name in ("linkedin", "indeed", "xing"):
            if name in wanted and cfg["sources"][name]["enabled"] and name not in results:
                results[name] = {**_blank(), "status": "skipped", "detail": "no collection method enabled"}
                logger.warning("source has no collection method enabled", extra={"source": name})

        for name, result in results.items():
            if name in METHODS:
                result["method"] = METHODS[name]
        rows = store.export_csv(cfg["paths"]["csv"])
        summary = {"profile": cfg["profile"].get("name"), "field": cfg["profile"].get("field"), "rows": rows, "by_status": store.counts_by_status(), "ai_calls": ai.calls_made,
                   "seconds": round(time.time() - started, 1), "sources": results}
        if pool_stats and "career_pages" in results:
            results["career_pages"]["pool"] = pool_stats
        logger.info("run finished", extra=summary)
        try:
            last_run = Path(cfg["paths"]["db"]).parent / "last_run.json"
            last_run.write_text(json.dumps(
                {**summary, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                ensure_ascii=False, default=str), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not write the run summary file", extra={"error": repr(exc)})
    finally:
        store.close()
    return results


def profile_files(directory: str = "profiles") -> list[Path]:
    return sorted(p for p in Path(directory).glob("*.yaml") if not p.name.endswith(".example.yaml"))


def run_all_profiles(directory: str, extra_args: list[str]) -> int:
    """Run every profile in its own process (a Scrapy reactor can start only once per process).

    One profile failing does not stop the others. Returns 1 if any profile failed.
    """
    files = profile_files(directory)
    if not files and Path("config.yaml").exists():
        files = [Path("config.yaml")]
    if not files:
        print("No profiles found. Create one with: python -m jobtracker.init", file=sys.stderr)
        return 2
    worst = 0
    for path in files:
        print(f"=== profile {path.stem} ===", flush=True)
        code = subprocess.run([sys.executable, "-m", "jobtracker.run", "--config", str(path), *extra_args]).returncode
        worst = max(worst, 1 if code else 0)
    return worst


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect new job postings and update the tracker.")
    parser.add_argument("--config", default=None, help="path to a config or profile file")
    parser.add_argument("--profile", help="profile name, short for --config profiles/<name>.yaml")
    parser.add_argument("--all-profiles", action="store_true",
                        help="run every profile in --profiles-dir, one after the other")
    parser.add_argument("--profiles-dir", default="profiles")
    parser.add_argument("--only", nargs="+", choices=ALL_SOURCES, help="run only these sources")
    parser.add_argument("--export-only", action="store_true",
                        help="apply CSV edits and rewrite the CSV without contacting any source")
    args = parser.parse_args(argv)
    load_env_file()            # credentials from .env in the project folder
    if args.all_profiles:
        extra = (["--only", *args.only] if args.only else []) + (["--export-only"] if args.export_only else [])
        return run_all_profiles(args.profiles_dir, extra)
    config_path = args.config or (
        str(Path(args.profiles_dir) / f"{args.profile}.yaml") if args.profile else "config.yaml")
    cfg = load_config(config_path)
    setup_logging(cfg["paths"]["log"], cfg["logging"]["level"], cfg["logging"]["scrapy_level"])
    if args.export_only:
        store = Store(cfg["paths"]["db"])
        try:
            logger.info("imported hand edits from CSV", extra=store.import_csv(cfg["paths"]["csv"]))
            logger.info("CSV exported", extra={"rows": store.export_csv(cfg["paths"]["csv"])})
        finally:
            store.close()
        return 0
    results = run(cfg, only=set(args.only) if args.only else None)
    # Exit code 1 only when at least one source failed and none delivered anything.
    states = [r["status"] for n, r in results.items() if n != "recheck"]
    return 1 if "failed" in states and not {"ok", "partial"} & set(states) else 0


if __name__ == "__main__":
    sys.exit(main())
