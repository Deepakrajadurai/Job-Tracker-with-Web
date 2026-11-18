# Job Tracker

Collects new job postings every day at 04:00, stores them in SQLite, and keeps a CSV you can
open and edit by hand. Built with Scrapy. Each source is an isolated spider or module, so one
broken source never stops the others.

Every row has four columns: `link`, `position`, `date of publication`, `status`.
`status` is one of `applied`, `not applied`, `no longer available`.

## Web interface

```bash
python -m jobtracker.web        # then open http://127.0.0.1:8000
```

Anyone can create an account with email and password or with single sign-on. After the first
login a short form asks for field, level and location (the same questions as the wizard below).
Each account gets its own search, database and files under `data/user<id>/`.

What the pages do:

- **Jobs.** All collected postings with search, status filter and a status dropdown per row.
  "Search now" starts a run for this account (at most one every 30 minutes). The page shows
  the result of every source after each run and says what to do about sources that are not set up.
- **Downloads.** CSV, Excel and PDF buttons export the current list. A filter that is set also
  applies to the download.
- **My search.** Change field, level, locations and keywords.
- **Settings.** Connect the mailbox that receives job alerts (stored encrypted, opened read-only)
  and the LinkedIn guest search switch.

The web app runs every account once a day at 04:00 server time (`JOBTRACKER_DAILY_AT`). If cron
or Task Scheduler already runs the tracker, set `JOBTRACKER_SCHEDULER=0` so it does not run twice.

### Single sign-on

A provider button appears on the login page as soon as its two values are set in `.env`.

| Provider | Where to create the client | Redirect URI to register |
|---|---|---|
| Google | Google Cloud console, APIs and Services, Credentials, OAuth client ID, type Web | `<site>/auth/google/callback` |
| GitHub | GitHub, Settings, Developer settings, OAuth Apps | `<site>/auth/github/callback` |
| Microsoft or any OpenID Connect provider | The provider's app registration, then the four `OIDC_*` values | `<site>/auth/oidc/callback` |

`<site>` is `http://127.0.0.1:8000` locally, or your public address, which you also put in
`JOBTRACKER_BASE_URL`. An SSO login joins an existing account only when the provider confirms
that the email address is verified.

### Putting it online

- The web app needs a machine that is always on. GitHub Actions can run the daily collection
  but cannot host a website.
- Serve it through HTTPS (a reverse proxy such as Caddy or nginx, or the HTTPS of a hosting
  platform), set `JOBTRACKER_COOKIE_SECURE=1`, `JOBTRACKER_BASE_URL` and a fixed
  `JOBTRACKER_SECRET_KEY`. Start with `JOBTRACKER_HOST=0.0.0.0 python -m jobtracker.web`.
- Keep the `data/` and `profiles/` folders on persistent storage. They hold all accounts and jobs.
- All accounts share the one AI key on the server and its free quota.
- Not included yet: email verification, password reset, account deletion in the UI and an admin
  page. Add these, plus a privacy notice, before opening the site to the public. Logins lock for
  15 minutes after 5 wrong passwords.

## Profiles: one per person and field

The tool assumes no field. Each person describes what they want once and gets a **profile**:
a small file in `profiles/` with their search phrases, title filters and locations, plus their
own database, CSV and log. A software student and a mechanical engineering student can share
one installation and never see each other's jobs.

```bash
python -m jobtracker.init
```

The wizard asks four things:

1. **Profile name**, for example `deepak` or `friend`.
2. **Field.** Presets exist for software and AI, mechanical engineering, electrical engineering
   and business. "Something else" takes any field (see the next paragraph).
3. **Level.** Working student, internship, thesis, entry level or experienced. Several are fine.
4. **Locations.** Countries or cities.

For a field without a preset you either type your own title keywords, or you describe the jobs
in plain words and the AI model turns that into search phrases and keywords (one API call).

The same without questions:

```bash
python -m jobtracker.init --name friend --field mechanical --level working_student thesis --location Germany Austria
python -m jobtracker.init --name sam --field custom --keywords "Architektur, BIM, Bauingenieur" --level internship
python -m jobtracker.init --name kim --describe "biotech lab work, cell culture, QA in pharma" --level entry
```

What the answers control:

| Answer | Effect |
|---|---|
| Field | Search phrases for LinkedIn, Xing and boards. Title keywords for Arbeitnow and career pages. The kind of employers the AI suggests. Starter companies where known. |
| Level | Prefix of the search phrases (`Werkstudent ...`). Words a title must contain. Senior titles are dropped for student and entry levels. |
| Locations | Search location and the place filter for career pages. |

