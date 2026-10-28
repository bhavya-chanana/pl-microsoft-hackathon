"""Match Reasoner: why the winner won, how, and the tactics, for every match of the curated dataset.

For one match the reasoner

1. builds the evidence ``Digest`` (digest.py; blind by default),
2. asks the strong model for a ``MatchReasoningDraft`` (one structured call),
3. verifies the draft against the digest (``verify``): every number in the text is a digest value or a
   minute, every evidence id exists, every ``{P:..}``/``{M:..}`` placeholder exists, each pattern's side
   matches the evidence it cites, the verdict agrees with the xG, turning points match their evidence,
4. on problems gives the model ONE rewrite turn with the list; a draft that still fails is replaced by the
   data-only template (``offline_draft``) and recorded as ``blocked``,
5. restores real names for the stored ``MatchReasoning`` (``restore_names``).

The batch runner (``run_batch``) does this for many matches with a semaphore, retries with backoff,
resume by ``digest_hash`` + ``PROMPT_VERSION`` + model, a failure ``manifest.jsonl`` and optional Azure OpenAI
Batch JSONL output instead of live calls::

    python -m matchlens.leaguedata reason --leagues EPL --seasons 2023-24 --limit 10

Environment: ``MATCHLENS_LEAGUE_MODEL`` (deployment for this task, default the strong model),
``MATCHLENS_REASON_CONCURRENCY`` (default 6), ``MATCHLENS_REASON_EMIT_BATCH`` (path of a Batch JSONL file).
Output: ``<reasoning_dir>/<league>/<season>/<match_key>.json`` (``:`` replaced by ``_``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import gzip
import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_framework import Agent

from matchlens.agents import prompts
from matchlens.agents.pipeline import call_agent
from matchlens.factcheck.verify import numbers_in
from matchlens.leaguedata.config import Config
from matchlens.leaguedata.contracts import (
    MatchReasoning,
    MatchReasoningDraft,
    PatternTag,
    Provenance,
    TacticalPatternDraft,
    TurningPointDraft,
    strip_citations,
)
from matchlens.leaguedata.digest import Digest, Tables, build_digest, estimate_tokens
from matchlens.leaguedata.schema import League
from matchlens.leaguedata.style import BINS

log = logging.getLogger(__name__)

PROMPT_VERSION = "league-reasoner-v2"
TASK = "league_match_reasoning"
REASON_TIMEOUT_S = 180.0
BACKOFF_S = 2.0  # first retry delay after a failed call; doubles each time
DEFAULT_CONCURRENCY = 6
DONE_STATUSES = ("passed", "rewritten", "blocked")  # a "template" result is a failure and is retried next run

# Patterns that describe what a side did with the ball: they may cite only that side's shots.
ATTACK_TAGS = frozenset({
    PatternTag.COUNTER_ATTACK, PatternTag.DIRECT_LONG_BALLS, PatternTag.CROSSING_WIDE_OVERLOAD,
    PatternTag.THROUGH_BALLS, PatternTag.SET_PIECE_THREAT, PatternTag.CORNER_THREAT, PatternTag.AERIAL_THREAT,
    PatternTag.LONG_RANGE_SHOOTING, PatternTag.REBOUNDS_SECOND_BALLS, PatternTag.FINISHING_OVER,
    PatternTag.FINISHING_UNDER, PatternTag.INDIVIDUAL_BRILLIANCE, PatternTag.PENALTY_DECISIVE,
    PatternTag.QUALITY_OVER_VOLUME, PatternTag.LATE_PUSH,
})
NEEDS_XG = frozenset({PatternTag.FINISHING_OVER, PatternTag.FINISHING_UNDER})

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_EVIDENCE_IN_TEXT = re.compile(r"\b(?:[SEGT]\d+|M:[A-Za-z0-9_]+)\b")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_MINUTE_AFTER = re.compile(r"^(?:\+\d+)?\s?(?:['’′]|\s?(?:min\b|mins\b|minutes?\b|-minute))")
_MINUTE_BEFORE = re.compile(r"(?:minute|min\.?)\s*$", re.I)
_ORDINAL_HALF = re.compile(r"^(?:st|nd)\s+half", re.I)
MAX_MINUTE = 130


# ===================================================================== offline draft


def _table(t: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not t:
        return []
    return [dict(zip(t["cols"], row, strict=True)) for row in t["rows"]]


def _bin_of(minute: int) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if lo <= minute < hi:
            return i
    return len(BINS) - 1


def _metric_rules() -> list[tuple[str, Any, PatternTag, str]]:
    return [
        ("ppda", lambda z: z <= -1.0, PatternTag.HIGH_PRESS, "pressed harder than usual (lower PPDA)"),
        ("deep_share", lambda z: z >= 1.0, PatternTag.TERRITORIAL_DOMINANCE, "got more of the play near goal"),
        ("directness", lambda z: z >= 1.0, PatternTag.DIRECT_LONG_BALLS, "played more directly"),
        ("counter_proxy", lambda z: z >= 1.0, PatternTag.COUNTER_ATTACK, "created more from counters"),
        ("create_cross", lambda z: z >= 1.0, PatternTag.CROSSING_WIDE_OVERLOAD, "created more from crosses"),
        ("create_through_ball", lambda z: z >= 1.0, PatternTag.THROUGH_BALLS, "created more from through balls"),
        ("header_share", lambda z: z >= 1.0, PatternTag.AERIAL_THREAT, "took more headers"),
        ("shot_distance_mean", lambda z: z >= 1.0, PatternTag.LONG_RANGE_SHOOTING, "shot from further out"),
        ("shot_share_corner", lambda z: z >= 1.0, PatternTag.CORNER_THREAT, "got more shots from corners"),
        ("shot_share_set_piece", lambda z: z >= 1.0, PatternTag.SET_PIECE_THREAT, "got more shots from set pieces"),
        ("shot_share_direct_free_kick", lambda z: z >= 1.0, PatternTag.SET_PIECE_THREAT,
         "got more shots from free kicks"),
        ("create_rebound", lambda z: z >= 1.0, PatternTag.REBOUNDS_SECOND_BALLS, "scavenged more second balls"),
    ]


def offline_draft(d: dict[str, Any]) -> MatchReasoningDraft:
    """Data-only reasoning from a digest payload (rules, no model). Always passes ``verify``."""
    hdr, summ = d["header"], d["summary"]
    lab = {"H": hdr["home"], "A": hdr["away"]}
    side_word = {"H": "home", "A": "away"}
    hg, ag = (int(x) for x in summ["score"].split("-"))
    goals_by = {"H": hg, "A": ag}
    xg = summ.get("xg")
    diff = summ.get("xg_diff")
    shots = _table(d.get("shots"))
    events = _table(d.get("events"))
    segs = _table(d.get("segments"))
    timeline = d.get("xg_timeline")
    deltas = d.get("style_delta") or {}
    winner = "H" if hg > ag else "A" if ag > hg else None
    loser = None if winner is None else ("A" if winner == "H" else "H")
    margin = None if xg is None or winner is None else (diff if winner == "H" else -diff)

    # ---- verdict
    if winner is None:
        verdict = "even"
    elif margin is not None:
        verdict = ("dominant" if margin >= 0.8 else "deserved" if margin >= 0.25 else "even" if margin > -0.25
                   else "fortunate" if margin > -0.75 else "smash_and_grab")
    else:
        verdict = "dominant" if abs(hg - ag) >= 3 else "deserved"

    # ---- headline, why, how
    if winner is None:
        headline = f"{lab['H']} and {lab['A']} draw {hg}-{ag}"
    else:
        headline = f"{lab[winner]} beat {lab[loser]} {goals_by[winner]}-{goals_by[loser]}"
    n_shots = summ.get("shots")
    if winner is None:
        why = f"The match ended {hg}-{ag}."
        if xg is not None:
            why += f" Chance quality was xG {xg['H']:.2f} for {lab['H']} and {xg['A']:.2f} for {lab['A']}."
    elif margin is None:
        why = (f"{lab[winner]} won {goals_by[winner]}-{goals_by[loser]}. The digest has no chance-quality data, "
               "so this rests on the scoreline alone.")
    else:
        sense = "backed up" if margin > 0 else "was not backed up"
        why = (f"{lab[winner]} won {goals_by[winner]}-{goals_by[loser]} and the result {sense} by the chances: "
               f"xG {xg[winner]:.2f} to {xg[loser]:.2f}")
        why += f" from {n_shots[winner]} shots to {n_shots[loser]}." if n_shots else "."
    how: list[str] = []
    if "ht" in summ:
        how.append(f"Half-time score {summ['ht']}, full-time {summ['score']}.")
    by_sit = summ.get("by_situation")
    if by_sit:
        for sd in ("H", "A"):
            rows = by_sit.get(sd) or {}
            if rows:
                sit, (n, x) = max(rows.items(), key=lambda kv: (kv[1][1], kv[0]))
                how.append(f"{lab[sd]}'s largest source of chances was {sit.replace('_', ' ')}: "
                           f"{n} shots worth {x:.2f} xG.")
    if timeline:
        i = max(range(len(timeline["diff"])), key=lambda k: (abs(timeline["diff"][k]), -k))
        if timeline["diff"][i] != 0:
            lead = "H" if timeline["diff"][i] > 0 else "A"
            how.append(f"{lab[lead]} had the biggest xG edge in the {timeline['bins'][i]} minute bin "
                       f"({abs(timeline['diff'][i]):.2f}).")
    if not how:
        how.append(f"The digest holds little detail beyond the score {summ['score']}.")

    # ---- patterns
    cands: list[tuple[float, PatternTag, str, str, list[str]]] = []

    def add(strength: float, tag: PatternTag, side: str, text: str, ev: list[str]) -> None:
        cands.append((min(1.0, max(0.1, round(strength, 2))), tag, side, text, ev))

    for side in ("H", "A"):
        for key, pred, tag, phrase in _metric_rules():
            vals = (deltas.get(side) or {}).get(f"M:{key}")
            if not vals:
                continue
            m, b, z = vals
            if pred(z):
                add(abs(z) / 3.0, tag, side,
                    f"{lab[side]} {phrase}: {m:g} in this match against {b:g} in recent form (z {z:+.1f}).",
                    [f"M:{key}"])
        vals = (deltas.get(side) or {}).get("M:xg_per_shot")
        if vals and winner == side and vals[2] >= 1.0:
            add(vals[2] / 3.0, PatternTag.QUALITY_OVER_VOLUME, side,
                f"{lab[side]} won on shot quality: {vals[0]:g} xG per shot against {vals[1]:g} in recent form.",
                ["M:xg_per_shot"])
        if xg is not None:
            over = goals_by[side] - xg[side]
            if over >= 1.0:
                add(abs(over) / 2.5, PatternTag.FINISHING_OVER, side,
                    f"{lab[side]} scored {goals_by[side]} from {xg[side]:.2f} xG.", ["SUM"])
            elif over <= -1.0:
                add(abs(over) / 2.5, PatternTag.FINISHING_UNDER, side,
                    f"{lab[side]} scored {goals_by[side]} from {xg[side]:.2f} xG.", ["SUM"])
    for r in shots:
        if r["sit"] == "penalty" and r["res"] == "goal" and abs(hg - ag) <= 1:
            add(0.6, PatternTag.PENALTY_DECISIVE, r["side"],
                f"{lab[r['side']]} converted a penalty at {r['min']}' worth {r['xg']:.2f} xG in a one-goal-or-level match.",
                [r["id"]])
    for r in events:
        if r["kind"] in ("red", "second_yellow"):
            add(0.7, PatternTag.RED_CARD_IMPACT, r["side"],
                f"{lab[r['side']]} had a player sent off at {r['min']}'.", [r["id"]])
    if winner is not None:
        parse = [tuple(int(x) for x in s["score"].split("-")) for s in segs]
        for s, (sh, sa) in zip(segs, parse, strict=True):
            if (sh < sa) if winner == "H" else (sa < sh):
                add(0.6, PatternTag.COMEBACK_RESILIENCE, winner,
                    f"{lab[winner]} trailed {s['score']} from the {s['from']}' and still won "
                    f"{goals_by[winner]}-{goals_by[loser]}.", [s["id"]])
                break
        leading = [(s, p) for s, p in zip(segs, parse, strict=True)
                   if ((p[0] > p[1]) if winner == "H" else (p[1] > p[0])) and "xgH" in s and s["to"] - s["from"] >= 15]
        if leading:
            s, _ = max(leading, key=lambda sp: sp[0]["to"] - sp[0]["from"])
            own, opp = (s["xgH"], s["xgA"]) if winner == "H" else (s["xgA"], s["xgH"])
            if own + opp >= 0.4 and own / (own + opp) < 0.4:
                add(0.5, PatternTag.GAME_STATE_SIT_BACK, winner,
                    f"{lab[winner]} created {own:.2f} xG to {opp:.2f} while ahead ({s['score']}, "
                    f"{s['from']}' to {s['to']}').", [s["id"]])
    cands.sort(key=lambda c: (-c[0], c[1].value, c[2]))
    seen: set[tuple[PatternTag, str]] = set()
    patterns: list[TacticalPatternDraft] = []
    for strength, tag, side, text, ev in cands:
        if (tag, side) in seen or len(patterns) >= 4:
            continue
        seen.add((tag, side))
        patterns.append(TacticalPatternDraft(tag=tag, side=side_word[side], description=text, evidence=ev,
                                             strength=strength))

    # ---- turning points
    goals: list[tuple[int, str, str, float | None]] = []  # minute, side, ref, xg
    for r in shots:
        if r["res"] in ("goal", "own_goal"):
            goals.append((r["min"], r["side"], r["id"], r["xg"]))
    for r in events:
        if r["kind"] in ("goal", "penalty_goal", "own_goal"):
            goals.append((r["min"], r["side"], r["id"], None))
    goals.sort(key=lambda g: (g[0], g[2]))
    picks: list[tuple[int, str, str, float | None]] = []
    if goals:
        picks.append(goals[0])
        diff_now, decisive = 0, None
        for g in goals:
            before = diff_now
            diff_now += 1 if g[1] == "H" else -1
            if winner is None:
                if diff_now == 0 and before != 0:
                    decisive = g
            else:
                sign = 1 if winner == "H" else -1
                if sign * before <= 0 and sign * diff_now >= 1:
                    decisive = g
        if decisive is not None and decisive not in picks:
            picks.append(decisive)
    points: list[TurningPointDraft] = []
    for minute, side, ref, gxg in picks:
        text = f"{lab[side]} scored at {minute}'"
        if gxg is not None:
            text += f" from a {gxg:.2f} xG chance"
        seg = next((s for s in segs if s["from"] == minute), None)
        if seg is not None:
            text += f", making it {seg['score']}"
        swing = timeline["diff"][_bin_of(minute)] if timeline else None
        points.append(TurningPointDraft(minute=minute, description=text + ".", evidence=[ref], xg_swing=swing))
    for r in events:
        if r["kind"] in ("red", "second_yellow") and len(points) < 3:
            swing = timeline["diff"][_bin_of(r["min"])] if timeline else None
            points.append(TurningPointDraft(minute=r["min"], description=f"{lab[r['side']]} had a player sent off at "
                                            f"{r['min']}'.", evidence=[r["id"]], xg_swing=swing))
    points.sort(key=lambda p: p.minute)

    def note(sd: str) -> str:
        s = f"{lab[sd]} scored {goals_by[sd]}"
        if xg is not None:
            s += f" from {xg[sd]:.2f} xG"
            if n_shots:
                s += f" and {n_shots[sd]} shots"
        return s + "."

    return MatchReasoningDraft(
        verdict=verdict, headline=headline, why_won=why, how=how[:5], patterns=patterns, turning_points=points,
        home_note=note("H"), away_note=note("A"),
        confidence=0.6 if xg is not None and shots else 0.4 if xg is not None else 0.3,
        limitations=list(d.get("limitations", [])),
    )


# ===================================================================== verification


def _texts(d: MatchReasoningDraft) -> Iterator[tuple[str, str]]:
    yield "headline", d.headline
    yield "why_won", d.why_won
    for i, s in enumerate(d.how):
        yield f"how[{i}]", s
    for i, p in enumerate(d.patterns):
        yield f"patterns[{i}].description", p.description
    for i, t in enumerate(d.turning_points):
        yield f"turning_points[{i}].description", t.description
    yield "home_note", d.home_note
    yield "away_note", d.away_note
    for i, s in enumerate(d.limitations):
        yield f"limitations[{i}]", s


def _allowed_numbers(digest: Digest) -> tuple[set[float], list[float]]:
    exact = {abs(v) for v in numbers_in(digest.payload())} | {0.0}
    percent = [100.0 * v for v in exact if 0.0 < v <= 1.0]
    return exact, percent


# single digits only: "84-94" is a minute window and "17-6" a shot count, both left to the number check
_SCORE = re.compile(r"(?<![\d.\-])\d-\d(?![\d\-])")
_RANGE = re.compile(r"(?<![\w.])(\d{1,3})\s?[-–]\s?(\d{1,3})(?![\w.])")
_SCORE_STR = re.compile(r"\d{1,2}-\d{1,2}")


def _scorelines(value: Any) -> set[str]:
    """Every ``a-b`` score string that appears as a value in the digest payload."""
    if isinstance(value, str):
        # "2-0" is also fine written winner-first for a 0-2 result
        return {value, "-".join(reversed(value.split("-")))} if _SCORE_STR.fullmatch(value) else set()
    if isinstance(value, dict):
        return set().union(*(_scorelines(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_scorelines(v) for v in value)) if value else set()
    return set()


def _unaccounted(text: str, exact: set[float], percent: list[float]) -> list[str]:
    """Numbers in ``text`` that are neither digest values (to the precision written), percentages of
    them, nor an explicit minute."""
    clean = _EVIDENCE_IN_TEXT.sub(" ", _PLACEHOLDER.sub(" ", text))
    # a rising pair of minutes ("the 84-94 spell") is a time window
    windows = {i for r in _RANGE.finditer(clean) if int(r.group(1)) < int(r.group(2)) <= MAX_MINUTE
               for i in (r.start(1), r.start(2))}
    bad: list[str] = []
    for m in _NUMBER.finditer(clean):
        raw = m.group(0)
        if m.start() in windows:
            continue
        value = float(raw.replace(",", "."))
        decimals = len(raw.replace(",", ".").split(".")[1]) if "." in raw.replace(",", ".") else 0
        after, before = clean[m.end():m.end() + 12], clean[max(0, m.start() - 10):m.start()]
        if decimals == 0 and value <= MAX_MINUTE and (
                _MINUTE_AFTER.match(after) or _MINUTE_BEFORE.search(before) or _ORDINAL_HALF.match(after)):
            continue
        tol = 0.5 * 10 ** -decimals + 1e-9
        if decimals == 0:
            ok = any(abs(value - a) < 1e-6 for a in exact if abs(a - round(a)) < 1e-9)
        else:
            ok = any(abs(value - a) <= tol for a in exact)
        if not ok:
            ok = any(abs(value - p) <= tol for p in percent)
        if not ok:
            bad.append(raw)
    return bad


# ---- names in blind mode
# Blind mode exists so the model cannot recall the real match, so any name-like capitalised word that is not a
# placeholder, a digest word or ordinary football English is refused (and so is any known club/player/manager).

_CAP_WORD = re.compile(r"(?<![^\W\d_])[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:['’-][^\W\d_]+)*")
_SENT_END = re.compile(r"(?:^|[.!?:;\n—–(\[\"'“])\s*$")
_PHRASES = ("Premier League", "La Liga", "Serie A", "Champions League", "Europa League", "Ligue 1",
            "Man of the Match")
_MONTHS_DAYS = ("january february march april may june july august september october november december "
                "monday tuesday wednesday thursday friday saturday sunday")
_COMMON = frozenset((
    # articles, pronouns, conjunctions, prepositions, adverbs
    "a an the this that these those it its they them their he she his her we our you i and but or nor so yet for "
    "of in on at by to from with without within into onto over under after before during until since while "
    "between against through across about around above below behind beyond despite although though because "
    "however therefore thus hence also then still just only even both either neither each every all any some "
    "most more less much many few several other another such no not never always often once twice again "
    "here there when where what which who whom whose why how if else yet already instead overall meanwhile "
    "finally initially eventually later earlier first second third last next early late now soon "
    # verbs and auxiliaries commonly opening a clause
    "is are was were be been being has have had do does did can could will would shall should may might must "
    "scored scoring conceded won lost drew led trailed created took saw gave made needed kept got went "
    "came played looked proved remained stayed turned ended finished started opened settled sealed "
    # football nouns / adjectives / adverbs
    "goal goals shot shots chance chances xg xa ppda press pressing counter counters set piece pieces corner "
    "corners penalty penalties foul fouls card cards red yellow substitution substitutions sub subs half "
    "time halftime fulltime full extra stoppage minute minutes score scoreline result results win wins draw "
    "defeat victory loss margin edge lead advantage pressure territory possession shape formation formations "
    "defence defense defensive attack attacking attacks midfield midfielder forward striker winger "
    "keeper goalkeeper side sides team teams club clubs league match matches game games season home away "
    "host hosts visitors visitor opponent opponents period spell phase phases state states quality volume "
    "finishing finish finished efficient clinical wasteful dominant dominated dominance deserved fortunate "
    "even level balanced open tight low mid high deep wide direct patient quick slow long short big small "
    "strong weak good poor better worse best worst clear key main major minor single early late decisive "
    "evidence digest data limitation limitations note notes confidence verdict headline form form's "
    "recent pre-match prematch recent typical usual unusual normal average usually typically mostly largely "
    "mainly partly largely entirely clearly simply broadly slightly sharply heavily rarely "
    "one two three four five six seven eight nine ten eleven twelve own "
    "rest half-time full-time late-goal ").split())


def _name_vocab(digest: Digest) -> set[str]:
    """Lower-cased words of every string in the digest payload (situations, actions, limitations, ...)."""
    words: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            words.update(w.lower() for w in re.findall(r"[^\W\d_]+", _PLACEHOLDER.sub(" ", v)))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(str(k))
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(digest.payload())
    return words


def _parts(word: str) -> list[str]:
    word = re.sub(r"['’]s$", "", word)
    return [p.lower() for p in re.split(r"['’-]", word) if p]


_ENGLISH_SUFFIX = re.compile(r"(?:ed|ing|ly|ion|ions|ness|ment|ments|ive|ous|ful|ity|ally|able|ible|ence|ance|less)$")


# The English word list ships with the package (lower-case entries of the public-domain Webster's Second,
# the same list macOS keeps at /usr/share/dict/words). The check must not depend on what the host has installed:
# Linux CI images have no system list, so "Territorial" was a name there and an English word on a Mac.
WORD_LIST = Path(__file__).with_name("english_words.txt.gz")
_FOOTBALL_ENGLISH = frozenset("equaliser equalizer winner opener".split())


@functools.cache
def _english_words() -> frozenset[str]:
    """Lower-case entries of the bundled word list (proper nouns are not in it, so they stay out)."""
    with gzip.open(WORD_LIST, "rt", encoding="utf-8") as fh:
        return _FOOTBALL_ENGLISH | frozenset(fh.read().split())


def _plain(word: str, vocab: set[str], initial: bool, known: frozenset[str] = frozenset()) -> bool:
    """True when a capitalised word is ordinary English / football / digest vocabulary."""
    if len(word) >= 2 and word.isupper():
        return True  # VAR, PPDA, UCL, EPL
    parts = _parts(word)
    if not parts:
        return True
    ok = _COMMON | vocab | set(_MONTHS_DAYS.split())
    if any(p in known and p not in ok for p in parts):
        return False  # a surname / club word from the tables ("Sterling", "Villa")
    if initial:
        # a sentence or label opener ("Near-post corners: ...") passes when it is an English word
        english = _english_words()
        return all(p in ok or p in english or (len(p) >= 6 and _ENGLISH_SUFFIX.search(p)) for p in parts)
    # mid-sentence capital: only digest vocabulary and months/days (nationalities, cities, surnames are refused)
    return all(p in vocab or p in set(_MONTHS_DAYS.split()) for p in parts)


def name_problems(text: str, digest: Digest, known: Iterable[str] = ()) -> list[str]:
    """Name-like words in ``text`` that a blind-mode draft must not contain."""
    if not digest.blind:
        return []
    flagged: list[str] = []
    for real in [*digest.names.values(), *(k for k in known if len(k) >= 5)]:
        # the real names of this very match, and every known club / player / manager
        if real and re.search(rf"(?<!\w){re.escape(real)}(?!\w)", text, re.I):
            flagged.append(real)
    clean = text
    for phrase in _PHRASES:
        clean = clean.replace(phrase, " ")
    clean = _EVIDENCE_IN_TEXT.sub(" ", _PLACEHOLDER.sub(" ", clean))
    vocab = _name_vocab(digest)
    known_tok = frozenset(t.lower() for k in known for t in re.findall(r"[^\W\d_]{4,}", k))
    runs: list[list[tuple[str, bool]]] = []
    last_end = -10
    for m in _CAP_WORD.finditer(clean):
        before = clean[:m.start()]
        initial = bool(_SENT_END.search(before))
        gap = clean[last_end:m.start()] if last_end >= 0 else None
        if runs and gap is not None and gap.strip() == "" and "\n" not in gap and len(gap) == 1:
            runs[-1].append((m.group(0), initial))
        else:
            runs.append([(m.group(0), initial)])
        last_end = m.end()
    for run in runs:
        if len(run) == 1:
            word, initial = run[0]
            if not _plain(word, vocab, initial, known_tok):
                flagged.append(word)
            continue
        # a sentence-opening ordinary word ("The", "Despite") may sit in front of a name
        words = list(run)
        while words and words[0][1] and _plain(words[0][0], vocab, True, known_tok):
            words.pop(0)
        if words and not all(_plain(w, vocab, False, known_tok) for w, _ in words):
            flagged.extend(w for w, _ in words if not _plain(w, vocab, False, known_tok))
    return sorted(set(flagged), key=flagged.index)


def verify(draft: MatchReasoningDraft, digest: Digest, known_names: Iterable[str] = ()) -> list[str]:
    """Problems found in ``draft`` against ``digest`` (empty list: the draft is supported).

    ``known_names``: club, player and manager names from the tables; in blind mode none may appear.
    """
    problems: list[str] = []
    known = list(known_names)
    payload = digest.payload()
    exact, percent = _allowed_numbers(digest)
    scorelines = _scorelines(payload)
    ids = digest.evidence_ids()
    allowed_ph = digest.placeholders()
    shot_rows, event_rows, seg_rows = digest.shot_rows(), digest.event_rows(), digest.segment_rows()
    has_xg = "xg" in digest.summary

    for path, text in _texts(draft):
        for ph in _PLACEHOLDER.findall(text):
            if ph not in allowed_ph:
                problems.append(f"{path}: unknown placeholder {ph}; use only {{H}}, {{A}} and the {{P:..}}/{{M:..}} "
                                "placeholders that appear in the digest")
        names = name_problems(text, digest, known)
        if names:
            problems.append(f"{path}: name(s) {', '.join(names)} are not allowed in blind mode; refer to people and "
                            "clubs only by the {H}/{A}/{P:..}/{M:..} placeholders from the digest (if a flagged word "
                            "is ordinary English, write it in lower case or use another word)")
        bad = _unaccounted(text, exact, percent)
        wrong_scores = [x for x in _SCORE.findall(text) if x not in scorelines]
        if wrong_scores:
            problems.append(f"{path}: scoreline(s) {', '.join(wrong_scores)} do not appear in the digest")
        if bad:
            problems.append(f"{path}: number(s) {', '.join(bad)} not found in the digest "
                            "(copy figures exactly; minutes may be written as 64')")

    def check_ids(path: str, evidence: Sequence[str]) -> list[str]:
        if not evidence:
            problems.append(f"{path}: cites no evidence id")
        unknown = [e for e in evidence if e not in ids]
        if unknown:
            problems.append(f"{path}: unknown evidence id(s) {', '.join(unknown)}")
        return [e for e in evidence if e in ids]

    side_letter = {"home": "H", "away": "A"}
    for i, p in enumerate(draft.patterns):
        path = f"patterns[{i}]"
        valid = check_ids(path, p.evidence)
        sd = side_letter[p.side]
        if p.tag in NEEDS_XG and not has_xg:
            problems.append(f"{path}: tag {p.tag.value} needs xG, which this digest does not have")
        for e in valid:
            if e in shot_rows and p.tag in ATTACK_TAGS and shot_rows[e]["side"] != sd:
                problems.append(f"{path}: {p.tag.value} is attributed to {p.side} but {e} is a shot by the other side")
            if e.startswith("M:") and e not in digest.metric_keys(sd) and e in digest.metric_keys("A" if sd == "H" else "H"):
                problems.append(f"{path}: {e} describes the other side, not {p.side}")

    bins = (digest.xg_timeline or {}).get("bins", [])
    diffs = (digest.xg_timeline or {}).get("diff", [])
    for i, t in enumerate(draft.turning_points):
        path = f"turning_points[{i}]"
        valid = check_ids(path, t.evidence)
        if not 0 <= t.minute <= MAX_MINUTE:
            problems.append(f"{path}: minute {t.minute} is outside the match")
        minutes: list[int] = []
        for e in valid:
            if e in shot_rows:
                minutes.append(shot_rows[e]["min"])
            elif e in event_rows:
                minutes.append(event_rows[e]["min"])
            elif e in seg_rows:
                minutes.append(seg_rows[e]["from"])
            elif re.fullmatch(r"T\d+", e):
                lo, hi = (int(x) for x in bins[int(e[1:]) - 1].split("-"))
                if not lo - 1 <= t.minute <= hi + (30 if int(e[1:]) == len(bins) else 1):
                    problems.append(f"{path}: minute {t.minute} is not inside {e} ({bins[int(e[1:]) - 1]})")
        if minutes and min(abs(t.minute - mn) for mn in minutes) > 3:
            problems.append(f"{path}: minute {t.minute} does not match the minute of the evidence it cites "
                            f"({', '.join(str(x) for x in sorted(set(minutes)))})")
        if t.xg_swing is not None:
            if not diffs:
                problems.append(f"{path}: xg_swing given but the digest has no xG timeline; use null")
            elif not any(abs(abs(t.xg_swing) - abs(v)) <= 0.0101 for v in diffs):
                problems.append(f"{path}: xg_swing {t.xg_swing} is not a value of xg_timeline.diff")

    home, away = (int(x) for x in digest.score.split("-"))
    if has_xg and home != away:
        winner = "H" if home > away else "A"
        margin = digest.summary["xg_diff"] * (1 if winner == "H" else -1)
        if draft.verdict in ("dominant", "deserved") and margin < -0.3:
            problems.append(f"verdict {draft.verdict} contradicts the xG: the winner created {margin:+.2f} xG "
                            "relative to the loser")
        if draft.verdict in ("fortunate", "smash_and_grab") and margin >= 0.8:
            problems.append(f"verdict {draft.verdict} contradicts the xG: the winner led the xG by {margin:.2f}")
    return problems


# ===================================================================== names


def restore_names(draft: MatchReasoningDraft, names: dict[str, str]) -> MatchReasoningDraft:
    """Replace blind placeholders (``{H}``, ``{P:p3f2a1}``, ...) with the real names."""
    if not names:
        return draft
    keys = sorted(names, key=len, reverse=True)

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            for key in keys:
                value = value.replace(key, names[key])
            return value
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value

    return MatchReasoningDraft.model_validate(fix(draft.model_dump(mode="json")))


# ===================================================================== one match


def _model_label(agent: Agent) -> str:
    client = getattr(agent, "client", None) or getattr(agent, "chat_client", None)
    return str(getattr(client, "model", None) or getattr(client, "model_id", None) or type(client).__name__)


async def _call(agent: Agent, payload: dict[str, Any], retries: int) -> tuple[MatchReasoningDraft | None, str | None]:
    err: str | None = None
    for attempt in range(retries + 1):
        value, err = await call_agent(agent, payload, MatchReasoningDraft, timeout=REASON_TIMEOUT_S)
        if value is not None:
            return value, None
        if attempt < retries:
            await asyncio.sleep(BACKOFF_S * 2 ** attempt)
    return None, err


def _provenance(model: str, digest: Digest, status: str, problems: list[str]) -> Provenance:
    return Provenance(model=model, prompt_version=PROMPT_VERSION, digest_hash=digest.digest_hash,
                      fact_check=status, problems=problems[:12],  # type: ignore[arg-type]
                      created_at=datetime.now(UTC).isoformat(timespec="seconds"))


async def reason_match(agent: Agent, digest: Digest, *, model: str | None = None, retries: int = 2,
                       known_names: Iterable[str] = ()) -> MatchReasoning:
    """Ask the model, verify, allow one rewrite turn, fall back to the data-only template.

    ``fact_check``: passed (first draft verified), rewritten (second draft verified; ``problems`` lists what
    was fixed), blocked (still failing: the template is stored, ``problems`` lists why) or template (the model
    never answered: the template is stored, ``problems`` holds the error; batch runs retry these).
    """
    model = model or _model_label(agent)
    known = list(known_names)
    payload: dict[str, Any] = {"task": TASK, "digest": digest.payload()}
    template = offline_draft(digest.payload())
    status, problems = "passed", []
    draft, err = await _call(agent, payload, retries)
    if draft is None:
        draft, status, problems = template, "template", [err or "no answer"]
    else:
        problems = verify(draft, digest, known)
        if problems:
            retry = {**payload, "previous": draft.model_dump(mode="json"), "problems": problems}
            second, err = await _call(agent, retry, retries)
            if second is not None and not (remaining := verify(second, digest, known)):
                draft, status = second, "rewritten"
            else:
                draft, status = template, "blocked"
                problems = remaining if second is not None else [*problems, err or "no answer to the rewrite turn"]
    draft = draft.model_copy(update={"headline": strip_citations(draft.headline)})
    return MatchReasoning(
        match_key=digest.match_key, league=League(digest.league), season=digest.season,
        home_id=digest.home_id, away_id=digest.away_id, score=digest.score,
        draft=restore_names(draft, digest.names), provenance=_provenance(model, digest, status, problems),
    )


# ===================================================================== batch


def safe_key(match_key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", match_key)


def reasoning_path(cfg: Config, league: str, season: str, match_key: str) -> Path:
    return cfg.reasoning_dir / league / season / f"{safe_key(match_key)}.json"


def manifest_path(cfg: Config) -> Path:
    return cfg.reasoning_dir / "manifest.jsonl"


def is_current(path: Path, digest_hash: str, model: str) -> bool:
    """True when ``path`` holds a finished reasoning of exactly this evidence, prompt and model."""
    try:
        prov = json.loads(path.read_text(encoding="utf-8"))["provenance"]
    except (OSError, ValueError, KeyError):
        return False
    return (prov.get("digest_hash") == digest_hash and prov.get("prompt_version") == PROMPT_VERSION
            and prov.get("model") == model and prov.get("fact_check") in DONE_STATUSES)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _strict_schema() -> dict[str, Any]:
    """JSON schema of the draft in OpenAI strict mode (no numeric bounds, no titles)."""
    schema = MatchReasoningDraft.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("minimum", "maximum", "title", "default"):
                node.pop(key, None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


def batch_request(digest: Digest, deployment: str, effort: str = "high") -> dict[str, Any]:
    """One line of an Azure OpenAI Batch input file (``/chat/completions``, structured output)."""
    user = json.dumps({"task": TASK, "digest": digest.payload()}, separators=(",", ":"), ensure_ascii=False)
    return {
        "custom_id": digest.match_key, "method": "POST", "url": "/chat/completions",
        "body": {
            "model": deployment,
            "messages": [{"role": "system", "content": prompts.LEAGUE_REASONER},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "MatchReasoningDraft", "strict": True, "schema": _strict_schema()}},
            "reasoning_effort": effort,
        },
    }


@dataclass
class BatchReport:
    counts: Counter[str] = field(default_factory=Counter)  # passed/rewritten/blocked/template/skipped/emitted/error
    tokens: int = 0  # digest tokens sent (or emitted)
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return self.counts["template"] + self.counts["error"]


async def run_batch(
    cfg: Config, match_keys: Sequence[str], *, concurrency: int = DEFAULT_CONCURRENCY, model_label: str,
    limit: int | None = None, tables: Tables | None = None, agent: Agent | None = None, blind: bool = True,
    emit_batch: Path | None = None, deployment: str | None = None, effort: str = "high", force: bool = False,
    retries: int = 2, progress_every: int = 50,
) -> BatchReport:
    """Reason about ``match_keys`` (first ``limit``) and store ``MatchReasoning`` JSON files.

    Skips matches whose stored file has the same digest hash, prompt version and ``model_label``
    (``force`` redoes them). With ``emit_batch`` no model is called: the requests are written as Azure OpenAI
    Batch JSONL (``deployment`` is the model deployment name) and nothing is stored.
    """
    tables = tables or Tables.load(cfg)
    builder = tables.style_builder()
    known = tables.known_names()
    keys = list(match_keys)[:limit] if limit else list(match_keys)
    report = BatchReport()
    started = time.monotonic()

    def record_failure(key: str, status: str, error: str) -> None:
        report.failures.append(f"{key}: {error}")
        manifest_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        with manifest_path(cfg).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"match_key": key, "status": status, "error": error, "model": model_label,
                                "prompt_version": PROMPT_VERSION, "at": datetime.now(UTC).isoformat(timespec="seconds")},
                               ensure_ascii=False) + "\n")

    def digest_for(key: str) -> Digest | None:
        try:
            return build_digest(key, tables, builder, blind=blind)
        except Exception as exc:  # noqa: BLE001 - one bad match must not stop the run
            log.warning("digest failed for %s: %s", key, exc)
            record_failure(key, "error", f"digest: {type(exc).__name__}: {exc}")
            report.counts["error"] += 1
            return None

    if emit_batch is not None:
        emit_batch.parent.mkdir(parents=True, exist_ok=True)
        with emit_batch.open("w", encoding="utf-8") as out:
            for key in keys:
                digest = digest_for(key)
                if digest is None:
                    continue
                path = reasoning_path(cfg, digest.league, digest.season, key)
                if not force and is_current(path, digest.digest_hash, model_label):
                    report.counts["skipped"] += 1
                    continue
                out.write(json.dumps(batch_request(digest, deployment or model_label, effort),
                                     separators=(",", ":"), ensure_ascii=False) + "\n")
                report.counts["emitted"] += 1
                report.tokens += estimate_tokens(digest)
        report.seconds = time.monotonic() - started
        return report

    if agent is None:
        raise ValueError("run_batch needs an agent unless emit_batch is set")
    sem = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def one(key: str) -> None:
        nonlocal done
        async with sem:
            digest = digest_for(key)
            if digest is None:
                return
            path = reasoning_path(cfg, digest.league, digest.season, key)
            if not force and is_current(path, digest.digest_hash, model_label):
                report.counts["skipped"] += 1
                return
            try:
                result = await reason_match(agent, digest, model=model_label, retries=retries, known_names=known)
            except Exception as exc:  # noqa: BLE001
                log.warning("reasoning failed for %s: %s", key, exc)
                record_failure(key, "error", f"{type(exc).__name__}: {exc}")
                report.counts["error"] += 1
                return
            report.tokens += estimate_tokens(digest)
            status = result.provenance.fact_check
            report.counts[status] += 1
            if status == "template":
                record_failure(key, status, "; ".join(result.provenance.problems))
            else:
                if status == "blocked":
                    record_failure(key, status, "; ".join(result.provenance.problems))
                _atomic_write(path, result.model_dump_json(indent=2))
        done += 1
        if progress_every and done % progress_every == 0:
            log.info("reasoned %d/%d matches (%s)", done, len(keys), dict(report.counts))

    await asyncio.gather(*(one(k) for k in keys))
    report.seconds = time.monotonic() - started
    return report


# ===================================================================== CLI


def build_agent() -> tuple[Agent, str, str]:
    """(agent, model label, deployment) for the strong model; ``MATCHLENS_LEAGUE_MODEL`` overrides the deployment."""
    from matchlens.agents.llm import LlmSettings, build_clients

    settings = LlmSettings.from_env()
    override = os.getenv("MATCHLENS_LEAGUE_MODEL")
    if override:
        settings = dataclasses.replace(settings, strong_model=override)
    clients = build_clients(settings)
    agent = Agent(clients.strong, prompts.LEAGUE_REASONER, name="league_reasoner",
                  default_options=clients.options(strong=True) or None)
    label = (f"azure:{settings.strong_model}({settings.strong_reasoning})" if settings.online
             else "offline-templates")
    return agent, label, settings.strong_model


def _csv(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [getattr(v, "value", v) for v in value]


def main(cfg: Config, args: Any) -> int:
    """``python -m matchlens.leaguedata reason`` (options: --leagues --seasons --limit; optional
    ``emit_batch``, ``force``, ``concurrency`` attributes or the MATCHLENS_REASON_* variables)."""
    tables = Tables.load(cfg)
    leagues, seasons = _csv(getattr(args, "leagues", None)), _csv(getattr(args, "seasons", None))
    limit = getattr(args, "limit", None)
    keys = tables.match_keys(leagues, seasons, limit)
    if not keys:
        print("no matches selected: run 'build' (and 'curate') first, or widen --leagues/--seasons")
        return 1
    emit = getattr(args, "emit_batch", None) or os.getenv("MATCHLENS_REASON_EMIT_BATCH")
    concurrency = int(getattr(args, "concurrency", None) or os.getenv("MATCHLENS_REASON_CONCURRENCY", DEFAULT_CONCURRENCY))
    force = bool(getattr(args, "force", False))
    agent, label, deployment = build_agent()
    emit_path = None if not emit else (cfg.reasoning_dir / "batch.jsonl" if emit is True else Path(emit))
    report = asyncio.run(run_batch(
        cfg, keys, concurrency=concurrency, model_label=label, tables=tables, agent=agent,
        emit_batch=emit_path, deployment=deployment, force=force))
    print(f"{len(keys)} matches ({tables.source} tables), model {label}")
    for name, n in sorted(report.counts.items()):
        print(f"  {name:<8}{n:>6}")
    if report.counts and report.tokens:
        print(f"  digest tokens sent: {report.tokens} (avg {report.tokens // max(1, sum(report.counts[k] for k in ('passed', 'rewritten', 'blocked', 'template', 'emitted')))} per match)")
    print(f"  {report.seconds:.0f}s")
    if emit_path:
        print(f"batch input written to {emit_path}")
    if report.failures:
        print(f"{len(report.failures)} problem(s), see {manifest_path(cfg)}")
    return 1 if report.failed else 0


def iter_reasonings(cfg: Config, leagues: Iterable[str] | None = None,
                    seasons: Iterable[str] | None = None) -> Iterator[MatchReasoning]:
    """Stored reasonings (for playbooks and the knowledge base)."""
    want_l, want_s = set(leagues or ()), set(seasons or ())
    root = cfg.reasoning_dir
    if not root.is_dir():
        return
    for league_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if want_l and league_dir.name not in want_l:
            continue
        for season_dir in sorted(p for p in league_dir.iterdir() if p.is_dir()):
            if want_s and season_dir.name not in want_s:
                continue
            for path in sorted(season_dir.glob("*.json")):
                yield MatchReasoning.model_validate_json(path.read_text(encoding="utf-8"))
