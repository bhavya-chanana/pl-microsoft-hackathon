"""Runtime knowledge bundle: what the agents and the API know about teams, players and matches.

``export(cfg)`` (CLI ``python -m matchlens.leaguedata export``) turns the curated dataset, the match reasonings
and the playbooks into plain JSON files in ``cfg.kb_dir``; ``KnowledgeBase.load(dir)`` reads them back with the
standard library and pydantic only (no DuckDB, no pandas), so the deployed image stays slim.

    teams.json        {"latest": {team_id: season}, "styles": [TeamStyle, ...]}   one per team-season
    mentality.json    [Mentality, ...]
    players.jsonl     one PlayerProfile per line                                    (loaded on first use)
    clubs.json        {team_id: ClubProfile}
    managers.json     {manager_id: ManagerProfile}
    reasonings.jsonl  one MatchSummary per line (headline, why_won, patterns)       (loaded on first use)
    playbooks.json    [Playbook, ...]
    index.json        {"format", "curated_version", "counts", "built_at"}

A team-season has one style: the domestic league's when the team also played the Champions League that season.

The API and the tactician reach the bundle through ``get_kb()``, which reads ``MATCHLENS_KB_DIR`` and returns
``None`` when it is not set (the feature is then simply off).
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import threading
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from matchlens.leaguedata.contracts import (
    ClubProfile,
    ManagerProfile,
    MatchReasoning,
    Mentality,
    PatternTag,
    Playbook,
    PlayerProfile,
    TeamStyle,
    strip_citations,
)
from matchlens.leaguedata.schema import League

log = logging.getLogger(__name__)

FORMAT = 1
KB_DIR_ENV = "MATCHLENS_KB_DIR"
CONTEXT_BUDGET = 3800  # characters of JSON in ``team_context``

# ------------------------------------------------------------------ records


class KbPattern(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tag: PatternTag
    side: Literal["home", "away"]
    strength: float
    description: str


class MatchSummary(BaseModel):
    """What the knowledge base keeps of a MatchReasoning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    match_key: str
    league: League
    season: str
    date: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    score: str
    verdict: str
    headline: str
    why_won: str
    patterns: list[KbPattern]

    def tags(self, side: Literal["home", "away"] | None = None) -> set[PatternTag]:
        return {p.tag for p in self.patterns if side is None or p.side == side}

    def result_for(self, team_id: str) -> str | None:
        """"win" / "draw" / "loss" from the team's point of view (None when the score cannot be read)."""
        m = re.match(r"\s*(\d+)\s*[-:]\s*(\d+)", self.score)
        if not m or team_id not in (self.home_id, self.away_id):
            return None
        h, a = int(m.group(1)), int(m.group(2))
        gf, ga = (h, a) if team_id == self.home_id else (a, h)
        return "win" if gf > ga else "loss" if gf < ga else "draw"


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["match", "team"]
    id: str
    title: str
    subtitle: str
    score: float


def summarise_reasoning(r: MatchReasoning, names: Mapping[str, str] | None = None) -> MatchSummary:
    names = names or {}
    d = r.draft
    parts = r.match_key.split(":")
    return MatchSummary(
        match_key=r.match_key, league=r.league, season=r.season, date=parts[2] if len(parts) > 2 else "",
        home_id=r.home_id, away_id=r.away_id, home_name=names.get(r.home_id, r.home_id),
        away_name=names.get(r.away_id, r.away_id), score=r.score, verdict=d.verdict,
        headline=strip_citations(d.headline),  # reasonings saved before the headline rule
        why_won=d.why_won,
        patterns=[KbPattern(tag=p.tag, side=p.side, strength=p.strength, description=p.description)
                  for p in d.patterns])


# ------------------------------------------------------------------ name matching

_STOP = {"fc", "cf", "afc", "ac", "as", "sc", "ssc", "us", "ud", "cd", "rc", "fk", "bk", "club", "calcio", "de",
         "the", "of", "1", "sv", "vfb", "vfl", "tsg", "fsv", "ogc", "rcd"}
_ALIASES = {"man": "manchester", "utd": "united", "spurs": "tottenham", "wolves": "wolverhampton",
            "hotspur": "tottenham", "internazionale": "inter"}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _tokens(text: str) -> frozenset[str]:
    out: list[str] = []
    for tok in _fold(text).split():
        out.extend(_ALIASES.get(tok, tok).split())
    return frozenset(t for t in out if t not in _STOP)