The result is a readable YAML file, for example `profiles/friend.yaml`. Presets are only a
starting point, so add or remove terms there at any time. `profiles/*.example.yaml` show two
generated profiles. Files ending in `.example.yaml` are never run.

Run one profile or all of them:

```bash
python -m jobtracker.run --profile friend
python -m jobtracker.run --all-profiles      # every file in profiles/, one after the other
```

Each profile writes to `data/<name>/jobs.csv`, `data/<name>/jobs.db` and `logs/<name>.log`.
With `--all-profiles` one failing profile does not stop the others. Job alert emails are shared
by all profiles that use the same mailbox, so give each person their own mailbox variables
(`email.host_env` and so on in the profile) if both use alerts.

## Sources and collection methods

Three methods are used, always in this order of preference.

- **Method C, API or feed.** Structured data from an official or documented endpoint. No HTML.
- **Method A, HTTP and HTML.** Fetch a public page, read schema.org `JobPosting` JSON-LD, then
  job links. robots.txt is obeyed.
- **Method B, browser.** Render a JavaScript page in headless Chromium and run Method A on the
  result. Optional (`browser.enabled`). It is only a second attempt for pages that were already
  fetched successfully and had no job data in their static HTML.

| Source | Method | Notes |
|---|---|---|
| Arbeitnow | C | Official API |
| Bundesagentur für Arbeit | C | Jobsuche endpoint, Germany, strong for Werkstudent and internships |
| Jobicy | C | Official API, remote jobs, asked at most once per hour |
| Company career pages | C, then A, then B, then AI | Greenhouse, Lever, Ashby, SmartRecruiters, Personio, Recruitee, Workable through their public job board APIs. Other pages as HTML. |
| Working student boards | C, then A and B | Arbeitnow student roles plus boards you configure |
| LinkedIn | Alert emails, optional A | Guest search is opt in |
| Indeed | Alert emails, optional A | `public_search` is opt in and usually refused |
| Xing | Alert emails, optional A | `public_search` is opt in and forbidden by robots.txt |

**What the methods do not do.** LinkedIn, Indeed and Xing have no API that is open to
individuals, and they refuse automated access through robots.txt, login walls or bot
protection. This tool does not log in to them, does not disguise the browser and does not
solve or bypass challenges. Method B is therefore not a way around a refusal. When one of these
sites refuses a request, the source is reported as `blocked` and job alert emails stay the
working method. The better substitute is what the table shows: the same employers publish
their jobs on their own career systems and on the Bundesagentur, which can be read directly.

To switch on Method B:

```bash
pip install playwright
playwright install chromium
```

Then set `browser.enabled: true` in the profile.

## Company rotation (career pages)

Companies publish many jobs only on their own career page, so the tracker keeps a pool of
companies per profile and works through it.

- Each run reads up to `companies_per_run` companies (default 50).
- A company that was read is not read again for `revisit_days` (default 7). The ones waiting
  longest go first.
- The pool starts with the companies in the profile. When fewer than 50 are due, the AI model
  is asked for new employers in your field and area (large companies, small and medium ones and
  startups) and is told which ones are already known. They join the pool and are read at once.
  This takes one AI request on such a day.
- When a company comes up again after a week and still lists a position that is already stored,
  nothing is added. The link is the key, and your status for that posting stays as it is.
- A company whose page fails 3 times in a row is retired from the rotation.

Reaching 50 new companies every day needs the AI key, because the pool has to grow to about
350 companies in the first week. Without a key the run reads the companies that are due and the
log says how many were missing. In a narrow field or a small area the model may also run out of
real employers. The summary then shows the shortfall instead of padding the list.

## What a run does

1. Reads your hand edits from `data/jobs.csv` (for example a status you changed to `applied`).
2. Reads job alert emails for LinkedIn, Indeed and Xing if a mailbox is configured.
3. Asks the AI model once for companies hiring in your area (cached for a week).
4. Runs all spiders in one Scrapy process: Arbeitnow, working student jobs, career pages,
   Xing, optional LinkedIn guest search, and the re-check of stored links.
5. Sends listing pages that had no structured data to the AI model in one batched request.
6. Writes `data/jobs.csv` again and logs a summary per source.

Rules the store enforces:

