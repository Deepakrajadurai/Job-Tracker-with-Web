"""FastAPI application: sign up, log in (password or SSO), profile, dashboard, downloads."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import VALID_STATUSES, exports
from ..ai import AIClient
from ..config import DEFAULTS, load_config, load_env_file
from ..init import write_profile
from ..presets import FIELDS, LEVELS, build_profile
from ..store import Store
from .runner import Runner
from .sso import build_oauth, fetch_identity
from .users import UserDB

logger = logging.getLogger("jobtracker.web")
HERE = Path(__file__).parent
PROJECT_DIR = HERE.parents[1]

SOURCES = [("arbeitnow", "Arbeitnow"), ("arbeitsagentur", "Bundesagentur für Arbeit"), ("jobicy", "Jobicy (remote)"),
           ("working_student", "Working student"), ("career_pages", "Career pages"),
           ("browser_rendering", "Browser rendering"), ("ai_extraction", "AI extraction"),
           ("nextcloud_sync", "Copy to Nextcloud"),
           ("linkedin", "LinkedIn"), ("indeed", "Indeed"), ("xing", "Xing"), ("recheck", "Re-check of saved links")]
HINTS = {
    "linkedin": "LinkedIn forbids automated access. Connect a job alert mailbox in Settings, "
                "or switch on the guest search there at your own risk.",
    "indeed": "Indeed has no open feed and blocks automated search. Connect a job alert mailbox in Settings.",
    "xing": "Xing forbids automated access to its search pages. Connect a job alert mailbox in Settings.",
}
MAX_LOGIN_FAILURES, LOCK_SECONDS = 5, 900


def _secret_key(home: Path) -> str:
    key = os.environ.get("JOBTRACKER_SECRET_KEY")
    if key:
        return key
    path = home / "data" / ".secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        logger.warning("JOBTRACKER_SECRET_KEY is not set, generated one in %s", path)
    return path.read_text(encoding="utf-8").strip()


def create_app(home: str | Path | None = None, start_scheduler: bool | None = None) -> FastAPI:
    # Always the project folder (or JOBTRACKER_HOME), never the folder the terminal happens to be in,
    # so every start finds the same accounts and jobs.
    home = Path(home or os.environ.get("JOBTRACKER_HOME") or PROJECT_DIR).resolve()
    load_env_file(home / ".env")
    secret = _secret_key(home)
    users = UserDB(home / "data" / "app.db", secret)
    runner = Runner(home, users)
    oauth, providers = build_oauth()
    missing = [name for key, name in (("google", "Google (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)"),
                                      ("github", "GitHub (GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET)"),
                                      ("oidc", "OpenID Connect (OIDC_DISCOVERY_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET)"))
               if key not in providers]
    print(f"Accounts database: {home / 'data' / 'app.db'}", flush=True)
    print(f"Each user's jobs:  {home / 'data'}{os.sep}user<id>{os.sep}jobs.db", flush=True)
    print(f"Single sign-on buttons shown: {', '.join(providers.values()) or 'none'}", flush=True)
    if missing:
        print("Not configured, add to .env and restart to show them: " + "; ".join(missing), flush=True)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    # Official provider logos are not shipped. Put the files from the providers' brand pages into
    # jobtracker/web/static/logos/ (google.svg, github.svg, or .png) and they appear on the buttons.
    logos = {}
    for key in ("google", "github", "oidc"):
        for ext in ("svg", "png"):
            if (HERE / "static" / "logos" / f"{key}.{ext}").is_file():
                logos[key] = f"/static/logos/{key}.{ext}"
                break
    failures: dict[str, list[float]] = {}
    if start_scheduler is None:
        start_scheduler = os.environ.get("JOBTRACKER_SCHEDULER", "1") != "0"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(runner.daily(os.environ.get("JOBTRACKER_DAILY_AT", "04:00"))) \
            if start_scheduler else None
        yield
        if task:
            task.cancel()
        users.close()

    app = FastAPI(title="Job Tracker", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", max_age=14 * 86400,
                       https_only=os.environ.get("JOBTRACKER_COOKIE_SECURE", "0") == "1")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.users, app.state.runner, app.state.oauth = users, runner, oauth

    # ------------------------------------------------------------- helpers
    def render(request: Request, template: str, /, status_code: int = 200, **context):
        request.session.setdefault("csrf", secrets.token_urlsafe(24))
        user = users.by_id(request.session["uid"]) if request.session.get("uid") else None
        return templates.TemplateResponse(request, template, {
            "user": user, "csrf": request.session["csrf"], "providers": providers, "logos": logos,
            "flash": request.session.pop("flash", None), **context}, status_code=status_code)

    def redirect(request: Request, path: str, flash: str | None = None):
        if flash:
            request.session["flash"] = flash
        return RedirectResponse(path, status_code=303)

    def current_user(request: Request):
        user = users.by_id(request.session["uid"]) if request.session.get("uid") else None
        if user is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return user

    async def csrf(request: Request):
        form = await request.form()
        token = request.session.get("csrf")
        if not token or not secrets.compare_digest(str(form.get("csrf", "")), token):
            raise HTTPException(status_code=403, detail="The form expired. Go back, reload the page and try again.")

    def sign_in(request: Request, user):
        request.session.clear()           # new session on login
        request.session["uid"] = user["id"]
        return redirect(request, "/dashboard" if runner.profile_path(user["id"]).exists() else "/profile")

    def user_config(user) -> dict | None:
        path = runner.profile_path(user["id"])
        return load_config(path) if path.exists() else None

    def open_store(user) -> tuple[Store, dict]:
        cfg = user_config(user)
        if cfg is None:
            raise HTTPException(status_code=303, headers={"Location": "/profile"})
        return Store(cfg["paths"]["db"]), cfg

    # ---------------------------------------------------------------- auth
    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return RedirectResponse("/dashboard" if request.session.get("uid") else "/login", status_code=303)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True, "version": os.environ.get("JOBTRACKER_VERSION", "dev")}

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request):
        return render(request, "auth.html", mode="signup")

    @app.post("/signup", dependencies=[Depends(csrf)])
    def signup(request: Request, email: str = Form(""), name: str = Form(""), password: str = Form("")):
        try:
            user = users.create(email, name, password)
        except ValueError as exc:
            return render(request, "auth.html", status_code=400, mode="signup", error=str(exc), email=email, name=name)
        return sign_in(request, user)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "auth.html", mode="login")

    @app.post("/login", dependencies=[Depends(csrf)])
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        key = email.strip().lower()
        recent = [t for t in failures.get(key, []) if time.time() - t < LOCK_SECONDS]
        if len(recent) >= MAX_LOGIN_FAILURES:
            return render(request, "auth.html", status_code=429, mode="login", email=email,
                          error="Too many failed attempts. Please wait 15 minutes.")
        user = users.authenticate(email, password)
        if user is None:
            failures[key] = recent + [time.time()]
            return render(request, "auth.html", status_code=401, mode="login", email=email,
                          error="Email or password is not correct.")
        failures.pop(key, None)
        return sign_in(request, user)

    @app.post("/logout", dependencies=[Depends(csrf)])
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/auth/{provider}")
    async def sso_start(request: Request, provider: str):
        if provider not in providers:
            raise HTTPException(404)
        base = os.environ.get("JOBTRACKER_BASE_URL", "").rstrip("/")
        callback = f"{base}/auth/{provider}/callback" if base else str(request.url_for("sso_callback", provider=provider))
        return await oauth.create_client(provider).authorize_redirect(request, callback)

    @app.get("/auth/{provider}/callback", name="sso_callback")
    async def sso_callback(request: Request, provider: str):
        if provider not in providers:
            raise HTTPException(404)
        try:
            identity = await fetch_identity(oauth, provider, request)
            if not identity.get("email"):
                raise ValueError("The provider did not share an email address.")
            user = users.from_sso(provider, identity["subject"], identity["email"], identity.get("name", ""),
                                  bool(identity.get("email_verified")))
        except ValueError as exc:
            return redirect(request, "/login", str(exc))
        except Exception:  # noqa: BLE001 - cancelled or failed at the provider
            logger.exception("single sign-on failed")
            return redirect(request, "/login", "Sign in did not complete. Please try again.")
        return sign_in(request, user)

    # ------------------------------------------------------------- profile
    @app.get("/profile", response_class=HTMLResponse)
    def profile_form(request: Request, user=Depends(current_user)):
        cfg = user_config(user)
        current = {"field": "software_ai", "levels": ["working_student"], "locations": "Germany", "keywords": ""}
        if cfg:
            current = {"field": cfg["profile"].get("field_key", "custom"), "levels": cfg["profile"].get("levels", []),
                       "locations": ", ".join(cfg["search"]["locations"]),
                       "keywords": ", ".join(cfg["filters"]["title_include_any"])
                       if cfg["profile"].get("field_key") == "custom" else ""}
        return render(request, "profile.html", fields=FIELDS, levels=LEVELS, current=current,
                      cfg=cfg, ai_ready=bool(os.environ.get(DEFAULTS["ai"]["api_key_env"])))

    @app.post("/profile", dependencies=[Depends(csrf)])
    def profile_save(request: Request, user=Depends(current_user), field: str = Form("software_ai"),
                     levels: list[str] = Form(default=[]), locations: str = Form("Germany"),
                     keywords: str = Form(""), describe: str = Form("")):
        split = lambda text: [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]  # noqa: E731
        words, terms, label = split(keywords), None, None
        try:
            if field == "custom" and not words and describe.strip():
                ai = AIClient(DEFAULTS["ai"], str(home / "data" / "ai_cache.json"))
                suggestion = ai.suggest_profile(describe.strip()[:300])
                words, terms, label = suggestion["keywords"], suggestion["search_terms"], describe.strip()[:80]
            profile = build_profile(f"user{user['id']}", field if field in FIELDS else "custom",
                                    [lv for lv in levels if lv in LEVELS], split(locations)[:6],
                                    words[:30], terms, label)
            if users.get_setting(user["id"], "linkedin_guest") == "1":
                profile["sources"]["linkedin"] = {"guest_search": True}
            write_profile(profile, str(home / "profiles"), force=True)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return redirect(request, "/profile", f"Could not save the search: {exc}")
        return redirect(request, "/dashboard", "Search saved. Press “Search now” or wait for the daily run.")

    # ----------------------------------------------------------- dashboard
    def last_run(cfg: dict) -> dict | None:
        try:
            return json.loads((Path(cfg["paths"]["db"]).parent / "last_run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def badge(key: str, result: dict) -> str:
        if result["status"] == "skipped":
            return "Not set up" if key in HINTS else "Skipped"
        return {"ok": "Completed", "partial": "Partial", "failed": "Failed",
                "blocked": "Not allowed"}.get(result["status"], result["status"])

    def explain(key: str, result: dict) -> str:
        """One plain sentence per source, so a 0 is never left unexplained."""
        status, detail = result["status"], result.get("detail", "")
        new, known, filtered = result.get("new", 0), result.get("duplicate", 0), result.get("filtered", 0)
        if status in ("skipped", "blocked") and key in HINTS:
            return HINTS[key]
        parts = []
        if key == "career_pages":
            pool = result.get("pool") or {}
            crawled = result.get("companies_crawled", 0)
            parts.append(f"{crawled} companies read in this run, {pool.get('pool', crawled)} in the weekly rotation.")
            if pool.get("shortfall"):
                parts.append(f"Target is {pool.get('target', 50)} per run. Not reached because: {pool['shortfall']}.")
        if key == "recheck":
            gone = result.get("marked_gone", 0)
            return (f"{result.get('checked', 0)} saved links checked, {gone} marked as no longer available."
                    + (f" {detail}" if detail else ""))
        if detail and not (key == "career_pages" and status == "ok"):
            parts.append(detail[0].upper() + detail[1:] + ("" if detail.endswith(".") else "."))
        if status in ("ok", "partial") and new == 0:
            if filtered:
                parts.append(f"{filtered} postings were found but none matched your search filters "
                             "(title keywords, level or location). You can widen them under My search.")
            elif known:
                parts.append("Everything found is already in your list.")
            elif not detail:
                parts.append("The source returned no postings for your search this time.")
        elif filtered:
            parts.append(f"{filtered} more did not match your filters.")
        return " ".join(parts)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(current_user), status: str = "", q: str = ""):
        store, cfg = open_store(user)
        try:
            jobs = store.list_jobs(status or None, q.strip() or None, limit=500)
            counts = store.counts_by_status()
            total = store.count()
        finally:
            store.close()
        run = last_run(cfg)
        rows = []
        for key, label in SOURCES:
            result = (run or {}).get("sources", {}).get(key)
            if result is None:
                continue
            rows.append({"label": label, **result, "note": explain(key, result),
                         "badge": badge(key, result)})
        return render(request, "dashboard.html", jobs=jobs, counts=counts, total=total, statuses=VALID_STATUSES,
                      status=status, q=q, run=run, sources=rows, cfg=cfg, running=runner.is_running(user["id"]))

    @app.post("/jobs/status", dependencies=[Depends(csrf)])
    def set_status(request: Request, user=Depends(current_user), link: str = Form(...), status: str = Form(...),
                   back: str = Form("/dashboard")):
        if status not in VALID_STATUSES:
            raise HTTPException(400, "unknown status")
        store, cfg = open_store(user)
        try:
            if store.get(link) is None:
                raise HTTPException(404)
            store.set_status(link, status)
            store.export_csv(cfg["paths"]["csv"])   # keep the CSV in step, the next run imports it
        finally:
            store.close()
        return RedirectResponse(back if back.startswith("/dashboard") else "/dashboard", status_code=303)

    @app.post("/run", dependencies=[Depends(csrf)])
    def run_now(request: Request, user=Depends(current_user)):
        outcome = runner.start(user["id"])
        message = {
            "started": "Search started. This page refreshes until it is done.",
            "running": "A search is already running.",
            "wait": f"Please wait {runner.minutes_until_allowed(user['id'])} more minutes, "
                    "so the job sites are not queried too often.",
            "no-profile": "Set up your search first.",
        }[outcome]
        return redirect(request, "/dashboard", message)

    @app.get("/export.{fmt}")
    def export(fmt: str, user=Depends(current_user), status: str = "", q: str = ""):
        store, cfg = open_store(user)
        try:
            jobs = store.list_jobs(status or None, q.strip() or None)
        finally:
            store.close()
        if fmt == "csv":
            body, media = exports.to_csv(jobs), "text/csv; charset=utf-8"
        elif fmt == "xlsx":
            body, media = exports.to_xlsx(jobs), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "pdf":
            body, media = exports.to_pdf(jobs, f"Job tracker: {cfg['profile'].get('field') or 'jobs'}"), "application/pdf"
        else:
            raise HTTPException(404)
        return Response(body, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="jobs.{fmt}"'})

    # ------------------------------------------------------------ settings
    @app.get("/settings", response_class=HTMLResponse)
    def settings_form(request: Request, user=Depends(current_user)):
        uid = user["id"]
        methods = (["Email and password"] if user["password_hash"] else []) + [
            {"google": "Google", "github": "GitHub", "oidc": os.environ.get("OIDC_NAME", "Single sign-on")}.get(p, p)
            for p in users.providers_for(uid)]
        return render(request, "settings.html", sign_in_methods=methods,
                      nc_url=users.get_setting(uid, "nc_url") or os.environ.get("JOBTRACKER_NEXTCLOUD_DEFAULT_URL", ""),
                      nc_user=users.get_setting(uid, "nc_user"),
                      nc_folder=users.get_setting(uid, "nc_folder") or "Job Tracker",
                      has_nc_password=bool(users.get_setting(uid, "nc_password")),
                      imap_host=users.get_setting(uid, "imap_host"),
                      imap_user=users.get_setting(uid, "imap_user"),
                      has_password=bool(users.get_setting(uid, "imap_password")),
                      linkedin_guest=users.get_setting(uid, "linkedin_guest") == "1")

    @app.post("/settings", dependencies=[Depends(csrf)])
    def settings_save(request: Request, user=Depends(current_user), imap_host: str = Form(""),
                      imap_user: str = Form(""), imap_password: str = Form(""),
                      linkedin_guest: str = Form(""), forget_mailbox: str = Form(""),
                      nc_url: str = Form(""), nc_user: str = Form(""), nc_password: str = Form(""),
                      nc_folder: str = Form("Job Tracker"), forget_nextcloud: str = Form("")):
        uid = user["id"]
        if forget_nextcloud:
            for key in ("nc_url", "nc_user", "nc_password", "nc_folder"):
                users.set_setting(uid, key, "")
        else:
            if nc_url.strip() and not nc_url.strip().startswith(("http://", "https://")):
                return redirect(request, "/settings", "The Nextcloud address must start with http:// or https://")
            users.set_setting(uid, "nc_url", nc_url.strip().rstrip("/")[:300])
            users.set_setting(uid, "nc_user", nc_user.strip()[:200])
            users.set_setting(uid, "nc_folder", nc_folder.strip().strip("/")[:200])
            if nc_password:
                users.set_setting(uid, "nc_password", nc_password)
        if forget_mailbox:
            for key in ("imap_host", "imap_user", "imap_password"):
                users.set_setting(uid, key, "")
        else:
            users.set_setting(uid, "imap_host", imap_host.strip()[:200])
            users.set_setting(uid, "imap_user", imap_user.strip()[:200])
            if imap_password:                       # empty field keeps the stored password
                users.set_setting(uid, "imap_password", imap_password)
        users.set_setting(uid, "linkedin_guest", "1" if linkedin_guest else "")
        path = runner.profile_path(uid)
        if path.exists():                           # keep the profile file in step with the switch
            header = "".join(line for line in path.read_text(encoding="utf-8").splitlines(True) if line.startswith("#"))
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            data.setdefault("sources", {}).setdefault("linkedin", {})["guest_search"] = bool(linkedin_guest)
            path.write_text(header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
                            encoding="utf-8")
        return redirect(request, "/settings", "Settings saved.")

    return app