# ------------------------------------------------------------------ the knowledge base


class KnowledgeBase:
    def __init__(self, directory: Path, *, index: dict[str, Any], styles: Sequence[TeamStyle],
                 mentality: Sequence[Mentality], clubs: Mapping[str, ClubProfile],
                 managers: Mapping[str, ManagerProfile], playbooks: Sequence[Playbook]) -> None:
        self.directory = directory
        self.index = index
        self._lock = threading.Lock()
        self._styles: dict[tuple[str, str], TeamStyle] = {}
        for s in sorted(styles, key=lambda s: (s.league == League.UCL, s.league.value)):
            self._styles.setdefault((s.team_id, s.season), s)  # domestic style wins over the UCL one
        self._seasons: dict[str, list[str]] = defaultdict(list)
        for team_id, season in self._styles:
            self._seasons[team_id].append(season)
        for seasons in self._seasons.values():
            seasons.sort()
        self._mentality: dict[tuple[str, str], Mentality] = {}
        for m in sorted(mentality, key=lambda m: m.league is not None):  # all-competition rows win
            self._mentality[(m.team_id, m.season)] = m
        self._clubs = dict(clubs)
        self._managers = dict(managers)
        self._playbooks = {(p.team_id, p.season): p for p in playbooks}
        self._names: dict[str, str] = {}
        for (team_id, _), s in sorted(self._styles.items()):
            self._names[team_id] = s.team_name  # newest season's spelling wins
        for team_id, club in self._clubs.items():
            self._names.setdefault(team_id, club.name)
        self._name_tokens = self._build_name_index()
        self._players: dict[str, list[PlayerProfile]] | None = None
        self._by_team_season: dict[tuple[str, str], list[PlayerProfile]] = {}
        self._matches: list[MatchSummary] | None = None

    # -- loading
    @classmethod
    def load(cls, directory: str | Path) -> KnowledgeBase:
        root = Path(directory)
        index = _read_json(root / "index.json")
        if index.get("format") != FORMAT:
            raise ValueError(f"unsupported knowledge base format {index.get('format')!r} in {root}")
        teams = _read_json(root / "teams.json")
        return cls(
            root, index=index,
            styles=[TeamStyle.model_validate(s) for s in teams.get("styles", [])],
            mentality=[Mentality.model_validate(m) for m in _read_json(root / "mentality.json", [])],
            clubs={k: ClubProfile.model_validate(v) for k, v in _read_json(root / "clubs.json", {}).items()},
            managers={k: ManagerProfile.model_validate(v) for k, v in _read_json(root / "managers.json", {}).items()},
            playbooks=[Playbook.model_validate(p) for p in _read_json(root / "playbooks.json", [])])

    def _load_players(self) -> dict[str, list[PlayerProfile]]:
        with self._lock:
            if self._players is None:
                by_id: dict[str, list[PlayerProfile]] = defaultdict(list)
                for line in _read_lines(self.directory / "players.jsonl"):
                    p = PlayerProfile.model_validate_json(line)
                    by_id[p.player_id].append(p)
                    self._by_team_season.setdefault((p.team_id, p.season), []).append(p)
                for rows in by_id.values():
                    rows.sort(key=lambda p: (p.season, p.minutes))
                for rows in self._by_team_season.values():
                    rows.sort(key=lambda p: (-p.minutes, p.player_id))
                self._players = by_id
            return self._players

    def _load_matches(self) -> list[MatchSummary]:
        with self._lock:
            if self._matches is None:
                rows = [MatchSummary.model_validate_json(line)
                        for line in _read_lines(self.directory / "reasonings.jsonl")]
                rows.sort(key=lambda m: (m.date, m.match_key), reverse=True)  # newest first
                self._matches = rows
            return self._matches

    # -- teams
    def team_ids(self) -> list[str]:
        return sorted(self._seasons, key=lambda t: (self._names.get(t, t).lower(), t))

    def team_name(self, team_id: str) -> str:
        return self._names.get(team_id, team_id)

    def seasons(self, team_id: str) -> list[str]:
        return list(self._seasons.get(team_id, ()))

    def list_teams(self) -> list[dict[str, Any]]:
        out = []
        for tid in self.team_ids():
            seasons = self.seasons(tid)
            leagues = sorted({self._styles[(tid, s)].league.value for s in seasons})
            out.append({"team_id": tid, "name": self.team_name(tid), "leagues": leagues, "seasons": seasons,
                        "latest_season": seasons[-1]})
        return out

    def _season(self, team_id: str, season: str | None) -> str | None:
        seasons = self._seasons.get(team_id)
        if not seasons:
            return None
        return seasons[-1] if season is None else season

    def team_style(self, team_id: str, season: str | None = None) -> TeamStyle | None:
        s = self._season(team_id, season)
        return self._styles.get((team_id, s)) if s else None

    def mentality(self, team_id: str, season: str | None = None) -> Mentality | None:
        s = self._season(team_id, season)
        return self._mentality.get((team_id, s)) if s else None

    def playbook(self, team_id: str, season: str | None = None) -> Playbook | None:
        if season is None:
            seasons = sorted(s for (t, s) in self._playbooks if t == team_id)
            season = seasons[-1] if seasons else None
        return self._playbooks.get((team_id, season)) if season else None

    def club(self, team_id: str) -> ClubProfile | None:
        return self._clubs.get(team_id)

    def manager(self, manager_id: str) -> ManagerProfile | None:
        return self._managers.get(manager_id)

    # -- players
    def player(self, player_id: str, season: str | None = None) -> PlayerProfile | None:
        rows = self._load_players().get(player_id)
        if not rows:
            return None
        if season is None:
            return rows[-1]
        return next((p for p in reversed(rows) if p.season == season), None)

    def player_tags(self, player_id: str) -> list[str]:
        p = self.player(player_id)
        return list(p.role_tags) if p else []

    def team_players(self, team_id: str, season: str | None = None) -> list[PlayerProfile]:
        """The squad by minutes played, most first."""
        self._load_players()
        s = self._season(team_id, season)
        return list(self._by_team_season.get((team_id, s), ())) if s else []

    # -- matches
    def matches(self, team: str | None = None, season: str | None = None, opponent: str | None = None,
                tags: Iterable[str | PatternTag] | None = None, result: str | None = None,
                league: str | League | None = None, limit: int = 50) -> list[MatchSummary]:
        """Reasoned matches, newest first.

        ``team`` and ``opponent`` are ids or names. ``tags`` must all be shown by the team (or by either side when
        no team is given); ``result`` is "win" / "draw" / "loss" for the team and needs ``team``.
        """
        team_id = self._team_arg(team)
        opp_id = self._team_arg(opponent)
        if (team and team_id is None) or (opponent and opp_id is None):
            return []
        wanted = {PatternTag(t) for t in tags} if tags else set()
        league_v = League(league).value if league else None
        out: list[MatchSummary] = []
        for m in self._load_matches():
            if season and m.season != season:
                continue
            if league_v and m.league.value != league_v:
                continue
            if team_id and team_id not in (m.home_id, m.away_id):
                continue
            if opp_id and not (opp_id in (m.home_id, m.away_id) and (team_id is None or opp_id != team_id)):
                continue
            if result:
                if team_id is None or m.result_for(team_id) != result:
                    continue
            if wanted:
                side = None if team_id is None else ("home" if m.home_id == team_id else "away")
                if not wanted <= m.tags(side):
                    continue
            out.append(m)
            if len(out) >= limit:
                break
        return out

    def _team_arg(self, value: str | None) -> str | None:
        if not value:
            return None
        return value if value in self._seasons else self.resolve_team(value)

    def search(self, text: str, limit: int = 20) -> list[SearchHit]:
        """Keyword search over match headlines, patterns and team names (simple weighted token hits)."""
        words = [w for w in _fold(text).split() if w not in _STOP]
        if not words:
            return []
        hits: list[SearchHit] = []
        for tid in self.team_ids():
            name_tok = _fold(self.team_name(tid)).split()
            score = sum(3.0 for w in words if w in name_tok or any(t.startswith(w) for t in name_tok))
            if score:
                hits.append(SearchHit(kind="team", id=tid, title=self.team_name(tid),
                                      subtitle=f"{len(self.seasons(tid))} seasons", score=score))
        for m in self._load_matches():
            head = _fold(m.headline)
            body = _fold(m.why_won)
            tag_words = " ".join(_fold(p.tag.value) for p in m.patterns)
            teams = _fold(f"{m.home_name} {m.away_name}")
            score = 0.0
            for w in words:
                score += 3.0 * (w in head.split()) + 1.0 * (w in body.split()) + 2.0 * (w in tag_words.split())
                score += 2.0 * (w in teams.split())
            if score:
                score += max(p.strength for p in m.patterns) * 0.1 if m.patterns else 0.0
                hits.append(SearchHit(kind="match", id=m.match_key, title=m.headline,
                                      subtitle=f"{m.home_name} {m.score} {m.away_name}, {m.date}", score=round(score, 3)))
        hits.sort(key=lambda h: (-h.score, h.kind, h.id))
        return hits[:limit]

    # -- team resolution
    def _build_name_index(self) -> dict[str, list[frozenset[str]]]:
        index: dict[str, list[frozenset[str]]] = {}
        for tid in self._seasons:
            names = {self._names.get(tid, tid), tid.replace("-", " ")}
            if tid in self._clubs:
                names.add(self._clubs[tid].name)
            names |= {s.team_name for (t, _), s in self._styles.items() if t == tid}
            index[tid] = [t for t in (_tokens(n) for n in names) if t]
        return index

    def resolve_team(self, name: str | None) -> str | None:
        """Team id for an id, a name or a slug, tolerant of "FC", accents, "Man Utd" style short forms."""
        if not name:
            return None
        if name in self._seasons:
            return name
        slug = _fold(name).replace(" ", "-")
        if slug in self._seasons:
            return slug
        q = _tokens(name)
        if not q:
            return None
        best: list[tuple[float, str]] = []
        for tid, variants in self._name_tokens.items():
            score = 0.0
            for tok in variants:
                if tok == q:
                    score = max(score, 2.0)
                elif q <= tok or tok <= q:
                    score = max(score, len(q & tok) / len(q | tok))
            if score >= 0.5:
                best.append((score, tid))
        if best:
            best.sort(key=lambda b: (-b[0], b[1]))
            if len(best) == 1 or best[0][0] > best[1][0]:
                return best[0][1]
            return None  # ambiguous ("Manchester")
        folded = " ".join(sorted(q))
        close: list[tuple[float, str]] = []
        for tid, variants in self._name_tokens.items():
            for tok in variants:
                ratio = difflib.SequenceMatcher(None, folded, " ".join(sorted(tok))).ratio()
                if ratio >= 0.82:
                    close.append((ratio, tid))
        if close:
            close.sort(key=lambda b: (-b[0], b[1]))
            if len(close) == 1 or close[0][0] - close[1][0] > 0.03 or close[0][1] == close[1][1]:
                return close[0][1]
        return None

    # -- compact context
    def team_context(self, team_id: str, season: str | None = None) -> dict[str, Any]:
        """Compact JSON for the tactician: style, patterns, mentality, manager, key players, recent headlines.

        Stays under ``CONTEXT_BUDGET`` characters; an empty dict for an unknown team.
        """
        style = self.team_style(team_id, season)
        if style is None:
            return {}
        pb = self.playbook(team_id, style.season)
        mentality = self.mentality(team_id, style.season)
        manager = self.manager(style.manager_id) if style.manager_id else None
        if pb is not None and pb.manager_id:
            manager = self.manager(pb.manager_id) or manager

        ranked = sorted((m for m in style.metrics if m.value is not None and m.pct is not None),
                        key=lambda m: (-abs(m.pct - 50), m.key))
        ctx: dict[str, Any] = {
            "team": {"team_id": team_id, "name": style.team_name, "league": style.league.value,
                     "season": style.season, "matches": style.matches},
            "manager": None if manager is None else {"manager_id": manager.manager_id, "name": manager.name},
            "formations": [f"{f.formation} {round(f.share * 100)}%" for f in style.formations[:3]],
            "style": [{"key": m.key, "value": _sig(m.value), "pct": round(m.pct), "proxy": m.is_proxy}
                      for m in ranked[:6]],
        }
        if pb is not None:
            ctx["patterns_for"] = [_pattern(f) for f in pb.patterns_for[:4]]
            ctx["patterns_against"] = [_pattern(f) for f in pb.patterns_against[:3]]
            ctx["strengths"] = [_clip(s, 120) for s in pb.strengths[:2]]
            ctx["weaknesses"] = [_clip(s, 120) for s in pb.weaknesses[:2]]
            ctx["game_state"] = [_clip(s, 120) for s in pb.game_state[:2]]
        if mentality is not None:
            ctx["mentality"] = [_clip(n, 110) for n in mentality.notes[:3]]
        key_ids = list(pb.key_players) if pb is not None else []
        players: list[PlayerProfile] = []
        for pid in key_ids:
            p = self.player(pid, style.season)
            if p is not None:
                players.append(p)
        for p in self.team_players(team_id, style.season):
            if len(players) >= 5:
                break
            if p.player_id not in {q.player_id for q in players}:
                players.append(p)
        ctx["key_players"] = [{"player_id": p.player_id, "name": p.name, "tags": p.role_tags[:3]}
                              for p in players[:5]]
        ctx["recent"] = [{"match_key": m.match_key, "score": m.score, "headline": _clip(m.headline, 100)}
                         for m in self.matches(team=team_id, season=style.season, limit=3)]
        return _fit(ctx)


