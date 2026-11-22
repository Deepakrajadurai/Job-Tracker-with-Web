"""Copy a profile's tracker files into Nextcloud over WebDAV after each run.

One-way copy: the files in Nextcloud are a convenient read-only view. Status changes are made
in the web interface (or in data/<name>/jobs.csv on the server), which stays the source of truth.

Settings come from the environment, so each web user can have their own Nextcloud account:
    JOBTRACKER_NEXTCLOUD_URL       e.g. http://100.64.146.94:8080
    JOBTRACKER_NEXTCLOUD_USER      Nextcloud login name
    JOBTRACKER_NEXTCLOUD_PASSWORD  an app password (Nextcloud, Settings, Security)
    JOBTRACKER_NEXTCLOUD_FOLDER    target folder, default "Job Tracker"
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

ENV = {"url": "JOBTRACKER_NEXTCLOUD_URL", "user": "JOBTRACKER_NEXTCLOUD_USER",
       "password": "JOBTRACKER_NEXTCLOUD_PASSWORD", "folder": "JOBTRACKER_NEXTCLOUD_FOLDER"}


class NextcloudError(RuntimeError):
    pass


def settings_from_env(env=os.environ) -> dict | None:
    values = {key: (env.get(var) or "").strip() for key, var in ENV.items()}
    if not (values["url"] and values["user"] and values["password"]):
        return None
    values["folder"] = values["folder"] or "Job Tracker"
    return values


def _dav_base(settings: dict) -> str:
    return f"{settings['url'].rstrip('/')}/remote.php/dav/files/{quote(settings['user'])}"


def upload(paths: list[str | Path], settings: dict, session=None, timeout: float = 60) -> list[str]:
    """Upload the files into the configured folder, creating it when needed. Returns remote paths."""
    session = session or requests.Session()
    auth = (settings["user"], settings["password"])
    base = _dav_base(settings)
    folder = "/".join(quote(part) for part in settings["folder"].strip("/").split("/") if part)
    current = base
    for part in folder.split("/") if folder else []:
        current = f"{current}/{part}"
        response = session.request("MKCOL", current, auth=auth, timeout=timeout)
        if response.status_code == 401:
            raise NextcloudError("Nextcloud rejected the login (check user name and app password)")
        if response.status_code not in (201, 405):          # 405 = folder exists already
            raise NextcloudError(f"could not create folder {part!r} (HTTP {response.status_code})")
    uploaded = []
    for path in map(Path, paths):
        target = f"{current}/{quote(path.name)}"
        with open(path, "rb") as fh:
            response = session.put(target, data=fh, auth=auth, timeout=timeout)
        if response.status_code == 401:
            raise NextcloudError("Nextcloud rejected the login (check user name and app password)")
        if response.status_code not in (200, 201, 204):
            raise NextcloudError(f"upload of {path.name} failed (HTTP {response.status_code})")
        uploaded.append(f"{settings['folder'].strip('/')}/{path.name}")
    return uploaded
