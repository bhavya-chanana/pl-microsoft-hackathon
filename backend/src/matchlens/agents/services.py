"""Fan-facing services built on the agent team (FR-035 to FR-041).

* catch-up ("What did I miss?") for the last 5, 15 or 30 minutes
* recap at half-time, full-time or on demand
* personalised insight feed for a fan profile

Results are cached per (window, profile, match position) so many fans asking
the same question cost one model call. Every result is fact-checked, and the
data-only template is used if the agent fails or is blocked.
"""

from __future__ import annotations

from typing import Any

from matchlens.agents import templates
from matchlens.agents.context import window_pack
from matchlens.agents.contracts import (
    Catchup,
    CatchupDraft,
    FactCheckReport,
    FactCheckStatus,
    FanProfile,
    Mode,
    Recap,
    RecapDraft,
)
from matchlens.agents.factchecker import check_text
from matchlens.agents.pipeline import AgentTeam, call_agent
from matchlens.runtime.session import MatchSession

CATCHUP_WINDOWS = (5, 15, 30)
RECAP_TIMEOUT_S = 20.0


def _profile_context(session: MatchSession, profile: FanProfile) -> dict[str, Any]:
    meta = session.meta
    out: dict[str, Any] = {"mode": profile.mode.value}
    if profile.club_id in (meta.home.team_id, meta.away.team_id):
        out["favourite_club"] = {"team_id": profile.club_id, "name": meta.team(profile.club_id).name}
    if profile.player_id in meta.players:
        p = meta.players[profile.player_id]
        out["favourite_player"] = {"player_id": p.player_id, "name": p.name,
                                   "stats": session.engine.player_stats(p.player_id)}
    if profile.metric:
        out["headline_metric"] = profile.metric
    return out


class FanServices:
    def __init__(self, team: AgentTeam) -> None:
        self.team = team
        self.generated_by = "data" if team.label == "offline-templates" else "agent"
        self._cache: dict[tuple[Any, ...], Catchup | Recap] = {}

    def reset(self) -> None:
        self._cache.clear()

    def _key(self, session: MatchSession, *parts: Any) -> tuple[Any, ...]:
        # Coarse match position: answers stay valid within the same minute and signal count.
        return (session.meta.match_id, int(session.engine.c.elapsed // 60), len(session.engine.signals()), *parts)

    async def catchup(self, session: MatchSession, minutes: int, profile: FanProfile) -> Catchup:
        if minutes not in CATCHUP_WINDOWS:
            raise ValueError(f"window must be one of {CATCHUP_WINDOWS}")
        key = self._key(session, "catchup", minutes, profile.model_dump_json())
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        engine = session.engine
        pack = window_pack(engine, minutes, list(session.insights.values()))
        payload = {"task": "catchup", "context": pack, "language": profile.language.value,
                   "profile": _profile_context(session, profile)}
        draft, err = await call_agent(self.team.catchup, payload, CatchupDraft)
        source = self.generated_by
        report: FactCheckReport
        if draft is not None:
            outcome = check_text(engine, {**pack, "profile": payload["profile"]},
                                 [draft.headline, *draft.bullets, draft.momentum_summary], draft.claims)
            report = outcome.report
            if outcome.problems:
                report.actions.append("; ".join(outcome.problems))
                report.actions.append("agent text blocked; data-only catch-up published")
                draft = None
        else:
            report = FactCheckReport(status=FactCheckStatus.DATA_ONLY, actions=[f"fallback: {err}"])
        if draft is None:
            draft, source = templates.catchup(pack, profile.language.value), "data"
        st = engine.state()
        result = Catchup(
            window_minutes=minutes,
            from_minute=f"{max(0, int(engine.c.elapsed // 60) - minutes)}'",
            to_minute=str(st["minute_label"]),
            score_before=pack["window"]["score_at_start"], score_now=dict(engine.score()),
            headline=draft.headline, bullets=draft.bullets, momentum_summary=draft.momentum_summary,
            spotlight_player_id=draft.spotlight_player_id,
            insight_ids=[i["insight_id"] for i in session.insights.values()
                         if i["elapsed"] >= engine.c.elapsed - minutes * 60 and i["stage"] != "retracted"],
            profile=profile, fact_check=report, source=source,
        )
        self._cache[key] = result
        return result

    async def recap(self, session: MatchSession, profile: FanProfile) -> Recap:
        status = str(session.engine.state()["status"])
        phase = status if status in ("half_time", "full_time") else "live"
        key = self._key(session, "recap", profile.model_dump_json())
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        engine = session.engine
        pack = window_pack(engine, 130, list(session.insights.values()))
        payload = {"task": "recap", "phase": phase, "context": pack, "language": profile.language.value,
                   "profile": _profile_context(session, profile)}
        draft, err = await call_agent(self.team.recap, payload, RecapDraft, timeout=RECAP_TIMEOUT_S)
        source = self.generated_by
        if draft is not None:
            outcome = check_text(engine, {**pack, "profile": payload["profile"]},
                                 [draft.title, draft.summary, *draft.key_moments, draft.turning_point], draft.claims)
            report = outcome.report
            if outcome.problems:
                report.actions += ["; ".join(outcome.problems), "agent text blocked; data-only recap published"]
                draft = None
        else:
            report = FactCheckReport(status=FactCheckStatus.DATA_ONLY, actions=[f"fallback: {err}"])
        if draft is None:
            draft, source = templates.recap(pack, profile.language.value), "data"
        result = Recap(phase=phase, title=draft.title, summary=draft.summary, key_moments=draft.key_moments,
                       turning_point=draft.turning_point, player_of_the_match_id=draft.player_of_the_match_id,
                       profile=profile, fact_check=report, source=source)
        self._cache[key] = result
        return result


def personalise_feed(session: MatchSession, profile: FanProfile, limit: int = 50) -> list[dict[str, Any]]:
    """Insights rendered for one fan: their mode and language, their club's angle,
    and their favourite player's moments boosted (FR-035 to FR-037)."""
    out = []
    for ins in session.insights.values():
        if ins["stage"] == "retracted":
            continue
        mode = profile.mode.value
        text = (ins["variants"].get(mode, {}).get(profile.language.value)
                or ins["variants"].get(Mode.CASUAL.value, {}).get(profile.language.value)
                or {"headline": ins["headline"], "body": ins.get("explanation") or ""})
        involves_player = bool(profile.player_id and profile.player_id in ins["player_ids"])
        if mode == Mode.PLAYER_FOCUS.value and profile.player_id and not involves_player and ins["priority"] > 1:
            continue
        if mode == Mode.CASUAL.value and ins["priority"] >= 4:
            continue
        angle = None
        if profile.club_id:
            angle = ins["club_angles"].get(profile.club_id, {}).get(profile.language.value)
        relevance = (5 - ins["priority"]) + (3 if involves_player else 0) + \
            (1 if profile.club_id and ins["team_id"] == profile.club_id else 0)
        out.append({
            "insight_id": ins["insight_id"], "signal_type": ins["signal_type"], "minute_label": ins["minute_label"],
            "elapsed": ins["elapsed"], "team_id": ins["team_id"], "player_ids": ins["player_ids"],
            "headline": text["headline"], "body": text["body"], "club_angle": angle,
            "key_numbers": ins["key_numbers"], "stage": ins["stage"], "fact_check": ins["fact_check"]["status"],
            "favourite_player": involves_player, "relevance": relevance,
        })
    out.sort(key=lambda x: -x["elapsed"])
    return out[:limit]