- New postings get the status `not applied`.
- The link is the key. Tracking parameters are stripped first, so one job is stored once.
- A run never overwrites an existing row. `applied` stays `applied`.
- A stored link that returns 404 or 410, redirects to the homepage, or shows a "closed" notice
  becomes `no longer available`. Only the status changes.
- A link that is unreachable (DNS error, timeout) is marked after 3 failed runs in a row.
- A blocked check (403, 429, robots.txt) is no verdict and changes nothing.
- Dates are stored as `YYYY-MM-DD`. A date that cannot be parsed is stored empty and logged.

## Project layout

| Path | Purpose |
|---|---|
| `jobtracker/run.py` | Entry point and orchestration |
| `jobtracker/init.py` | Profile wizard |
| `jobtracker/presets.py` | Field and level presets |
| `jobtracker/config.py` | Defaults and config loading |
| `jobtracker/store.py` | SQLite store, CSV export and import |
| `jobtracker/ingest.py` | Normalize, filter, dedupe, store (shared by all sources) |
| `jobtracker/dates.py` | Date normalization |
| `jobtracker/links.py` | Link normalization and ATS detection |
| `jobtracker/availability.py` | Decides whether a posting is still online |
| `jobtracker/extract.py` | JSON-LD and link extraction for listing pages |
| `jobtracker/exports.py` | CSV, Excel and PDF downloads |
| `jobtracker/web/` | Web interface: accounts, SSO, dashboard, settings, daily scheduler |
| `jobtracker/ai.py` | Gemini / Groq client with cache, batching and call cap |
| `jobtracker/email_alerts.py` | IMAP reader for LinkedIn, Indeed and Xing job alerts |
| `jobtracker/scrapy_ext.py` | Scrapy settings, backoff retry middleware, store pipeline |
| `jobtracker/logging_setup.py` | JSON logging to stdout and file |
| `jobtracker/spiders/arbeitnow.py` | Arbeitnow public API |
| `jobtracker/spiders/arbeitsagentur.py` | Bundesagentur für Arbeit Jobsuche |
| `jobtracker/spiders/jobicy.py` | Jobicy remote jobs API |
| `jobtracker/spiders/indeed.py` | Indeed public search (opt in) |
| `jobtracker/browser.py` | Method B, headless browser rendering |
| `jobtracker/spiders/working_student.py` | Student roles from Arbeitnow plus configured boards |
| `jobtracker/spiders/career_pages.py` | Greenhouse, Lever, Ashby, SmartRecruiters, Personio, Recruitee, Workable, generic pages |
| `jobtracker/spiders/linkedin.py` | LinkedIn logged-out search (opt in) |
| `jobtracker/spiders/xing.py` | Xing public search page |
| `jobtracker/spiders/recheck.py` | Re-check of stored links |
| `tests/` | 121 tests, all network calls mocked |
| `profiles/` | One YAML file per person, plus two examples |
| `config.example.yaml` | Every available key with comments |
| `.github/workflows/daily.yml` | GitHub Actions schedule |
| `scripts/run_daily.sh`, `scripts/run_daily.bat` | Wrappers for cron and Task Scheduler |

## Setup

Python 3.10 or newer.

```bash
cd job-tracker
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the values
python -m jobtracker.init          # create your profile
python -m pytest                   # optional, runs offline
```

### AI key (free tier)

The default model is **Google Gemini** (`gemini-3.8-flash`), which Google offers on a free tier
without a credit card.

1. Open https://aistudio.google.com/apikey and create a key.
2. Put it in `.env` as `GEMINI_API_KEY=...`. Every command reads `.env` from the project folder,
   on Windows too. A variable set in the shell wins over the file.

The tool makes at most `ai.max_calls_per_run` requests per run (default 5). A normal day needs
0 to 2, because the company list is cached for 7 days and listing pages go out in batches.
Free-tier limits and model names change, so check the limits shown in AI Studio. If the model
name is retired, set `ai.model` to a current Flash model. On the free tier Google may use
prompts to improve its products. The prompts here contain only your search terms and public
links from job pages.

To use **Groq** instead, create a key at https://console.groq.com/keys and set
`ai.provider: groq`, `ai.model: openai/gpt-oss-120b`, `ai.api_key_env: GROQ_API_KEY`.

Without a key the run still works. AI steps are skipped and logged.

`ai.use_search_grounding: true` lets Gemini use Google Search for the company list, which makes
"currently hiring" more accurate. It has its own free allowance, so check it in AI Studio first.
Without grounding the model answers from what it learned in training, so treat the list as
suggestions. Every suggested page is fetched and only real postings found there are stored.

