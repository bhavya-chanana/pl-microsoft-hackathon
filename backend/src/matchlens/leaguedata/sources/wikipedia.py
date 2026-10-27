"""Wikipedia text (CC BY-SA 4.0) for clubs, managers and key players.

Titles come from the English-Wikipedia sitelinks stored by the Wikidata adapter, so this
adapter fetches after ``wikidata``. Per page:

* the REST summary (``/api/rest_v1/page/summary/{title}``) -> one ``TextDoc`` titled with the page;
* optionally the plain-text page extract (``action=query&prop=extracts&explaintext``) from
  which the "History" (clubs) or style/philosophy sections (managers, players) are cut,
  capped at 2,500 characters -> ``TextDoc`` titled ``"<page>#<Section>"``.

Raw files in ``raw/wikipedia``: ``index.json`` (what was requested and when), ``summary/<file>.json``,
``extract/<file>.json``. Every ``TextDoc`` keeps its URL, licence and retrieval date.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from matchlens.leaguedata import http
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.schema import League, TextDoc
from matchlens.leaguedata.sources import wikidata

log = logging.getLogger(__name__)

SOURCE = "wikipedia"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
API_URL = "https://en.wikipedia.org/w/api.php"
LICENCE = "CC BY-SA 4.0"
SECTION_CAP = 2500
MANAGER_SINCE = "2015-01-01"  # managers with a spell open at or after this date

SECTION_NAMES: dict[str, tuple[str, ...]] = {
    "club": ("history",),
    "manager": ("coaching style", "managerial style", "management style", "style of management",
                "philosophy", "tactics", "tactical approach", "style of play", "playing style"),
    "player": ("style of play", "playing style", "player profile"),
}


def _file_stem(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")[:60]
    return f"{slug}_{hashlib.sha1(title.encode()).hexdigest()[:6]}"


def summary_url(title: str) -> str:
    return SUMMARY_URL + quote(title.replace(" ", "_"), safe="()_,.'-")


def extract_url(title: str) -> str:
    q = {"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
         "redirects": 1, "format": "json", "formatversion": 2, "titles": title}
    return f"{API_URL}?{urlencode(q)}"


# ------------------------------------------------------------------ fetch


def targets(cfg: Config, *, limit: int | None = None) -> list[dict[str, str]]:
    """Pages to fetch (kind, entity id, title) from the Wikidata raw data."""
    raw = wikidata.load_raw(cfg)
    out: list[dict[str, str]] = []
    club_ids = sorted(raw.clubs)
    if limit is not None:
        club_ids = club_ids[:limit]
    managers: set[str] = set()
    for (m, c, _start), end in raw.spells.items():
        if c in club_ids and (end is None or end >= MANAGER_SINCE):
            managers.add(m)
    for q in club_ids:
        if raw.clubs[q]["article"]:
            out.append({"kind": "club", "entity_id": wikidata.entity_id(q), "title": raw.clubs[q]["article"]})
        for p in wikidata.key_players(raw, q, 5):
            out.append({"kind": "player", "entity_id": wikidata.entity_id(p), "title": raw.players[p]["article"]})
    for m in sorted(managers):
        if raw.managers[m]["article"]:
            out.append({"kind": "manager", "entity_id": wikidata.entity_id(m), "title": raw.managers[m]["article"]})
    seen: set[str] = set()
    unique = []
    for t in out:
        if t["entity_id"] not in seen:
            seen.add(t["entity_id"])
            unique.append(t)
    return unique


def _load_index(raw: Path) -> dict[str, dict[str, str]]:
    path = raw / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False, sections: bool = True) -> int:
    """Fetch summaries (and style sections) for the pages listed by the Wikidata raw data."""
    raw = cfg.raw_dir(SOURCE)
    index = _load_index(raw)
    new = 0
    today = datetime.now(UTC).date().isoformat()
    for t in targets(cfg, limit=limit):
        stem = _file_stem(t["title"])
        summary_path = raw / "summary" / f"{stem}.json"
        existed = summary_path.exists() and not refresh
        try:
            http.fetch_bytes(summary_url(t["title"]), summary_path, cfg, refresh=refresh,
                             headers={"Accept": "application/json"})
            if sections:
                http.fetch_bytes(extract_url(t["title"]), raw / "extract" / f"{stem}.json", cfg, refresh=refresh)
        except http.FetchError as exc:
            log.warning("wikipedia %s: %s", t["title"], exc)
            continue
        entry = index.get(t["entity_id"])
        index[t["entity_id"]] = {**t, "file": stem,
                                 "retrieved": entry["retrieved"] if entry and existed else today}
        new += 0 if existed else 1
    http.write_atomic(raw / "index.json", json.dumps(index, indent=1, sort_keys=True).encode())
    return new


# ------------------------------------------------------------------ text helpers

_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")


def split_sections(text: str) -> list[tuple[str, int, str]]:
    """Plain-text page -> [(heading, level, body)] where body excludes sub-headings' markers."""
    out: list[tuple[str, int, list[str]]] = [("", 1, [])]
    for line in text.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            out.append((m.group(2), len(m.group(1)), []))
        else:
            out[-1][2].append(line)
    return [(h, lvl, "\n".join(body).strip()) for h, lvl, body in out]