def _pattern(f: Any) -> dict[str, Any]:
    return {"tag": f.tag.value, "share_pct": round(f.share * 100),
            "win_pct": None if f.win_rate is None else round(f.win_rate * 100)}


def _sig(x: float | None) -> float | None:
    return None if x is None else float(f"{x:.3g}")


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _fit(ctx: dict[str, Any]) -> dict[str, Any]:
    """Trim the least important lists until the JSON fits ``CONTEXT_BUDGET``."""
    order = [("recent", 1), ("style", 4), ("key_players", 3), ("patterns_against", 2), ("strengths", 1),
             ("weaknesses", 1), ("game_state", 1), ("mentality", 1), ("patterns_for", 2), ("style", 2)]
    steps = iter(order)
    while len(json.dumps(ctx, ensure_ascii=False)) > CONTEXT_BUDGET:
        try:
            key, keep = next(steps)
        except StopIteration:
            break
        if isinstance(ctx.get(key), list):
            ctx[key] = ctx[key][:keep]
    return ctx


# ------------------------------------------------------------------ files


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is None:
            raise FileNotFoundError(f"{path} not found; run: python -m matchlens.leaguedata export")
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_lines(path: Path) -> Iterable[str]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield line


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _dump_lines(path: Path, rows: Iterable[BaseModel]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row.model_dump_json() + "\n")
            n += 1
    return n


