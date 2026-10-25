"""Fan polls and fan player ratings (BRD §6.2, now in scope).

Polls open from the match itself, so every replay of a run produces the same
polls and a resumed run can re-attach its stored votes:

    result   "Who wins from here?" at kick-off, after every goal and at half-time;
             each closes when the next opens and all are resolved at full-time
    motm     best player of the first half (at half-time, open until ten minutes into
             the second half) and player of the match (at full-time), with the four
             best-rated players by the data as options

Fans also rate any player who has played, 1 to 10, next to the data rating
(interpret/ratings.py). Voters are anonymous: the browser keeps a random id and
only a salted hash of it is stored, one vote per voter per poll or player
(voting again replaces the vote). Question text is localised here because it
is match content.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from matchlens.domain.models import Event, EventType, MatchMeta
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.ratings import Rating, data_rating, has_played, match_card

VOTER_PATTERN = r"^[A-Za-z0-9-]{16,64}$"
_VOTER = re.compile(VOTER_PATTERN)
MOTM_OPTIONS = 4
MOTM_HT_OPEN_S = 600.0  # the half-time poll stays open ten match minutes into the second half
LANGUAGES = ("en", "hi", "es")

QUESTIONS = {
    "result": {"en": "Who wins from here?", "hi": "अब यहाँ से कौन जीतेगा?", "es": "¿Quién gana desde aquí?"},
    "motm_ht": {"en": "Best player of the first half?", "hi": "पहले हाफ़ का सबसे अच्छा खिलाड़ी?",
                "es": "¿Mejor jugador de la primera parte?"},
    "motm_ft": {"en": "Player of the match?", "hi": "प्लेयर ऑफ़ द मैच?", "es": "¿Jugador del partido?"},
}
CONTEXT = {
    "kickoff": {"en": "Before kick-off", "hi": "किक-ऑफ़ से पहले", "es": "Antes del saque inicial"},
    "goal": {"en": "After the goal", "hi": "गोल के बाद", "es": "Tras el gol"},
    "half_time": {"en": "At half-time", "hi": "हाफ़-टाइम पर", "es": "En el descanso"},
    "full_time": {"en": "At full-time", "hi": "फ़ुल-टाइम पर", "es": "Al final del partido"},
}
DRAW = {"en": "Draw", "hi": "ड्रॉ", "es": "Empate"}

Sink = Callable[[str, dict[str, Any]], None]


def _same(text: str) -> dict[str, str]:
    return dict.fromkeys(LANGUAGES, text)


def _context(key: str) -> dict[str, str]:
    return dict(CONTEXT[key])


@dataclass
class Poll:
    poll_id: str
    kind: str  # "result" or "motm"
    question: dict[str, str]
    context: dict[str, str]
    options: list[tuple[str, dict[str, str]]]  # (option_id, label by language)
    minute_label: str
    opened_elapsed: float
    closes_elapsed: float | None = None
    closed: bool = False
    # Resolved at full-time: the final result, or the data's pick for player polls.
    answer: str | None = None
    votes: dict[str, str] = field(default_factory=dict)  # voter hash -> option id

    def public(self) -> dict[str, Any]:
        counts = {oid: 0 for oid, _ in self.options}
        for choice in self.votes.values():
            counts[choice] = counts.get(choice, 0) + 1
        return {
            "poll_id": self.poll_id, "kind": self.kind, "question": self.question, "context": self.context,
            "minute_label": self.minute_label, "closed": self.closed, "answer": self.answer,
            "total_votes": len(self.votes),
            "options": [{"option_id": oid, "label": label, "votes": counts[oid]} for oid, label in self.options],
        }


class FanZone:
    def __init__(self, meta: MatchMeta, salt: str, sink: Sink | None = None) -> None:
        self.meta = meta
        self._salt = salt
        self._sink = sink
        self.polls: dict[str, Poll] = {}
        self.ratings_votes: dict[str, dict[str, int]] = {}  # player id -> voter hash -> score
        self._results = 0

    # ------------------------------------------------------------ match flow

    def observe(self, event: Event, engine: MatchEngine) -> list[Poll]:
        """Open, close and resolve polls as the match unfolds. Returns the polls that changed."""
        changed = [p for p in self.polls.values()
                   if not p.closed and p.closes_elapsed is not None and event.elapsed >= p.closes_elapsed]
        for p in changed:
            p.closed = True
        label = engine.state()["minute_label"]
        if event.type == EventType.KICKOFF:
            # Play also restarts with a kick-off after every goal; only the match's first opens a poll.
            if not self.polls:
                changed += self._open_result(event, label, _context("kickoff"))
            elif event.period == 2 and (ht := self.polls.get("motm-ht")) and ht.closes_elapsed is None:
                ht.closes_elapsed = event.elapsed + MOTM_HT_OPEN_S
        elif event.type == EventType.GOAL:
            changed += self._open_result(event, label, _context("goal"))
        elif event.type == EventType.HALF_TIME:
            changed += self._open_result(event, label, _context("half_time"))
            changed += self._open_motm("motm-ht", "motm_ht", event, label, _context("half_time"), engine)
        elif event.type == EventType.FULL_TIME:
            changed += self._full_time(event, label, engine)
        return list({p.poll_id: p for p in changed}.values())

    def _close_open_results(self) -> list[Poll]:
        open_ = [p for p in self.polls.values() if p.kind == "result" and not p.closed]
        for p in open_:
            p.closed = True
        return open_

    def _open_result(self, event: Event, label: str, context: dict[str, str]) -> list[Poll]:
        changed = self._close_open_results()
        home, away = self.meta.home, self.meta.away
        poll = Poll(f"result-{self._results}", "result", QUESTIONS["result"], context,
                    [(home.team_id, _same(home.name)), ("draw", DRAW), (away.team_id, _same(away.name))],
                    label, event.elapsed)
        self._results += 1
        self.polls[poll.poll_id] = poll
        return [*changed, poll]

    def _top_players(self, engine: MatchEngine) -> list[str]:
        rows = self.ratings(engine)
        return [r["player_id"] for r in sorted(rows, key=lambda r: (-r["data_rating"], r["player_id"]))]

    def _open_motm(self, poll_id: str, question: str, event: Event, label: str, context: dict[str, str],
                   engine: MatchEngine) -> list[Poll]:
        top = self._top_players(engine)[:MOTM_OPTIONS]
        if not top:
            return []
        options = [(pid, _same(self.meta.players[pid].name)) for pid in top]
        poll = Poll(poll_id, "motm", QUESTIONS[question], context, options, label, event.elapsed)
        self.polls[poll_id] = poll
        return [poll]

    def _full_time(self, event: Event, label: str, engine: MatchEngine) -> list[Poll]:
        changed = [p for p in self.polls.values() if not p.closed]
        for p in changed:
            p.closed = True
        score = engine.score()
        home, away = score[self.meta.home.team_id], score[self.meta.away.team_id]
        result = self.meta.home.team_id if home > away else self.meta.away.team_id if away > home else "draw"
        top = self._top_players(engine)
        for p in self.polls.values():
            if p.kind == "result":
                p.answer = result
            elif options := [oid for oid, _ in p.options]:
                # The data's pick among the options, judged on the whole match.
                p.answer = min(options, key=lambda oid: top.index(oid) if oid in top else len(top))
            changed.append(p)
        changed += self._open_motm("motm-ft", "motm_ft", event, label, _context("full_time"), engine)
        if ft := self.polls.get("motm-ft"):
            ft.answer = ft.options[0][0]
        return changed

    # ------------------------------------------------------------ votes

    def _voter(self, voter_id: str) -> str:
        if not _VOTER.match(voter_id):
            raise ValueError("invalid voter id")
        return hashlib.sha256(f"{self._salt}:{voter_id}".encode()).hexdigest()[:16]

    def vote(self, poll_id: str, voter_id: str, option_id: str) -> Poll:
        poll = self.polls.get(poll_id)
        if poll is None:
            raise LookupError("unknown poll")
        if poll.closed:
            raise PermissionError("this poll has closed")
        if option_id not in {oid for oid, _ in poll.options}:
            raise ValueError("unknown option")
        voter = self._voter(voter_id)
        poll.votes[voter] = option_id
        if self._sink:
            self._sink(f"poll:{poll_id}:{voter}", {"kind": "poll", "poll_id": poll_id, "voter": voter,
                                                     "choice": option_id})
        return poll

    def rate(self, player_id: str, voter_id: str, score: int, engine: MatchEngine) -> dict[str, Any]:
        if player_id not in self.meta.players:
            raise LookupError("unknown player")
        if not has_played(engine.player_stats(player_id)):
            raise PermissionError("this player has not played yet")
        if not 1 <= score <= 10:
            raise ValueError("score must be 1 to 10")
        voter = self._voter(voter_id)
        self.ratings_votes.setdefault(player_id, {})[voter] = score
        if self._sink:
            self._sink(f"rating:{player_id}:{voter}", {"kind": "rating", "player_id": player_id, "voter": voter,
                                                       "score": score})
        return self.rating_row(player_id, engine)

    def load(self, rows: list[dict[str, Any]]) -> None:
        """Re-attach stored votes after the polls were rebuilt from the stored events."""
        for row in rows:
            if row.get("kind") == "poll" and (poll := self.polls.get(row["poll_id"])):
                poll.votes[row["voter"]] = row["choice"]
            elif row.get("kind") == "rating":
                self.ratings_votes.setdefault(row["player_id"], {})[row["voter"]] = int(row["score"])

    # ------------------------------------------------------------ views

    def public_polls(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.polls.values()]

    def fan_tally(self, player_id: str) -> tuple[float | None, int]:
        scores = list(self.ratings_votes.get(player_id, {}).values())
        return (round(sum(scores) / len(scores), 1) if scores else None), len(scores)

    def rating_tallies(self) -> dict[str, dict[str, Any]]:
        out = {}
        for pid in self.ratings_votes:
            avg, n = self.fan_tally(pid)
            out[pid] = {"fan_rating": avg, "fan_votes": n}
        return out

    def _rate(self, player_id: str, engine: MatchEngine) -> tuple[dict[str, Any], Rating, int]:
        stats = engine.player_stats(player_id)
        team = str(stats["team_id"])
        other = self.meta.away.team_id if team == self.meta.home.team_id else self.meta.home.team_id
        conceded = engine.score().get(other, 0)
        finished = engine.state()["status"] == "full_time"
        return stats, data_rating(stats, conceded=conceded, finished=finished), conceded

    def card(self, player_id: str, engine: MatchEngine) -> dict[str, Any]:
        """The player's FIFA-style match card (interpret/ratings.py)."""
        stats, rating, conceded = self._rate(player_id, engine)
        return match_card(stats, rating, conceded=conceded)

    def rating_row(self, player_id: str, engine: MatchEngine) -> dict[str, Any]:
        stats, rating, conceded = self._rate(player_id, engine)
        avg, n = self.fan_tally(player_id)
        return {**rating.as_dict(), "team_id": stats["team_id"], "name": stats["name"],
                "short_name": stats["short_name"], "position": stats["position"], "on_pitch": stats["on_pitch"],
                "overall": match_card(stats, rating, conceded=conceded)["overall"], "fan_rating": avg,
                "fan_votes": n, "gap": round(avg - rating.value, 1) if avg is not None else None}

    def ratings(self, engine: MatchEngine) -> list[dict[str, Any]]:
        """Every player who has played, by team and then by data rating."""
        order = {self.meta.home.team_id: 0, self.meta.away.team_id: 1}
        rows = [self.rating_row(pid, engine) for pid in self.meta.players if has_played(engine.player_stats(pid))]
        return sorted(rows, key=lambda r: (order.get(r["team_id"], 2), -r["data_rating"], r["player_id"]))
