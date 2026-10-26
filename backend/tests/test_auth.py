import asyncio
import importlib

from fastapi import Response
from starlette.requests import Request

from matchlens.auth import create_session, decode_session
from matchlens.store.base import MemoryStore


def test_session_round_trip_and_rejects_tampering():
    token = create_session({"sub": "google-user", "email": "fan@example.test"}, "test-secret")
    assert decode_session(token, "test-secret")["sub"] == "google-user"
    assert decode_session(token, "wrong-secret") is None
    head, body, signature = token.split(".")
    assert decode_session(f"{head}.{body}.x{signature}", "test-secret") is None


def test_session_expiration():
    token = create_session({"sub": "google-user"}, "test-secret", lifetime_s=-1)
    assert decode_session(token, "test-secret") is None


def test_google_login_sets_http_only_cookie_and_persists_profile(monkeypatch):
    api = importlib.import_module("matchlens.api.app")

    async def exercise():
        store = MemoryStore()
        await store.open()
        monkeypatch.setattr(api, "store_backend", store)
        monkeypatch.setattr(api, "auth_settings", api.AuthSettings("client-id", "session-secret", False))
        monkeypatch.setattr(api, "verify_google_credential", lambda token, audience: {
            "sub": "google-sub", "email": "fan@example.test", "name": "Match Fan", "picture": "",
        })
        response = Response()
        result = await api.google_login(api.GoogleLoginRequest(credential="x" * 30), response)
        cookie = response.headers["set-cookie"]
        assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
        session_cookie = cookie.split(";", 1)[0]
        request = Request({"type": "http", "method": "GET", "path": "/", "scheme": "http",
                           "http_version": "1.1", "server": ("test", 80), "client": ("test", 1),
                           "root_path": "", "query_string": b"", "headers": [(b"cookie", session_cookie.encode())]})
        profile = await api.auth_me(request)
        assert result["user"]["sub"] == profile["user"]["sub"] == "google-sub"
        saved = await api.save_fan_profile(api.FanProfileRequest(language="es", club_id="HBC"), request)
        assert saved["language"] == "es"
        stored = await store.read("fan_profiles", "google-sub", "profile")
        assert stored["club_id"] == "HBC"

    asyncio.run(exercise())
