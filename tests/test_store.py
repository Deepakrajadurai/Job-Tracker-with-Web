"""Deduplication, status preservation, the 'no longer available' transition, CSV sync."""
import csv

import pytest

from jobtracker import STATUS_APPLIED, STATUS_GONE, STATUS_NOT_APPLIED
from jobtracker.ingest import ingest

LINK = "https://example.com/jobs/1"


def posting(**kw):
    return {"link": LINK, "position": "Machine Learning Engineer", "date": "2026-11-01",
            "source": "test", **kw}


def test_new_posting_is_appended_as_not_applied(store, cfg):
    assert ingest(store, cfg, posting()) == "new"
    row = store.get(LINK)
    assert row["status"] == STATUS_NOT_APPLIED
    assert row["date_published"] == "2026-11-01"


def test_same_link_is_never_duplicated(store, cfg):
    assert ingest(store, cfg, posting()) == "new"
    assert ingest(store, cfg, posting(position="Changed title")) == "duplicate"
    assert store.count() == 1
    assert store.get(LINK)["position"] == "Machine Learning Engineer"


def test_tracking_parameters_do_not_create_duplicates(store, cfg):
    ingest(store, cfg, posting(link=LINK + "?utm_source=x&trk=abc"))
    assert ingest(store, cfg, posting(link=LINK + "/#apply")) == "duplicate"
    assert store.count() == 1


def test_linkedin_links_dedupe_on_job_id(store, cfg):
    a = "https://de.linkedin.com/jobs/view/ml-engineer-at-acme-4012345678?refId=abc&trackingId=x"
    b = "https://www.linkedin.com/comm/jobs/view/4012345678/?trk=email"
    assert ingest(store, cfg, posting(link=a)) == "new"
    assert ingest(store, cfg, posting(link=b)) == "duplicate"


def test_applied_status_survives_a_new_run(store, cfg):
    ingest(store, cfg, posting())
    store.set_status(LINK, STATUS_APPLIED)
    assert ingest(store, cfg, posting()) == "duplicate"
    assert store.get(LINK)["status"] == STATUS_APPLIED


def test_invalid_status_is_rejected(store, cfg):
    ingest(store, cfg, posting())
    with pytest.raises(ValueError):
        store.set_status(LINK, "maybe")


def test_removed_posting_becomes_no_longer_available_and_only_status_changes(store, cfg):
    ingest(store, cfg, posting())
    before = dict(store.get(LINK))
    assert store.record_check(LINK, "gone") is True
    after = dict(store.get(LINK))
    assert after["status"] == STATUS_GONE
    for column in ("link", "position", "date_published", "source", "company", "location", "first_seen"):
        assert after[column] == before[column]


def test_unreachable_needs_consecutive_failures(store, cfg):
    ingest(store, cfg, posting())
    assert store.record_check(LINK, "unreachable", unreachable_threshold=3) is False
    assert store.record_check(LINK, "unreachable", unreachable_threshold=3) is False
    assert store.get(LINK)["status"] == STATUS_NOT_APPLIED
    assert store.record_check(LINK, "unreachable", unreachable_threshold=3) is True
    assert store.get(LINK)["status"] == STATUS_GONE


def test_successful_check_resets_the_failure_counter(store, cfg):
    ingest(store, cfg, posting())
    store.record_check(LINK, "unreachable", unreachable_threshold=2)
    store.record_check(LINK, "available", unreachable_threshold=2)
    assert store.record_check(LINK, "unreachable", unreachable_threshold=2) is False
    assert store.get(LINK)["status"] == STATUS_NOT_APPLIED


def test_blocked_check_leaves_status_alone(store, cfg):
    ingest(store, cfg, posting())
    assert store.record_check(LINK, "unknown") is False
    assert store.get(LINK)["status"] == STATUS_NOT_APPLIED


def test_applied_is_preserved_when_posting_disappears_by_default(store, cfg):
    ingest(store, cfg, posting())
    store.set_status(LINK, STATUS_APPLIED)
    assert store.record_check(LINK, "gone", preserve_applied=True) is False
    assert store.get(LINK)["status"] == STATUS_APPLIED
    assert store.record_check(LINK, "gone", preserve_applied=False) is True
    assert store.get(LINK)["status"] == STATUS_GONE


def test_gone_and_applied_postings_are_not_rechecked(store, cfg):
    for n, status in enumerate([STATUS_NOT_APPLIED, STATUS_APPLIED, STATUS_GONE]):
        store.add_job(f"https://example.com/jobs/{n}", "x", status=status)
    assert store.links_to_check() == ["https://example.com/jobs/0"]
    assert len(store.links_to_check(preserve_applied=False)) == 2


def test_csv_has_exactly_the_four_columns(store, cfg, tmp_path):
    ingest(store, cfg, posting())
    path = tmp_path / "out.csv"
    store.export_csv(str(path))
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["link", "position", "date of publication", "status"]
    assert rows[1] == [LINK, "Machine Learning Engineer", "2026-11-01", STATUS_NOT_APPLIED]


def test_hand_edit_in_csv_is_imported_and_then_kept(store, cfg, tmp_path):
    ingest(store, cfg, posting())
    path = tmp_path / "jobs.csv"
    store.export_csv(str(path))
    text = path.read_text(encoding="utf-8-sig").replace(STATUS_NOT_APPLIED, "Applied ")
    path.write_text(text, encoding="utf-8")
    assert store.import_csv(str(path))["status_updates"] == 1
    assert store.get(LINK)["status"] == STATUS_APPLIED
    assert ingest(store, cfg, posting()) == "duplicate"
    assert store.get(LINK)["status"] == STATUS_APPLIED


def test_csv_with_invalid_status_or_semicolons(store, cfg, tmp_path):
    ingest(store, cfg, posting())
    path = tmp_path / "jobs.csv"
    path.write_text("link;position;date of publication;status\n"
                    f"{LINK};x;2026-11-01;interviewing\n"
                    "https://example.com/jobs/manual;Hand added;03.11.2026;applied\n", encoding="utf-8")
    result = store.import_csv(str(path))
    assert result == {"status_updates": 0, "added": 1, "invalid": 1}
    assert store.get(LINK)["status"] == STATUS_NOT_APPLIED
    manual = store.get("https://example.com/jobs/manual")
    assert manual["status"] == STATUS_APPLIED and manual["date_published"] == "2026-11-03"
