"""Google ID-token verification and short-lived, HttpOnly app sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class AuthSettings:
    google_client_id: str | None
    session_secret: str | None
    secure_cookie: bool

    @classmethod
    def from_env(cls) -> "AuthSettings":
        load_dotenv()
        return cls(
            google_client_id=os.getenv("GOOGLE_CLIENT_ID") or None,
            session_secret=os.getenv("MATCHLENS_AUTH_SECRET") or None,
            secure_cookie=os.getenv("MATCHLENS_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"},
        )


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_session(user: dict[str, str], secret: str, lifetime_s: int = 60 * 60 * 24 * 7) -> str:
    now = int(time.time())
    head = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(json.dumps({**user, "iat": now, "exp": now + lifetime_s}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{signature}"


def decode_session(token: str, secret: str) -> dict[str, Any] | None:
    try:
        head, body, signature = token.split(".")
        expected = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(claims.get("sub"), str) or claims.get("exp", 0) <= time.time():
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def verify_google_credential(credential: str, client_id: str) -> dict[str, str]:
    """Verify Google's signed ID token, issuer, expiry and OAuth audience."""
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(credential, Request(), audience=client_id)
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("Invalid Google token issuer")
    if claims.get("email_verified") is not True or not claims.get("sub"):
        raise ValueError("A verified Google account is required")
    return {
        "sub": str(claims["sub"]),
        "email": str(claims.get("email", "")),
        "name": str(claims.get("name", "Fan")),
        "picture": str(claims.get("picture", "")),
    }