### Job alert mailbox (optional, needed for Indeed)

LinkedIn, Indeed and Xing send job alerts by email when you ask for them. Reading those emails
is the compliant way to cover these three sites.

1. Create job alerts on each site for your searches, with daily delivery.
2. Use a mailbox with IMAP access. For Gmail, turn on 2-step verification and create an app
   password. A separate mailbox or a Gmail label with a filter keeps things tidy.
3. Set `JOBTRACKER_IMAP_HOST` (for example `imap.gmail.com`), `JOBTRACKER_IMAP_USER` and
   `JOBTRACKER_IMAP_PASSWORD`. Set `email.folder` if you use a label.

The mailbox is opened read-only. Nothing is deleted or marked as read.

### Fine tune a profile

A profile only contains what differs from the defaults. `config.example.yaml` documents every
key, and any of them can be added to a profile.

- `search.queries` and `search.locations` drive LinkedIn, Xing and board searches.
- `filters.title_include_any` and `filters.title_require_any` filter the sources that return
  every open job (Arbeitnow and company career pages). Terms of 6 letters or fewer match whole
  words only, so `Intern` does not match `International`.
- `filters.title_exclude_any` applies everywhere.
- `sources.career_pages.companies` lists companies. The AI suggestions for your field are added.
- `ai.max_calls_per_run` is the AI call cap, per profile.

A single `config.yaml` in the project folder still works if you prefer one file and no profiles.

### Run it by hand

```bash
python -m jobtracker.run --profile deepak     # .env in the project folder is read automatically
python -m jobtracker.run --profile deepak --only arbeitnow career_pages   # some sources
python -m jobtracker.run --profile deepak --export-only                   # apply CSV edits only
python -m jobtracker.run --all-profiles
```

Results are in `data/<name>/jobs.csv`. The log is in `logs/<name>.log` (JSON lines) and on
stdout. The last log line of a run, `run finished`, has the counts and the state of every source.
The exit code is 1 only when at least one source failed and no source delivered anything.

### Editing the CSV

Open `data/<name>/jobs.csv`, change the `status` cell, save as CSV. The next run imports the change
before it does anything else. A status that is not one of the three values is ignored and
logged. A row you add by hand is imported. A row you delete comes back, because the database is
the primary store. Close the file in Excel before 04:00, since Excel locks open files.

## Scheduling

### Linux and macOS (cron)

```cron
0 4 * * * /full/path/to/job-tracker/scripts/run_daily.sh >> /full/path/to/job-tracker/logs/cron.log 2>&1
```

Add it with `crontab -e`. Cron uses the system time zone. The machine must be on at 04:00.
The script runs every profile in `profiles/`.

### Windows (Task Scheduler)

```bat
setx GEMINI_API_KEY "your-key"
schtasks /Create /TN "JobTracker" /TR "C:\full\path\job-tracker\scripts\run_daily.bat" /SC DAILY /ST 04:00
```

In the Task Scheduler GUI you can also tick "Wake the computer to run this task" and "Run task
as soon as possible after a scheduled start is missed".

### GitHub Actions (free, no machine needed)

`.github/workflows/daily.yml` runs on the cron `0 4 * * *`.

1. Push this project to a **private** repository. Your application history is in the state files.
2. Commit your files in `profiles/`. They hold no secrets. The workflow runs all of them.
3. Under Settings, Secrets and variables, Actions, add `GEMINI_API_KEY` and, if used, the three
   `JOBTRACKER_IMAP_*` secrets.

GitHub cron runs in **UTC**. 04:00 in Berlin is `0 3 * * *` in winter and `0 2 * * *` in summer.
Scheduled runs can start some minutes late.

A runner starts empty every time, so the state has to survive between runs. The workflow commits
the database, CSV and AI cache of every profile under `data/` back to the repository after each run.
Storing them as an artifact and downloading it at the start of the next run also works, but
artifacts expire and a commit gives you history. To change a status, edit `data/<name>/jobs.csv` on
GitHub or pull, edit and push. GitHub pauses schedules in repositories without activity for 60
days. The daily state commit normally prevents that.

## Optional upgrade: Google Sheets

The tool does not depend on Google Sheets. Two ways to use it anyway:

**Manual.** In a sheet choose File, Import, Upload, pick `data/<name>/jobs.csv`, "Replace current sheet".

