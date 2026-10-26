"""Username + password accounts for the demo (no email, no recovery). Passwords are PBKDF2-hashed."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from .db import Database
from .models import Preferences

ITERATIONS = 200_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest)
    except ValueError:
        return False


class WrongPassword(Exception):
    pass


def sign_in(db: Database, username: str, password: str) -> tuple[dict, bool]:
    """Log in, or create the account on first sign-in. Returns (user, created)."""
    key = username.lower()
    rows = db.query("SELECT id, username, password_hash FROM users WHERE lower(username) = ?", (key,))
    if rows:
        if not verify_password(password, rows[0]["password_hash"]):
            raise WrongPassword()
        return {"id": rows[0]["id"], "username": rows[0]["username"]}, False
    cur = db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                     (username, hash_password(password), time.time()))
    db.commit()
    return {"id": cur.lastrowid, "username": username}, True


def get_preferences(db: Database, user_id: int) -> Preferences:
    rows = db.query("SELECT body FROM preferences WHERE user_id = ?", (user_id,))
    return Preferences.model_validate(json.loads(rows[0]["body"])) if rows else Preferences()


def save_preferences(db: Database, user_id: int, prefs: Preferences) -> Preferences:
    db.execute("INSERT OR REPLACE INTO preferences VALUES (?, ?, ?)", (user_id, prefs.model_dump_json(), time.time()))
    db.commit()
    return prefs


def session_secret(data_dir: Path) -> str:
    """The shared Matchlens session secret, or a random local one kept beside the demo database."""
    secret = os.getenv("MATCHLENS_AUTH_SECRET")
    if secret:
        return secret
    path = data_dir / "session_secret"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()