def write_bundle(directory: Path, *, styles: Sequence[TeamStyle], mentality: Sequence[Mentality] = (),
                 players: Iterable[PlayerProfile] = (), clubs: Mapping[str, ClubProfile] | None = None,
                 managers: Mapping[str, ManagerProfile] | None = None,
                 reasonings: Iterable[MatchReasoning] = (), playbooks: Sequence[Playbook] = (),
                 curated_version: str | None = None) -> Path:
    """Write the bundle atomically (a half-written folder never replaces a good one)."""
    directory = Path(directory)
    work = directory.with_name(directory.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    names = {s.team_id: s.team_name for s in sorted(styles, key=lambda s: s.season)}
    for tid, club in (clubs or {}).items():
        names.setdefault(tid, club.name)
    ordered = sorted(styles, key=lambda s: (s.team_id, s.season, s.league.value))
    latest: dict[str, str] = {}
    for s in ordered:
        latest[s.team_id] = max(latest.get(s.team_id, s.season), s.season)
    _dump(work / "teams.json", {"latest": latest, "styles": [s.model_dump(mode="json") for s in ordered]})
    _dump(work / "mentality.json", [m.model_dump(mode="json")
                                    for m in sorted(mentality, key=lambda m: (m.team_id, m.season))])
    n_players = _dump_lines(work / "players.jsonl", sorted(players, key=lambda p: (p.player_id, p.season, p.team_id)))
    _dump(work / "clubs.json", {k: v.model_dump(mode="json") for k, v in sorted((clubs or {}).items())})
    _dump(work / "managers.json", {k: v.model_dump(mode="json") for k, v in sorted((managers or {}).items())})
    n_matches = _dump_lines(work / "reasonings.jsonl",
                            (summarise_reasoning(r, names) for r in sorted(reasonings, key=lambda r: r.match_key)))
    _dump(work / "playbooks.json", [p.model_dump(mode="json")
                                    for p in sorted(playbooks, key=lambda p: (p.team_id, p.season))])
    counts = {"team_seasons": len(ordered), "teams": len(latest), "mentality": len(mentality), "players": n_players,
              "clubs": len(clubs or {}), "managers": len(managers or {}), "reasonings": n_matches,
              "playbooks": len(playbooks)}
    _dump(work / "index.json", {"format": FORMAT, "curated_version": curated_version, "counts": counts,
                                "built_at": datetime.now(UTC).isoformat(timespec="seconds")})
    old = directory.with_name(directory.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if directory.exists():
        directory.rename(old)
    work.rename(directory)
    shutil.rmtree(old, ignore_errors=True)
    return directory


def export(cfg: Any, leagues: Iterable[League] | None = None, seasons: Iterable[str] | None = None) -> Path:
    """Build ``cfg.kb_dir`` from the curated dataset, the reasonings and the playbooks (needs DuckDB)."""
    from matchlens.leaguedata.curate import latest
    from matchlens.leaguedata.knowledge import Knowledge
    from matchlens.leaguedata.playbook import Inputs, load_playbooks, load_reasonings
    from matchlens.leaguedata.schema import DEFAULT_LEAGUES, SEASONS

    leagues = list(leagues or DEFAULT_LEAGUES)
    seasons = list(seasons or SEASONS)
    curated = latest(cfg)
    inputs = Inputs(cfg)

    styles: list[TeamStyle] = []
    for lg, season in inputs.league_seasons(leagues, seasons):
        styles.extend(inputs.styles(lg, season).values())
    mentality: list[Mentality] = []
    for team_id, season in sorted({(s.team_id, s.season) for s in styles}):
        m = inputs.mentality(team_id, season)
        if m is not None:
            mentality.append(m)

    players: list[PlayerProfile] = []
    seen: set[tuple[str, str, str]] = set()
    for lg, season in inputs.league_seasons(leagues, seasons):  # domestic leagues come before the UCL
        for p in inputs.player_profiles(lg, season):
            if (p.player_id, p.team_id, p.season) not in seen:
                seen.add((p.player_id, p.team_id, p.season))
                players.append(p)

    knowledge = Knowledge.from_tables({
        "clubs": inputs.clubs, "managers": inputs._table("managers"), "manager_spells": inputs._table("manager_spells"),
        "honours": inputs._table("honours"), "text_docs": inputs._table("text_docs"), "matches": inputs.matches})
    clubs: dict[str, ClubProfile] = {}
    for team_id in sorted({s.team_id for s in styles}):
        try:
            clubs[team_id] = knowledge.club(team_id)
        except KeyError:
            log.debug("no club row for %s", team_id)
    manager_ids = sorted({s.manager_id for s in styles if s.manager_id} | {m for p in load_playbooks(cfg)
                                                                          for m in [p.manager_id] if m})
    managers: dict[str, ManagerProfile] = {}
    for mid in manager_ids:
        try:
            managers[mid] = knowledge.manager(mid)
        except KeyError:
            log.debug("no manager row for %s", mid)

    return write_bundle(
        cfg.kb_dir, styles=styles, mentality=mentality, players=players, clubs=clubs, managers=managers,
        reasonings=load_reasonings(cfg, leagues, seasons),
        playbooks=[p for p in load_playbooks(cfg) if p.league in leagues and p.season in seasons],
        curated_version=curated.name)


def main(cfg: Any, args: Any) -> int:
    """Entry point of ``python -m matchlens.leaguedata export``."""
    try:
        path = export(cfg, getattr(args, "leagues", None), getattr(args, "seasons", None))
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    index = json.loads((path / "index.json").read_text(encoding="utf-8"))
    print(f"knowledge base -> {path} (curated {index['curated_version']})")
    for name, n in index["counts"].items():
        print(f"  {name:<14}{n:>8}")
    for f in sorted(path.iterdir()):
        print(f"  {f.name:<18}{f.stat().st_size / 1e6:>9.2f} MB")
    return 0


# ------------------------------------------------------------------ access


_CACHE: dict[str, KnowledgeBase] = {}
_CACHE_LOCK = threading.Lock()


def get_kb() -> KnowledgeBase | None:
    """The bundle named by ``MATCHLENS_KB_DIR`` (cached per path); None when unset or unreadable."""
    value = os.getenv(KB_DIR_ENV)
    if not value:
        return None
    with _CACHE_LOCK:
        kb = _CACHE.get(value)
        if kb is None:
            try:
                kb = KnowledgeBase.load(value)
            except (OSError, ValueError, KeyError) as exc:
                log.warning("knowledge base at %s is not usable: %s", value, exc)
                return None
            _CACHE[value] = kb
        return kb


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = ["KnowledgeBase", "MatchSummary", "SearchHit", "export", "get_kb", "main", "reset_cache",
           "summarise_reasoning", "write_bundle"]
