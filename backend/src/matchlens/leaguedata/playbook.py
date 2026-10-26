"""Team playbooks: match reasonings aggregated per team-season, then written up by one strong-model call.

    aggregate(team_id, league, season, reasonings, matches) -> Aggregate     (deterministic)
    prepare_playbook(...) -> Prepared                                        (payload + inputs hash)
    build_playbook(prepared, clients) -> BuiltPlaybook                       (one call, fact-checked)
    main(cfg, args)                                                          (CLI ``playbooks``)

``aggregate`` counts, for every ``PatternTag``, the share of the team's reasoned matches in which the tag
was shown by the team ("for") or by its opponents ("against"), the team's win rate in those matches and up
to three example match keys. When the manager changed during the season it also returns one split per
manager. Nothing in it comes from a model.

The model only writes prose (``PlaybookDraft``): strengths, weaknesses, game-state behaviour, key players
and a summary. It sees the aggregates, the style metrics, the mentality and that team's match headlines,
nothing else. Numbers in its text must appear in what it was given and key players must be candidates; a
draft that fails is repaired once and then replaced by a deterministic template, so a playbook is always
produced and never states a number that was not computed.

Each playbook is stored next to its provenance in ``<data>/playbooks/<league>/<season>/<team_id>.json`` and is
rebuilt only when the hash of its inputs changes. ``kb.export`` bundles them for runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.contracts import (
    Mentality,
    MatchReasoning,
    PatternFreq,
    PatternTag,
    PlayerProfile,
    Playbook,
    TeamStyle,
)
from matchlens.leaguedata.schema import League, Match

log = logging.getLogger(__name__)

PROMPT_VERSION = "league_playbook/1"
MAX_EXAMPLES = 3
MAX_PATTERNS = 10  # per list in the playbook
MAX_SPLIT_PATTERNS = 5
MAX_HEADLINES = 30  # headlines shown to the model
MAX_CANDIDATES = 12
MAX_EVIDENCE = 12
CONCURRENCY = 4
CALL_TIMEOUT_S = 150.0
SMALL_COUNTS = {float(n) for n in range(0, 12)} | {45.0, 90.0, 100.0}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ------------------------------------------------------------------ aggregation


class ManagerSplit(_Model):
    """One manager's matches within a season in which the manager changed."""

    manager_id: str
    matches: int
    first_date: str
    last_date: str
    patterns_for: list[PatternFreq]
    patterns_against: list[PatternFreq]


class Headline(_Model):
    match_key: str
    date: str
    opponent_id: str
    home: bool
    result: str | None  # "W" / "D" / "L" for the team
    score: str | None  # team goals first: "2-1"
    verdict: str
    headline: str


class Aggregate(_Model):
    team_id: str
    league: League
    season: str
    manager_id: str | None  # of the team's latest reasoned match
    matches: int  # reasoned matches of the team in the season (all competitions)
    wins: int
    draws: int
    losses: int
    patterns_for: list[PatternFreq]
    patterns_against: list[PatternFreq]
    manager_splits: list[ManagerSplit]
    evidence_matches: list[str]
    headlines: list[Headline]


@dataclass
class _Obs:
    """What one reasoned match says about the team."""

    key: str
    date: str
    is_home: bool
    opponent_id: str
    manager_id: str | None
    result: str | None
    score: str | None
    reasoning: MatchReasoning
    tags_for: dict[PatternTag, float] = field(default_factory=dict)  # tag -> strongest strength
    tags_against: dict[PatternTag, float] = field(default_factory=dict)


_SCORE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)")


def _date_of(match_key: str) -> str:
    parts = match_key.split(":")
    return parts[2] if len(parts) > 2 else ""


def _result(goals_for: int, goals_against: int) -> str:
    return "W" if goals_for > goals_against else "L" if goals_for < goals_against else "D"


