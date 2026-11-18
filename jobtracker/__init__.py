"""Daily job-search automation: collect postings, dedupe, track status."""

__version__ = "1.0.0"

STATUS_NOT_APPLIED = "not applied"
STATUS_APPLIED = "applied"
STATUS_GONE = "no longer available"
VALID_STATUSES = (STATUS_APPLIED, STATUS_NOT_APPLIED, STATUS_GONE)
