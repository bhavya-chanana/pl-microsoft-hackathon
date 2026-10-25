"""Polite, cached downloads (standard library only).

Every response is saved under ``Config.raw_dir(source)`` and never fetched
again unless ``refresh`` is set, so a pipeline run can be stopped and resumed
at any point. Writes are atomic (temporary file, then rename), requests to the
same host are spaced by ``Config.host_delay``, and 429/5xx responses back off.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from matchlens.leaguedata.config import DEFAULT_DELAY, Config

log = logging.getLogger(__name__)

_last_request: dict[str, float] = {}


class FetchError(RuntimeError):
    pass


def _wait_for(host: str, cfg: Config) -> None:
    delay = cfg.host_delay.get(host, DEFAULT_DELAY)
    wait = _last_request.get(host, 0.0) + delay - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def fetch_bytes(url: str, cache: Path, cfg: Config, *, refresh: bool = False,
                headers: dict[str, str] | None = None, data: bytes | None = None,
                retries: int = 4) -> bytes:
    """Return the body of ``url``, from ``cache`` when present."""
    if cache.exists() and not refresh:
        return cache.read_bytes()
    host = urlparse(url).netloc
    req_headers = {"User-Agent": cfg.user_agent, "Accept-Encoding": "gzip"} | (headers or {})
    for attempt in range(retries + 1):
        _wait_for(host, cfg)
        try:
            req = urllib.request.Request(url, data=data, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            write_atomic(cache, body)
            return body
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                backoff = 2 ** attempt * 5
                log.warning("%s -> HTTP %s, retrying in %ss", url, exc.code, backoff)
                time.sleep(backoff)
                continue
            raise FetchError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(2 ** attempt * 2)
                continue
            raise FetchError(f"{url}: {exc}") from exc
    raise FetchError(url)  # pragma: no cover - loop always returns or raises


def fetch_json(url: str, cache: Path, cfg: Config, **kw: Any) -> Any:
    return json.loads(fetch_bytes(url, cache, cfg, **kw))


def fetch_text(url: str, cache: Path, cfg: Config, encoding: str = "utf-8", **kw: Any) -> str:
    return fetch_bytes(url, cache, cfg, **kw).decode(encoding, errors="replace")
