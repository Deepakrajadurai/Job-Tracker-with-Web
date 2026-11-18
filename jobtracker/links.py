"""Link normalization (the dedup key) and ATS detection."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = {
    "trk", "trackingid", "refid", "ref", "src", "gh_src", "lever-source", "fbclid", "gclid",
    "position", "pagenum", "originalsubdomain", "mcid", "cid", "from", "tk", "advn", "adid",
}
_LINKEDIN_JOB = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})")


def normalize_link(link) -> str:
    """Canonical form of a job link, or "" when it is not a usable http(s) URL."""
    if not link or not isinstance(link, str):
        return ""
    link = link.strip()
    try:
        parts = urlsplit(link)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    host = parts.netloc.lower()
    path = parts.path or ""
    if host.endswith("linkedin.com"):
        match = _LINKEDIN_JOB.search(path)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match.group(1)}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)]
    if "indeed." in host:
        jk = dict(query).get("jk")
        if jk:
            return f"https://{host}/viewjob?jk={jk}"
    kept = [(k, v) for k, v in query if k.lower() not in _TRACKING and not k.lower().startswith("utm_")]
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(kept), ""))


_ATS_PATTERNS = [
    ("greenhouse", re.compile(r"^(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("lever-eu", re.compile(r"^jobs\.eu\.lever\.co/([\w.-]+)", re.I)),
    ("lever", re.compile(r"^jobs\.lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"^(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("recruitee", re.compile(r"^([\w-]+)\.recruitee\.com", re.I)),
    ("workable", re.compile(r"^apply\.workable\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"^(?!apply\.|www\.|jobs\.)([\w-]+)\.workable\.com", re.I)),
    ("personio", re.compile(r"^([\w-]+)\.jobs\.personio\.(?:de|com)", re.I)),
]


def detect_ats(url: str) -> tuple[str, str] | None:
    """Return (ats, slug) when the URL points at a known applicant tracking system."""
    if not url:
        return None
    stripped = re.sub(r"^https?://", "", url.strip())
    for ats, pattern in _ATS_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return ats, match.group(1)
    return None