def _observe(team_id: str, r: MatchReasoning, match: Match | None) -> _Obs:
    is_home = r.home_id == team_id
    gf = ga = None
    if match is not None:
        gf, ga = (match.home_goals, match.away_goals) if is_home else (match.away_goals, match.home_goals)
    else:
        m = _SCORE.match(r.score)
        if m:
            h, a = int(m.group(1)), int(m.group(2))
            gf, ga = (h, a) if is_home else (a, h)
    manager = None
    if match is not None:
        manager = match.home_manager_id if is_home else match.away_manager_id
    obs = _Obs(key=r.match_key, date=match.date if match is not None else _date_of(r.match_key), is_home=is_home,
               opponent_id=r.away_id if is_home else r.home_id, manager_id=manager,
               result=None if gf is None else _result(gf, ga), score=None if gf is None else f"{gf}-{ga}",
               reasoning=r)
    mine, theirs = ("home", "away") if is_home else ("away", "home")
    for p in r.draft.patterns:
        target = obs.tags_for if p.side == mine else obs.tags_against if p.side == theirs else None
        if target is not None:
            target[p.tag] = max(p.strength, target.get(p.tag, 0.0))
    return obs


def _freqs(obs: Sequence[_Obs], side: str, limit: int) -> list[PatternFreq]:
    """Pattern frequencies over ``obs``: ``side`` is "for" (team's own tags) or "against" (opponents')."""
    n = len(obs)
    if n == 0:
        return []
    by_tag: dict[PatternTag, list[tuple[float, _Obs]]] = defaultdict(list)
    for o in obs:
        for tag, strength in (o.tags_for if side == "for" else o.tags_against).items():
            by_tag[tag].append((strength, o))
    out: list[PatternFreq] = []
    for tag, hits in by_tag.items():
        decided = [o for _, o in hits if o.result is not None]
        win_rate = round(sum(o.result == "W" for o in decided) / len(decided), 4) if decided else None
        examples = [o.key for _, o in sorted(hits, key=lambda h: (-h[0], h[1].key))[:MAX_EXAMPLES]]
        out.append(PatternFreq(tag=tag, share=round(len(hits) / n, 4), win_rate=win_rate, example_keys=examples))
    out.sort(key=lambda f: (-f.share, f.tag.value))
    return out[:limit]


def aggregate(team_id: str, league: League, season: str, reasonings: Iterable[MatchReasoning],
              matches: Iterable[Match] = ()) -> Aggregate:
    """Deterministic aggregation of the team's reasonings in one season (every competition in the data).

    ``matches`` supplies results and managers; a reasoning without a ``Match`` row falls back to its score
    and has no manager.
    """
    by_key = {m.match_key: m for m in matches}
    obs = [_observe(team_id, r, by_key.get(r.match_key)) for r in reasonings
           if r.season == season and team_id in (r.home_id, r.away_id)]
    obs.sort(key=lambda o: (o.date, o.key))
    patterns_for = _freqs(obs, "for", MAX_PATTERNS)
    patterns_against = _freqs(obs, "against", MAX_PATTERNS)

    managers: dict[str, list[_Obs]] = defaultdict(list)
    for o in obs:
        if o.manager_id:
            managers[o.manager_id].append(o)
    splits: list[ManagerSplit] = []
    if len(managers) >= 2:
        for mid, group in sorted(managers.items(), key=lambda kv: (kv[1][0].date, kv[0])):
            splits.append(ManagerSplit(manager_id=mid, matches=len(group), first_date=group[0].date,
                                       last_date=group[-1].date,
                                       patterns_for=_freqs(group, "for", MAX_SPLIT_PATTERNS),
                                       patterns_against=_freqs(group, "against", MAX_SPLIT_PATTERNS)))
    latest_manager = next((o.manager_id for o in reversed(obs) if o.manager_id), None)

    evidence: list[str] = []
    for freq in [*patterns_for[:4], *patterns_against[:4]]:
        for key in freq.example_keys[:2]:
            if key not in evidence:
                evidence.append(key)
    evidence = evidence[:MAX_EVIDENCE]

    return Aggregate(
        team_id=team_id, league=league, season=season, manager_id=latest_manager, matches=len(obs),
        wins=sum(o.result == "W" for o in obs), draws=sum(o.result == "D" for o in obs),
        losses=sum(o.result == "L" for o in obs), patterns_for=patterns_for, patterns_against=patterns_against,
        manager_splits=splits, evidence_matches=evidence,
        headlines=[Headline(match_key=o.key, date=o.date, opponent_id=o.opponent_id, home=o.is_home,
                            result=o.result, score=o.score, verdict=o.reasoning.draft.verdict,
                            headline=o.reasoning.draft.headline) for o in obs])


# ------------------------------------------------------------------ model output


