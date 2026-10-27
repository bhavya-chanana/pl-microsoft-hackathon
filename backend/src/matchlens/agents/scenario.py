"""Scenario agent: a fan's match described in words becomes a playable storyline.

The strong model writes a draft against the roster; ``check_storyline`` lists
anything the simulator could not play, and the model gets one turn to fix it.
A draft that still has problems is refused rather than played wrong, and the
refusal says why: the agent was too slow, did not answer, or could not fix it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError

from matchlens.agents.contracts import _Strict
from matchlens.domain.models import MatchMeta
from matchlens.simulator.scenarios import PRESETS, check_storyline
from matchlens.simulator.storyline import Beat, Phase, Storyline

SCENARIO_TIMEOUT_S = 45.0
MAX_PROMPT = 400

Failure = Literal["timeout", "no_answer", "unplayable"]


class PhaseDraft(_Strict):
    start_minute: int
    end_minute: int
    home_dominance: float = Field(description="-1 (away on top) to +1 (home on top)")
    label: str


class BeatDraft(_Strict):
    minute: int = Field(description="1-45 first half, 46-95 second half")
    type: Literal["goal", "big_chance", "yellow_card", "red_card", "substitution"]
    team_id: str
    player_id: str
    related_player_id: str | None


class ScenarioDraft(_Strict):
    title: str
    summary: str
    phases: list[PhaseDraft]
    beats: list[BeatDraft]


def roster(meta: MatchMeta) -> dict[str, Any]:
    def side(team) -> dict[str, Any]:
        def row(pid: str, starting: bool) -> dict[str, Any]:
            p = meta.players[pid]
            return {"player_id": pid, "name": p.name, "position": p.position.value, "role": p.role,
                    "starting": starting}
        return {"team_id": team.team_id, "name": team.name, "side": team.side.value,
                "players": [row(pid, True) for pid in team.starting_xi] + [row(pid, False) for pid in team.bench]}
    return {"home": side(meta.home), "away": side(meta.away)}


def to_storyline(draft: ScenarioDraft) -> tuple[Storyline | None, list[str]]:
    try:
        story = Storyline(
            title=draft.title[:60], summary=draft.summary[:240],
            phases=[Phase(start_minute=max(0, p.start_minute), end_minute=min(96, p.end_minute),
                          home_dominance=max(-1.0, min(1.0, p.home_dominance)), label=p.label[:40])
                    for p in draft.phases],
            beats=[Beat(minute=b.minute, type=b.type, team_id=b.team_id, player_id=b.player_id,
                        related_player_id=b.related_player_id) for b in draft.beats],
        )
    except ValidationError as exc:
        return None, [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()][:8]
    return story, []


@dataclass(frozen=True)
class Written:
    """A checked storyline, or why there is none. ``problems`` are the last draft's, if there was one."""

    story: Storyline | None
    problems: list[str]
    failure: Failure | None = None


async def write_storyline(team: Any, meta: MatchMeta, prompt: str, language: str) -> Written:
    """Draft a storyline and check it, with one turn to fix what the simulator could not play."""
    from matchlens.agents.pipeline import call_agent, timed_out

    payload: dict[str, Any] = {"task": "scenario", "prompt": prompt.strip()[:MAX_PROMPT], "language": language,
                               "roster": roster(meta)}
    problems: list[str] = []
    for _ in range(2):
        if problems:
            payload["problems"] = problems
        draft, err = await call_agent(team.scenario, payload, ScenarioDraft, timeout=SCENARIO_TIMEOUT_S)
        if draft is None:
            return Written(None, problems, "timeout" if timed_out(err) else "no_answer")
        story, problems = to_storyline(draft)
        if story is not None:
            problems = check_storyline(meta, story)
            if not problems:
                return Written(story, [])
    return Written(None, problems, "unplayable")


# Words that point at a preset beyond what its own title and summary say.
PRESET_HINTS = {
    "comeback": "comeback behind recover turnaround fightback",
    "ten-man-heist": "ten men heist counter red card sent defend",
    "late-late-show": "late stoppage injury winner dramatic",
    "goal-fest": "goals thriller seven wild chaos open",
}
NO_MATCH_NOTE = "Nothing in the description matched a preset, so this is the default one. "
OFFLINE_NOTE = "Picked the closest preset; writing a new match needs the live model."


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def closest_preset(prompt: str) -> tuple[str, bool]:
    """Offline stand-in for the Scenario agent: the preset whose title, summary and hints share the most
    words with the prompt. No shared word falls back to the first preset (second value False)."""
    best, best_score = next(iter(PRESETS)), 0
    wanted = _words(prompt)
    for pid, (factory, _) in PRESETS.items():
        story = factory()
        score = len(wanted & _words(f"{story.title} {story.summary} {PRESET_HINTS.get(pid, '')} {pid}"))
        if score > best_score:
            best, best_score = pid, score
    return best, best_score > 0