def section_text(page_text: str, names: Sequence[str], cap: int = SECTION_CAP) -> tuple[str, str] | None:
    """First matching top-level section (with its sub-sections) as ``(heading, text)``."""
    secs = split_sections(page_text)
    wanted = {n.lower() for n in names}
    for i, (heading, level, body) in enumerate(secs):
        if heading.lower() not in wanted:
            continue
        parts = [body]
        for h, lvl, b in secs[i + 1:]:
            if lvl <= level:
                break
            parts.append(f"{h}. {b}" if b else "")
        text = re.sub(r"\n{2,}", "\n\n", "\n\n".join(p for p in parts if p)).strip()
        if not text:
            continue
        return heading, _cap(text, cap)
    return None


def _cap(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    cut = text[:cap]
    end = max(cut.rfind(". "), cut.rfind(".\n"))
    return (cut[:end + 1] if end > cap // 2 else cut.rstrip()) + ("" if end > cap // 2 else "…")


# ------------------------------------------------------------------ normalize


def _page_text(doc: dict[str, Any]) -> str | None:
    pages = doc.get("query", {}).get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    for page in pages:
        if page.get("extract"):
            return page["extract"]
    return None


def normalize(cfg: Config) -> dict[str, list]:
    raw = cfg.raw_dir(SOURCE)
    docs: list[TextDoc] = []
    for entity_id, entry in sorted(_load_index(raw).items()):
        kind = entry["kind"]
        summary_file = raw / "summary" / f"{entry['file']}.json"
        if not summary_file.exists():
            continue
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        if summary.get("type") not in (None, "standard"):
            continue  # disambiguation or missing page
        title = summary.get("title") or entry["title"]
        url = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page") or wikidata.article_url(title)
        if summary.get("extract"):
            docs.append(TextDoc(entity_kind=kind, entity_id=entity_id, title=title, text=summary["extract"].strip(),
                                url=url, licence=LICENCE, retrieved=entry["retrieved"]))
        extract_file = raw / "extract" / f"{entry['file']}.json"
        if extract_file.exists():
            page = _page_text(json.loads(extract_file.read_text(encoding="utf-8")))
            found = section_text(page, SECTION_NAMES[kind]) if page else None
            if found:
                heading, text = found
                docs.append(TextDoc(entity_kind=kind, entity_id=entity_id, title=f"{title}#{heading}", text=text,
                                    url=f"{url}#{heading.replace(' ', '_')}", licence=LICENCE,
                                    retrieved=entry["retrieved"]))
    return {"text_docs": docs}
