"""Accounts: users, password hashes, linked SSO identities, encrypted per-user settings."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
    provider TEXT NOT NULL,
    subject  TEXT NOT NULL,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (provider, subject)
);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   BLOB NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        scheme, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, TypeError):
        return False


class UserDB:
    def __init__(self, path: str | Path, secret_key: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))

    def close(self) -> None:
        self.conn.close()

    # ----------------------------------------------------------------- users
    def create(self, email: str, name: str = "", password: str | None = None):
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise ValueError("Please enter a valid email address.")
        if password is not None and len(password) < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        try:
            cur = self.conn.execute(
                "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (email, name.strip()[:100], hash_password(password) if password else None,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            raise ValueError("An account with this email already exists. Please log in.") from None
        self.conn.commit()
        return self.by_id(cur.lastrowid)

    def by_id(self, user_id):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def by_email(self, email: str):
        return self.conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()

    def all_ids(self) -> list[int]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM users ORDER BY id")]

    def authenticate(self, email: str, password: str):
        user = self.by_email(email)
        # Hash even when the account is unknown, so response time does not reveal who has an account.
        ok = check_password(password, user["password_hash"] if user else hash_password("x" * MIN_PASSWORD))
        return user if (user and ok) else None

    def from_sso(self, provider: str, subject: str, email: str, name: str, email_verified: bool):
        """Find or create the account behind a single sign-on identity.

        An existing account is only joined through its email when the provider verified that email.
        """
        row = self.conn.execute("SELECT user_id FROM identities WHERE provider = ? AND subject = ?",
                                (provider, str(subject))).fetchone()
        if row:
            return self.by_id(row["user_id"])
        user = self.by_email(email) if email else None
        if user is not None and not email_verified:
            raise ValueError("This email already has an account. Log in with your password first.")
        if user is None:
            user = self.create(email, name)
        self.conn.execute("INSERT INTO identities (provider, subject, user_id) VALUES (?, ?, ?)",
                          (provider, str(subject), user["id"]))
        self.conn.commit()
        return user

    def providers_for(self, user_id: int) -> list[str]:
        return [r["provider"] for r in self.conn.execute(
            "SELECT provider FROM identities WHERE user_id = ? ORDER BY provider", (user_id,))]

    # -------------------------------------------------------------- settings
    def set_setting(self, user_id: int, key: str, value: str) -> None:
        if value == "":
            self.conn.execute("DELETE FROM settings WHERE user_id = ? AND key = ?", (user_id, key))
        else:
            self.conn.execute("INSERT OR REPLACE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                              (user_id, key, self._fernet.encrypt(value.encode())))
        self.conn.commit()

    def get_setting(self, user_id: int, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE user_id = ? AND key = ?",
                                (user_id, key)).fetchone()
        if not row:
            return default
        try:
            return self._fernet.decrypt(row["value"]).decode()
        except InvalidToken:      # secret key changed, the stored value is unreadable
            return default