**Sheets API.** Create a Google Cloud service account, enable the Sheets API, share your sheet
with the service account address, `pip install gspread`, and run a small script after each run.
It first pulls the sheet into the CSV so your status edits are kept, then pushes the fresh CSV.

```python
# sheets_sync.py   usage: python sheets_sync.py pull   |   python sheets_sync.py push
import csv, sys, gspread
ws = gspread.service_account(filename="service_account.json").open("Job Tracker").sheet1
if sys.argv[1] == "pull":      # run BEFORE jobtracker, brings your edits into the CSV
    rows = ws.get_all_values()
    if rows:
        with open("data/deepak/jobs.csv", "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
else:                          # run AFTER jobtracker
    with open("data/deepak/jobs.csv", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    ws.clear()
    ws.update(rows, "A1")
```

A data validation dropdown on the status column keeps the three values clean.

## Known limitations

**All sources.** This build was tested with mocked network responses only. Check the first real
run in the log. Site layouts and APIs change, and a source that returns nothing logs a warning.

**LinkedIn.** LinkedIn's terms and robots.txt forbid automated access and it has no public job
API. The compliant method is job alert emails, which is the default. `guest_search: true` turns
on the logged-out search endpoint that LinkedIn's own public job pages use. That is your
decision and your risk. It is rate limited, may return HTTP 429 or 999, and can stop working at
any time. Runs from GitHub Actions are blocked more often than runs from a home connection.
Without the opt-in, stored LinkedIn links cannot be re-checked, so they never switch to
`no longer available` on their own. Alert emails give the email date as publication date.

**Indeed.** Indeed no longer offers RSS feeds or an open job search API, and its search pages sit
behind bot protection. It cannot be scraped reliably, so there is no Indeed spider. The only
implemented method is job alert emails. Without a mailbox Indeed is reported as `skipped` in
every run summary. Some alert links are tracking redirects without a job id and are dropped.
Stored Indeed links are usually blocked on re-check, so their status stays as it is.

**Xing.** No public API. Xing's robots.txt forbids its job search pages (confirmed in a real
run in November 2026), so `public_search` is off by default. If you switch it on and Xing still
forbids it, the source is reported as `blocked`. Alert emails are the working method. The search page shows no reliable
publication date, so dates from this spider are usually empty.

**Bundesagentur für Arbeit.** The Jobsuche endpoint is the one the agency's own website uses.
It is documented by the community, not offered by the agency as a supported developer API, so
it can change or be closed without notice. The parser accepts the field names of both known
versions (`api_version` v4 and v6). Only German locations are searched.

**Jobicy.** Remote jobs only. Jobicy asks for at most one automated request per hour and for
its link to be kept as the source. Both are built in, so a second run within the hour skips it.

**Arbeitnow.** The API has no search parameters, so the spider reads the newest pages
(`max_pages`) and filters by title. Older postings beyond those pages are not seen.

**Working student boards.** Student roles come from the Arbeitnow API out of the box. Other
boards differ too much for a ready-made parser. Add their search URLs under
`sources.working_student.boards`. Pages with schema.org `JobPosting` data work directly, other
pages go through the AI extraction, and boards that need a login or JavaScript do not work.

**Company career pages.** Companies on Greenhouse, Lever, Ashby, SmartRecruiters or Personio
work through public APIs. The slugs in the example config are from memory and were not verified
live, so check them once. Large companies with their own career sites (Google, Amazon,
Microsoft, SAP, Siemens) render jobs with JavaScript and mostly return nothing through the
generic path. Use their job alert emails or add an ATS based entry where one exists. Ashby job
pages answer HTTP 200 even for closed jobs, so those rows may not switch to
`no longer available`.

**Fields.** Starter company lists exist mainly for the software preset, because the supported
career systems (Greenhouse, Lever, Ashby) are common in tech. Large industrial employers mostly
use systems without an open API. For other fields the main sources are Arbeitnow, job alert
emails, Xing, AI suggested career pages and companies you add yourself. Preset keywords are a
reasonable start, not a complete vocabulary of a field.

**AI.** The company list can contain outdated or wrong links. Failures are logged per company.
Links returned by the AI extraction are accepted only if they were really on the page.

**Re-check.** At most `recheck.max_per_run` links per run, least recently checked first.
`applied` rows are kept as `applied` even if the posting disappears. Set
`recheck.preserve_applied: false` if you prefer them to switch to `no longer available`.

## Not included

Notifications, and the account features listed under "Putting it online".