class PlaybookDraft(_Strict):
    """What the model returns (strict schema: no defaults, no dicts)."""

    strengths: list[str]
    weaknesses: list[str]
    game_state: list[str]
    key_players: list[str]  # player_ids from "candidates"
    summary: str


def _label(key: str) -> str:
    return key.replace("_", " ")


def _pct_int(x: float | None) -> int | None:
    return None if x is None else round(x * 100)


def _freq_payload(f: PatternFreq) -> dict[str, Any]:
    return {"tag": f.tag.value, "label": _label(f.tag.value), "share": f.share, "share_pct": _pct_int(f.share),
            "win_rate": f.win_rate, "win_pct": _pct_int(f.win_rate), "example_keys": f.example_keys}


def _round(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def candidate_players(players: Sequence[PlayerProfile], limit: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    """The team-season's most involved players: minutes-weighted attacking output, then minutes."""

    def per90(p: PlayerProfile, key: str) -> float:
        m = next((m for m in p.per90 if m.key == key), None)
        return m.value if m is not None and m.value is not None else 0.0

    def weight(p: PlayerProfile) -> tuple[float, int, str]:
        output = (per90(p, "npxg") + per90(p, "xa")) * p.minutes / 90.0
        return (-output, -p.minutes, p.player_id)

    best = sorted((p for p in players if p.minutes > 0), key=weight)[:limit]
    return [{"player_id": p.player_id, "name": p.name, "positions": p.positions[:2], "minutes": p.minutes,
             "tags": p.role_tags[:4]} for p in best]


def make_payload(agg: Aggregate, style: TeamStyle, mentality: Mentality | None,
                 candidates: Sequence[Mapping[str, Any]] = (), names: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Everything the model (or the template) may use, as plain JSON."""
    names = names or {}
    headlines = agg.headlines
    if len(headlines) > MAX_HEADLINES:  # even spread through the season, deterministic
        step = len(headlines) / MAX_HEADLINES
        headlines = [headlines[int(i * step)] for i in range(MAX_HEADLINES)]
    ment: dict[str, Any] | None = None
    if mentality is not None:
        ment = {k: v for k, v in mentality.model_dump(mode="json").items() if k not in {"team_id", "league"}}
    return {
        "task": "league_playbook",
        "team": {"team_id": agg.team_id, "name": style.team_name or names.get(agg.team_id, agg.team_id),
                 "league": agg.league.value, "season": agg.season, "manager_id": agg.manager_id},
        "record": {"matches": agg.matches, "wins": agg.wins, "draws": agg.draws, "losses": agg.losses},
        "aggregates": {
            "patterns_for": [_freq_payload(f) for f in agg.patterns_for],
            "patterns_against": [_freq_payload(f) for f in agg.patterns_against],
            "manager_splits": [{"manager_id": s.manager_id, "matches": s.matches, "first_date": s.first_date,
                                "last_date": s.last_date,
                                "patterns_for": [_freq_payload(f) for f in s.patterns_for],
                                "patterns_against": [_freq_payload(f) for f in s.patterns_against]}
                               for s in agg.manager_splits],
        },
        "style": {
            "matches": style.matches,
            "formations": [{"formation": f.formation, "share_pct": _pct_int(f.share)} for f in style.formations[:4]],
            "metrics": [{"key": m.key, "label": _label(m.key), "value": _round(m.value), "pct": _round(m.pct, 0),
                         "is_proxy": m.is_proxy} for m in style.metrics if m.value is not None],
        },
        "mentality": ment,
        "headlines": [{"match_key": h.match_key, "date": h.date, "opponent": names.get(h.opponent_id, h.opponent_id),
                       "home": h.home, "score": h.score, "result": h.result, "verdict": h.verdict,
                       "headline": h.headline} for h in headlines],
        "candidates": list(candidates),
    }


# ------------------------------------------------------------------ template and fact-check


def _share_text(f: dict[str, Any], who: str) -> str:
    text = f"{who} {f['label']} in {f['share_pct']}% of matches"
    if f["win_pct"] is not None:
        text += f" (win rate {f['win_pct']}% in those)"
    return text


def template_draft(payload: Mapping[str, Any]) -> PlaybookDraft:
    """Deterministic playbook text: every number is copied from the payload, so it always passes the check."""
    team = payload["team"]
    agg = payload["aggregates"]
    metrics = [m for m in payload["style"]["metrics"] if m.get("pct") is not None]
    high = sorted((m for m in metrics if m["pct"] >= 75), key=lambda m: (-m["pct"], m["key"]))[:2]
    low = sorted((m for m in metrics if m["pct"] <= 25), key=lambda m: (m["pct"], m["key"]))[:2]

    strengths = [_share_text(f, "Showed") for f in agg["patterns_for"][:3]]
    strengths += [f"{m['label']} is in percentile {int(m['pct'])} of the league" for m in high]
    weaknesses = [_share_text(f, "Opponents showed") + " against them" for f in agg["patterns_against"][:3]]
    weaknesses += [f"{m['label']} is only in percentile {int(m['pct'])} of the league" for m in low]
    ment = payload.get("mentality") or {}
    game_state = [str(n) for n in (ment.get("notes") or [])[:3]]

    rec = payload["record"]
    top = agg["patterns_for"][0]["label"] if agg["patterns_for"] else None
    summary = (f"{team['name']} {team['season']}: {rec['wins']} wins, {rec['draws']} draws and {rec['losses']} "
               f"defeats in {rec['matches']} analysed matches.")
    if top:
        summary += f" Their most frequent pattern was {top}."
    if agg["manager_splits"]:
        summary += f" The manager changed during the season ({len(agg['manager_splits'])} managers)."
    return PlaybookDraft(
        strengths=strengths or ["No standout pattern in the available matches."],
        weaknesses=weaknesses or ["No recurring weakness pattern in the available matches."],
        game_state=game_state or ["No game-state data available."],
        key_players=[c["player_id"] for c in payload.get("candidates", [])[:5]],
        summary=summary)


def check_draft(draft: PlaybookDraft, payload: Mapping[str, Any]) -> list[str]:
    """Problems that block a draft: invented numbers, unknown players, missing or oversized fields."""
    from matchlens.factcheck.verify import numbers_in, unaccounted_numbers

    problems: list[str] = []
    allowed = numbers_in(payload) | SMALL_COUNTS
    texts = {"strengths": draft.strengths, "weaknesses": draft.weaknesses, "game_state": draft.game_state,
             "summary": [draft.summary]}
    for name, items in texts.items():
        if not items or any(not s.strip() for s in items):
            problems.append(f"{name} must not be empty")
        if len(items) > 6:
            problems.append(f"{name} has {len(items)} items; at most 6")
        for text in items:
            if len(text) > 400:
                problems.append(f"{name}: an item is longer than 400 characters")
        bad = unaccounted_numbers(" ".join(items), allowed)
        if bad:
            problems.append(f"{name}: numbers not in the data: {', '.join(bad)}")
    known = {c["player_id"] for c in payload.get("candidates", [])}
    unknown = [p for p in draft.key_players if p not in known]
    if unknown:
        problems.append(f"key_players not among the candidates: {', '.join(unknown)}")
    if len(draft.key_players) > 6:
        problems.append("key_players has more than 6 players")
    return problems


# ------------------------------------------------------------------ building


@dataclass(frozen=True)
class Prepared:
    agg: Aggregate
    style: TeamStyle
    mentality: Mentality | None
    payload: dict[str, Any]

    def inputs_hash(self, model_label: str) -> str:
        blob = json.dumps({"payload": self.payload, "style": self.style.model_dump(mode="json"),
                           "prompt": PROMPT_VERSION, "model": model_label}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BuiltPlaybook:
    playbook: Playbook
    fact_check: str  # "passed" | "repaired" | "template"
    problems: list[str]
    manager_splits: list[ManagerSplit]
    model: str


def prepare_playbook(team_id: str, league: League, season: str, *, reasonings: Iterable[MatchReasoning],
                     matches: Iterable[Match], style: TeamStyle, mentality: Mentality | None = None,
                     players: Sequence[PlayerProfile] = (), names: Mapping[str, str] | None = None) -> Prepared:
    agg = aggregate(team_id, league, season, reasonings, matches)
    payload = make_payload(agg, style, mentality, candidate_players(players), names)
    return Prepared(agg=agg, style=style, mentality=mentality, payload=payload)


async def _call(agent: Any, payload: dict[str, Any], timeout: float) -> tuple[PlaybookDraft | None, str | None]:
    from matchlens.agents.pipeline import call_agent

    return await call_agent(agent, payload, PlaybookDraft, timeout=timeout)


def _assemble(prep: Prepared, draft: PlaybookDraft) -> Playbook:
    agg = prep.agg
    return Playbook(
        team_id=agg.team_id, league=agg.league, season=agg.season, manager_id=agg.manager_id or prep.style.manager_id,
        style=prep.style, mentality=prep.mentality, patterns_for=agg.patterns_for, patterns_against=agg.patterns_against,
        strengths=[s.strip() for s in draft.strengths], weaknesses=[s.strip() for s in draft.weaknesses],
        game_state=[s.strip() for s in draft.game_state], key_players=list(dict.fromkeys(draft.key_players)),
        summary=draft.summary.strip(), evidence_matches=agg.evidence_matches)


async def build_playbook(prep: Prepared, clients: Any | None = None, *, timeout_s: float = CALL_TIMEOUT_S) -> BuiltPlaybook:
    """One strong-model call (plus at most one repair turn); the template when it fails or has no data."""
    template = template_draft(prep.payload)
    model = "template"
    if clients is None or prep.agg.matches == 0:
        return BuiltPlaybook(_assemble(prep, template), "template", [], prep.agg.manager_splits, model)
    from agent_framework import Agent

    from matchlens.agents import prompts

    opts = clients.options(strong=True)
    agent = Agent(clients.strong, prompts.LEAGUE_PLAYBOOK, name="league_playbook",
                  description="Writes a team-season playbook from aggregated match reasonings",
                  default_options=opts or None)
    model = clients.label
    payload = prep.payload
    problems: list[str] = []
    draft, err = await _call(agent, payload, timeout_s)
    if err:
        problems = [err]
    elif draft is not None:
        problems = check_draft(draft, payload)
        if not problems:
            return BuiltPlaybook(_assemble(prep, draft), "passed", [], prep.agg.manager_splits, model)
        repair = {**payload, "problems": problems, "previous": draft.model_dump(mode="json")}
        draft2, err2 = await _call(agent, repair, timeout_s)
        if draft2 is not None:
            again = check_draft(draft2, payload)
            if not again:
                return BuiltPlaybook(_assemble(prep, draft2), "repaired", problems, prep.agg.manager_splits, model)
            problems = problems + again
        elif err2:
            problems = problems + [err2]
    log.warning("playbook %s %s: using the template (%s)", prep.agg.team_id, prep.agg.season, "; ".join(problems))
    return BuiltPlaybook(_assemble(prep, template), "template", problems, prep.agg.manager_splits, model)


# ------------------------------------------------------------------ storage


def playbooks_dir(cfg: Config) -> Path:
    return cfg.root / "playbooks"


def playbook_path(cfg: Config, league: League, season: str, team_id: str) -> Path:
    return playbooks_dir(cfg) / league.value / season / f"{team_id}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_playbook(cfg: Config, built: BuiltPlaybook, inputs_hash: str) -> Path:
    pb = built.playbook
    record = {"inputs_hash": inputs_hash, "model": built.model, "fact_check": built.fact_check,
              "problems": built.problems, "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "manager_splits": [s.model_dump(mode="json") for s in built.manager_splits],
              "playbook": pb.model_dump(mode="json")}
    path = playbook_path(cfg, pb.league, pb.season, pb.team_id)
    _atomic_write(path, json.dumps(record, indent=1, ensure_ascii=False))
    return path


def stored_hash(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("inputs_hash")
    except (OSError, ValueError):
        return None


def load_playbooks(cfg: Config) -> list[Playbook]:
    out: list[Playbook] = []
    for path in sorted(playbooks_dir(cfg).glob("*/*/*.json")):
        try:
            out.append(Playbook.model_validate(json.loads(path.read_text(encoding="utf-8"))["playbook"]))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("skipping unreadable playbook %s: %s", path, exc)
    return out


def load_reasonings(cfg: Config, leagues: Iterable[League] | None = None,
                    seasons: Iterable[str] | None = None) -> list[MatchReasoning]:
    """Every verified ``MatchReasoning`` under ``cfg.reasoning_dir`` (blocked and unreadable files are skipped)."""
    wanted_l = {lg.value for lg in leagues} if leagues else None
    wanted_s = set(seasons) if seasons else None
    out: list[MatchReasoning] = []
    for path in sorted(cfg.reasoning_dir.glob("*/*/*.json")):
        if (wanted_l and path.parent.parent.name not in wanted_l) or (wanted_s and path.parent.name not in wanted_s):
            continue
        try:
            r = MatchReasoning.model_validate(json.loads(path.read_text(encoding="utf-8")))
            if r.provenance.fact_check != "blocked":  # blocked = data-only template after two failed checks
                out.append(r)
        except (OSError, ValueError) as exc:
            log.warning("skipping unreadable reasoning %s: %s", path, exc)
    return out


# ------------------------------------------------------------------ curated inputs


class Inputs:
    """Curated tables and the derived team knowledge, loaded lazily (needs DuckDB via ``curate.read_curated``)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _table(self, name: str) -> list[Any]:
        from matchlens.leaguedata.curate import read_curated

        return read_curated(self.cfg, name)

    @cached_property
    def matches(self) -> list[Match]:
        return self._table("matches")

    @cached_property
    def clubs(self) -> list[Any]:
        return self._table("clubs")

    @cached_property
    def names(self) -> dict[str, str]:
        return {c.team_id: c.name for c in self.clubs}

    @cached_property
    def shots_by_match(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = defaultdict(list)
        for s in self._table("shots"):
            out[s.match_key].append(s)
        return out

    @cached_property
    def events_by_match(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = defaultdict(list)
        for e in self._table("match_events"):
            out[e.match_key].append(e)
        return out

    @cached_property
    def stats_by_match(self) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = defaultdict(list)
        for s in self._table("team_match_stats"):
            out[s.match_key].append(s)
        return out

    @cached_property
    def _style_builder(self) -> Any:
        from matchlens.leaguedata.style import StyleBuilder

        shots = [s for rows in self.shots_by_match.values() for s in rows]
        stats = [s for rows in self.stats_by_match.values() for s in rows]
        return StyleBuilder(self.matches, stats, shots, self._table("lineups"), self.names)

    @cached_property
    def _style_cache(self) -> dict[tuple[League, str], dict[str, TeamStyle]]:
        return {}

    def styles(self, league: League, season: str) -> dict[str, TeamStyle]:
        key = (league, season)
        if key not in self._style_cache:
            try:
                self._style_cache[key] = self._style_builder.league_styles(league, season)
            except KeyError:
                self._style_cache[key] = {}
        return self._style_cache[key]

    def league_seasons(self, leagues: Iterable[League], seasons: Iterable[str]) -> list[tuple[League, str]]:
        present = {(m.league, m.season) for m in self.matches}
        wanted_s = set(seasons)
        order = {lg: i for i, lg in enumerate(League)}
        return sorted(((lg, s) for lg, s in present if lg in set(leagues) and s in wanted_s),
                      key=lambda ls: (ls[1], ls[0] == League.UCL, order[ls[0]]))

    def team_seasons(self, leagues: Iterable[League], seasons: Iterable[str]) -> list[tuple[str, League, str]]:
        """(team_id, league, season): domestic league first; a team only in the UCL that season is listed under UCL."""
        out: list[tuple[str, League, str]] = []
        seen: set[tuple[str, str]] = set()
        for lg, season in self.league_seasons(leagues, seasons):
            for team_id in sorted(self.styles(lg, season)):
                if (team_id, season) not in seen:
                    seen.add((team_id, season))
                    out.append((team_id, lg, season))
        return out

    @cached_property
    def _matches_by_team_season(self) -> dict[tuple[str, str], list[Match]]:
        out: dict[tuple[str, str], list[Match]] = defaultdict(list)
        for m in self.matches:
            out[(m.home_id, m.season)].append(m)
            out[(m.away_id, m.season)].append(m)
        return out

    def team_matches(self, team_id: str, season: str) -> list[Match]:
        return self._matches_by_team_season.get((team_id, season), [])

    def mentality(self, team_id: str, season: str) -> Mentality | None:
        from matchlens.leaguedata.mentality import compute_mentality

        mine = self.team_matches(team_id, season)
        if not mine:
            return None
        keys = [m.match_key for m in mine]
        events = [e for k in keys for e in self.events_by_match.get(k, ())]
        shots = [s for k in keys for s in self.shots_by_match.get(k, ())]
        stats = [s for k in keys for s in self.stats_by_match.get(k, ())]
        return compute_mentality(team_id, season, self.matches, events, shots, stats, league=None)

    @cached_property
    def _profile_builder(self) -> Any:
        from matchlens.leaguedata.players import ProfileBuilder

        shots = [s for rows in self.shots_by_match.values() for s in rows]
        return ProfileBuilder(self._table("player_matches"), shots, self._table("lineups"),
                              self._table("players"), self._table("player_valuations"))

    def player_profiles(self, league: League, season: str) -> list[PlayerProfile]:
        return self._profile_builder.league_profiles(league, season)

    @cached_property
    def _profile_cache(self) -> dict[tuple[League, str], dict[str, list[PlayerProfile]]]:
        return {}

    def team_players(self, team_id: str, league: League, season: str) -> list[PlayerProfile]:
        key = (league, season)
        if key not in self._profile_cache:
            by_team: dict[str, list[PlayerProfile]] = defaultdict(list)
            for p in self.player_profiles(league, season):
                by_team[p.team_id].append(p)
            self._profile_cache[key] = by_team
        return self._profile_cache[key].get(team_id, [])


# ------------------------------------------------------------------ CLI


async def _run(cfg: Config, args: Any) -> int:
    from matchlens.agents.llm import build_clients
    from matchlens.leaguedata.schema import DEFAULT_LEAGUES, SEASONS

    leagues = list(getattr(args, "leagues", None) or DEFAULT_LEAGUES)
    seasons = list(getattr(args, "seasons", None) or SEASONS)
    limit = getattr(args, "limit", None)
    force = bool(getattr(args, "refresh", False))

    inputs = Inputs(cfg)
    reasonings = load_reasonings(cfg, leagues, seasons)
    by_team: dict[tuple[str, str], list[MatchReasoning]] = defaultdict(list)
    for r in reasonings:
        by_team[(r.home_id, r.season)].append(r)
        by_team[(r.away_id, r.season)].append(r)
    clients = build_clients()
    todo = inputs.team_seasons(leagues, seasons)
    if limit is not None:
        per: dict[tuple[League, str], int] = defaultdict(int)
        capped = []
        for team_id, lg, season in todo:
            if per[(lg, season)] < limit:
                per[(lg, season)] += 1
                capped.append((team_id, lg, season))
        todo = capped
    print(f"{len(reasonings)} reasonings, {len(todo)} team-seasons, model {clients.label}")

    sem = asyncio.Semaphore(CONCURRENCY)
    counts = {"built": 0, "skipped": 0, "template": 0}

    async def one(team_id: str, league: League, season: str) -> None:
        style = inputs.styles(league, season)[team_id]
        prep = prepare_playbook(team_id, league, season, reasonings=by_team.get((team_id, season), ()),
                                matches=inputs.team_matches(team_id, season), style=style,
                                mentality=inputs.mentality(team_id, season),
                                players=inputs.team_players(team_id, league, season), names=inputs.names)
        digest = prep.inputs_hash(clients.label)
        path = playbook_path(cfg, league, season, team_id)
        if not force and stored_hash(path) == digest:
            counts["skipped"] += 1
            return
        async with sem:
            built = await build_playbook(prep, clients)
        save_playbook(cfg, built, digest)
        counts["built"] += 1
        counts["template"] += built.fact_check == "template"
        print(f"  {league.value} {season} {team_id}: {built.fact_check}")

    results = await asyncio.gather(*(one(*ts) for ts in todo), return_exceptions=True)
    failed = [r for r in results if isinstance(r, BaseException)]
    for exc in failed:
        log.error("playbook failed: %r", exc)
    print(f"built {counts['built']} ({counts['template']} from the template), skipped {counts['skipped']} "
          f"unchanged, failed {len(failed)}")
    return 1 if failed else 0


def main(cfg: Config, args: Any) -> int:
    """Entry point of ``python -m matchlens.leaguedata playbooks`` (``--refresh`` rebuilds unchanged ones)."""
    try:
        return asyncio.run(_run(cfg, args))
    except FileNotFoundError as exc:
        print(str(exc))
        return 1


__all__ = [
    "Aggregate", "BuiltPlaybook", "Headline", "Inputs", "ManagerSplit", "PlaybookDraft", "Prepared", "aggregate",
    "build_playbook", "candidate_players", "check_draft", "load_playbooks", "load_reasonings", "main",
    "make_payload", "playbook_path", "prepare_playbook", "save_playbook", "template_draft",
]
