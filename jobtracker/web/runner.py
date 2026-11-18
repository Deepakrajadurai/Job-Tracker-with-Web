"""Starts tracker runs for web users: on demand and once a day."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("jobtracker.web.runner")

IMAP_VARS = {"imap_host": "JOBTRACKER_IMAP_HOST", "imap_user": "JOBTRACKER_IMAP_USER",
             "imap_password": "JOBTRACKER_IMAP_PASSWORD"}
MIN_MINUTES_BETWEEN_RUNS = 30


class Runner:
    def __init__(self, home: Path, users):
        self.home = home
        self.users = users
        self._procs: dict[int, subprocess.Popen] = {}
        self._started: dict[int, float] = {}

    def profile_path(self, user_id: int) -> Path:
        return self.home / "profiles" / f"user{user_id}.yaml"

    def env_for(self, user_id: int) -> dict:
        """Each run only sees the mailbox of its own user, never the server's or another user's."""
        env = dict(os.environ)
        for key, var in IMAP_VARS.items():
            value = self.users.get_setting(user_id, key)
            if value:
                env[var] = value
            else:
                env.pop(var, None)
        return env

    def is_running(self, user_id: int) -> bool:
        proc = self._procs.get(user_id)
        return proc is not None and proc.poll() is None

    def minutes_until_allowed(self, user_id: int) -> int:
        elapsed = (time.time() - self._started.get(user_id, 0)) / 60
        return max(0, int(MIN_MINUTES_BETWEEN_RUNS - elapsed + 0.999))

    def start(self, user_id: int, force: bool = False) -> str:
        """Returns 'started', 'running', 'wait' or 'no-profile'."""
        if not self.profile_path(user_id).exists():
            return "no-profile"
        if self.is_running(user_id):
            return "running"
        if not force and self.minutes_until_allowed(user_id) > 0:
            return "wait"
        self._procs[user_id] = subprocess.Popen(
            [sys.executable, "-m", "jobtracker.run", "--config", str(self.profile_path(user_id))],
            cwd=str(self.home), env=self.env_for(user_id),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._started[user_id] = time.time()
        return "started"

    def wait(self, user_id: int, timeout: float | None = None) -> None:
        proc = self._procs.get(user_id)
        if proc is not None:
            proc.wait(timeout=timeout)

    # ------------------------------------------------------------ scheduler
    @staticmethod
    def seconds_until(clock: str, now: datetime | None = None) -> float:
        now = now or datetime.now()
        hour, minute = (int(part) for part in clock.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def daily(self, clock: str = "04:00") -> None:
        """Run every user's profile once a day at `clock` (server local time), one after the other."""
        while True:
            await asyncio.sleep(self.seconds_until(clock))
            for user_id in self.users.all_ids():
                try:
                    if self.start(user_id, force=True) == "started":
                        await asyncio.to_thread(self.wait, user_id, 3600)
                except Exception:  # noqa: BLE001 - one user's failure must not stop the others
                    logger.exception("scheduled run failed", extra={"user": user_id})
