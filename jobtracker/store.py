"""SQLite tracking store (primary) with a hand-editable CSV export."""
from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import STATUS_APPLIED, STATUS_GONE, STATUS_NOT_APPLIED, VALID_STATUSES
from .dates import normalize_date
from .links import normalize_link

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["link", "position", "date of publication", "status"]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs (
    link            TEXT PRIMARY KEY,
    position        TEXT NOT NULL,
    date_published  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '{STATUS_NOT_APPLIED}'
                    CHECK (status IN ('{STATUS_APPLIED}', '{STATUS_NOT_APPLIED}', '{STATUS_GONE}')),
    source          TEXT NOT NULL DEFAULT '',
    company         TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    first_seen      TEXT NOT NULL,
    last_checked    TEXT,
    fail_count      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS companies (
    key            TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    entry          TEXT NOT NULL,          -- JSON: ats + slug (+ params) or url
    origin         TEXT NOT NULL DEFAULT 'config',
    added_at       TEXT NOT NULL,
    last_crawled   TEXT,                   -- YYYY-MM-DD of the last attempt
    last_ok        INTEGER,
    last_postings  INTEGER NOT NULL DEFAULT 0,
    fail_count     INTEGER NOT NULL DEFAULT 0
);
"""


def company_key(entry: dict) -> str:
    """Stable identity of a company entry: its ATS board, or its careers URL."""
    from .links import detect_ats

    ats, slug, url = entry.get("ats"), entry.get("slug"), entry.get("url")
    if not ats and url:
        detected = detect_ats(url)
        if detected:
            ats, slug = detected
    if ats and slug:
        return f"{ats}:{str(slug).lower()}"
    normalized = normalize_link(url)
    return f"url:{normalized.lower()}" if normalized else ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ writes
    def add_job(self, link: str, position: str, date_published: str = "", source: str = "",
                company: str = "", location: str = "", status: str = STATUS_NOT_APPLIED) -> bool:
        """Insert a posting. Returns False (and changes nothing) when the link already exists."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO jobs (link, position, date_published, status, source, company,"
            " location, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link, position, date_published or "", status, source, company or "", location or "", _now()),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_status(self, link: str, status: str) -> bool:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        cur = self.conn.execute(
            "UPDATE jobs SET status = ?, fail_count = 0 WHERE link = ? AND status != ?",
            (status, link, status),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def record_check(self, link: str, outcome: str, unreachable_threshold: int = 3,
                     preserve_applied: bool = True) -> bool:
        """Apply an availability check result. Returns True when status became 'no longer available'.

        outcome: 'available' | 'gone' | 'unreachable' | 'unknown'. Only the status column
        (plus internal bookkeeping) is touched.
        """
        row = self.get(link)
        if row is None:
            return False
        fail_count = row["fail_count"]
        mark_gone = False
        if outcome == "available":
            fail_count = 0
        elif outcome == "gone":
            mark_gone = True
        elif outcome == "unreachable":
            fail_count += 1
            mark_gone = fail_count >= max(1, int(unreachable_threshold))
        if row["status"] == STATUS_GONE or (preserve_applied and row["status"] == STATUS_APPLIED):
            mark_gone = False
        new_status = STATUS_GONE if mark_gone else row["status"]
        self.conn.execute(
            "UPDATE jobs SET status = ?, fail_count = ?, last_checked = ? WHERE link = ?",
            (new_status, fail_count, _now(), link),
        )
        self.conn.commit()
        return mark_gone

    # ---------------------------------------------------------- company pool
    def upsert_company(self, entry: dict, origin: str = "config") -> str | None:
        """Add a company to the rotation pool. Existing companies keep their crawl history."""
        key = company_key(entry)
        if not key:
            return None
        name = str(entry.get("name") or entry.get("slug") or entry.get("url"))[:200]
        clean = {k: v for k, v in entry.items() if k in ("name", "ats", "slug", "url", "params")}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO companies (key, name, entry, origin, added_at) VALUES (?, ?, ?, ?, ?)",
            (key, name, json.dumps(clean, ensure_ascii=False), origin, _now()))
        if cur.rowcount == 0 and origin == "config":   # config edits (params, name) win
            self.conn.execute("UPDATE companies SET name = ?, entry = ? WHERE key = ?",
                              (name, json.dumps(clean, ensure_ascii=False), key))
        self.conn.commit()
        return key

    def due_companies(self, limit: int = 50, revisit_days: int = 7, max_failures: int = 3,
                      today: date | None = None) -> list[dict]:
        """Companies not crawled within the last `revisit_days` days, longest waiting first.

        Compared by calendar day, so a company crawled on Monday is due again next Monday.
        Companies that failed `max_failures` times in a row are left out.
        """
        cutoff = ((today or date.today()) - timedelta(days=int(revisit_days))).isoformat()
        rows = self.conn.execute(
            "SELECT key, entry FROM companies WHERE fail_count < ?"
            " AND (last_crawled IS NULL OR last_crawled <= ?)"
            " ORDER BY last_crawled IS NOT NULL, last_crawled ASC, added_at ASC LIMIT ?",
            (int(max_failures), cutoff, int(limit))).fetchall()
        return [{**json.loads(r["entry"]), "key": r["key"]} for r in rows]

    def mark_company(self, key: str, ok: bool, postings: int = 0, today: date | None = None) -> None:
        self.conn.execute(
            "UPDATE companies SET last_crawled = ?, last_ok = ?, last_postings = ?,"
            " fail_count = CASE WHEN ? THEN 0 ELSE fail_count + 1 END WHERE key = ?",
            ((today or date.today()).isoformat(), int(ok), int(postings), int(ok), key))
        self.conn.commit()

    def company_names(self) -> list[str]:
        return [r["name"] for r in self.conn.execute("SELECT name FROM companies ORDER BY name")]

    def company_stats(self, max_failures: int = 3) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(fail_count >= ?) AS inactive FROM companies",
            (int(max_failures),)).fetchone()
        return {"pool": row["total"] or 0, "inactive": row["inactive"] or 0}

    # ------------------------------------------------------------------- reads
    def list_jobs(self, status: str | None = None, query: str | None = None, limit: int | None = None) -> list:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if status in VALID_STATUSES:
            sql += " AND status = ?"
            args.append(status)
        if query:
            sql += " AND (position LIKE ? OR company LIKE ? OR link LIKE ?)"
            args += [f"%{query}%"] * 3
        sql += " ORDER BY first_seen DESC, date_published DESC, link"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        return self.conn.execute(sql, args).fetchall()

    def get(self, link: str):
        return self.conn.execute("SELECT * FROM jobs WHERE link = ?", (link,)).fetchone()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def counts_by_status(self) -> dict:
        rows = self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        return {status: n for status, n in rows}

    def links_to_check(self, limit: int = 300, preserve_applied: bool = True) -> list[str]:
        """Links still considered live, least recently checked first."""
        skip = [STATUS_GONE] + ([STATUS_APPLIED] if preserve_applied else [])
        marks = ",".join("?" * len(skip))
        rows = self.conn.execute(
            f"SELECT link FROM jobs WHERE status NOT IN ({marks})"
            " ORDER BY last_checked IS NOT NULL, last_checked ASC LIMIT ?",
            (*skip, int(limit)),
        ).fetchall()
        return [r["link"] for r in rows]

    # --------------------------------------------------------------------- CSV
    def export_csv(self, path: str) -> int:
        rows = self.conn.execute(
            "SELECT link, position, date_published, status FROM jobs"
            " ORDER BY first_seen DESC, date_published DESC, link"
        ).fetchall()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_COLUMNS)
            for row in rows:
                writer.writerow([row["link"], row["position"], row["date_published"], row["status"]])
        os.replace(tmp, target)
        return len(rows)

    def import_csv(self, path: str) -> dict:
        """Pull hand edits from the CSV into the database.

        Status edits win over the stored status. Rows with an unknown link are added.
        Rows with an invalid status are logged and left untouched.
        """
        result = {"status_updates": 0, "added": 0, "invalid": 0}
        if not Path(path).exists():
            return result
        with open(path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(fh, dialect=dialect)
            for line_no, raw in enumerate(reader, start=2):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
                link = normalize_link(row.get("link", ""))
                status = " ".join(row.get("status", "").lower().split())
                if not link:
                    continue
                if status not in VALID_STATUSES:
                    result["invalid"] += 1
                    logger.warning("CSV row has an invalid status, ignoring it",
                                   extra={"line": line_no, "link": link, "status": status})
                    continue
                existing = self.get(link)
                if existing is None:
                    position = row.get("position") or "(added by hand)"
                    self.add_job(link, position, normalize_date(row.get("date of publication")),
                                 source="manual", status=status)
                    result["added"] += 1
                elif existing["status"] != status:
                    self.set_status(link, status)
                    result["status_updates"] += 1
        return result
