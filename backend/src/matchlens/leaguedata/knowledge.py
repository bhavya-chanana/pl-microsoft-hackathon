"""Club and manager profiles assembled from normalised rows (no model, no network).

Facts (founding year, stadium, honours, manager tenures) come from Wikidata rows; text comes
from Wikipedia ``TextDoc`` rows and is returned with attribution. Preferred formations come
from our own ``Match`` rows. Rivalries are only reported when supplied (Wikidata has no
reliable rivalry property), otherwise ``ClubProfile.rivals`` is empty.

    club_profile(team_id, clubs=..., honours=..., spells=..., managers=..., docs=...)
    manager_profile(manager_id, managers=..., clubs=..., spells=..., honours=..., docs=..., matches=...)
    Knowledge.from_tables(tables).club(team_id) / .manager(manager_id)
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from matchlens.leaguedata.contracts import ClubProfile, FormationShare, ManagerProfile
from matchlens.leaguedata.schema import (
    Club,
    Honour,
    Manager,
    ManagerSpell,
    Match,
    TextDoc,
)

WIKIDATA_ATTRIBUTION = "Wikidata (https://www.wikidata.org), CC0 1.0"
MAX_TEXT = 3000


def _month(date: str | None) -> str:
    return date[:7] if date else "present"


def _season_end(season: str) -> str:
    """Approximate end of a ``YYYY-YY`` season: 30 June of its second calendar year."""
    return f"{int(season[:4]) + 1}-06-30"


def _docs_for(docs: Iterable[TextDoc], kind: str, entity_id: str) -> tuple[TextDoc | None, TextDoc | None]:
    """(summary, section) of an entity; sections have titles of the form ``Page#Section``."""
    summary = section = None
    for d in docs:
        if d.entity_kind != kind or d.entity_id != entity_id:
            continue
        if "#" in d.title:
            section = section or d
        else:
            summary = summary or d
    return summary, section


def _text(summary: TextDoc | None, section: TextDoc | None) -> str | None:
    parts = [d.text.strip() for d in (summary, section) if d is not None and d.text.strip()]
    if not parts:
        return None
    text = "\n\n".join(parts)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT].rstrip() + "…"


def _cite(doc: TextDoc) -> str:
    return f"Wikipedia: {doc.title.split('#')[0]} - {doc.url} ({doc.licence}, retrieved {doc.retrieved})"


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def club_profile(team_id: str, *, clubs: Sequence[Club], honours: Sequence[Honour] = (),
                 spells: Sequence[ManagerSpell] = (), managers: Sequence[Manager] = (),
                 docs: Sequence[TextDoc] = (), rivals: Mapping[str, Sequence[str]] | None = None) -> ClubProfile:
    club = next((c for c in clubs if c.team_id == team_id), None)
    if club is None:
        raise KeyError(f"unknown club {team_id!r}")
    names = {m.manager_id: m.name for m in managers}
    won = sorted((h for h in honours if h.team_id == team_id), key=lambda h: (h.season, h.competition), reverse=True)
    timeline = sorted((s for s in spells if s.team_id == team_id), key=lambda s: (s.start, s.end or "9999"),
                      reverse=True)
    summary, section = _docs_for(docs, "club", team_id)
    attribution = [_cite(d) for d in (summary, section) if d is not None]
    if any(h.source == "wikidata" for h in won) or timeline or club.source_ids.wikidata:
        attribution.append(WIKIDATA_ATTRIBUTION)
    return ClubProfile(
        team_id=team_id, name=club.name, country=club.country, founded=club.founded, stadium=club.stadium,
        honours=[f"{h.competition} {h.season}" for h in won],
        rivals=list((rivals or {}).get(team_id, ())),
        manager_timeline=[f"{names.get(s.manager_id, s.manager_id)} ({_month(s.start)} to {_month(s.end)})"
                          for s in timeline],
        history=_text(summary, section),
        attribution=_unique(attribution))


def manager_formations(manager_id: str, matches: Iterable[Match]) -> list[FormationShare]:
    """Formations used in the manager's matches, most used first (share of matches with a formation)."""
    counts: Counter[str] = Counter()
    for m in matches:
        if m.home_manager_id == manager_id and m.home_formation:
            counts[m.home_formation] += 1
        elif m.away_manager_id == manager_id and m.away_formation:
            counts[m.away_formation] += 1
    total = sum(counts.values())
    return [FormationShare(formation=f, share=round(n / total, 3))
            for f, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def manager_profile(manager_id: str, *, managers: Sequence[Manager], clubs: Sequence[Club] = (),
                    spells: Sequence[ManagerSpell] = (), honours: Sequence[Honour] = (),
                    docs: Sequence[TextDoc] = (), matches: Sequence[Match] = ()) -> ManagerProfile:
    manager = next((m for m in managers if m.manager_id == manager_id), None)
    if manager is None:
        raise KeyError(f"unknown manager {manager_id!r}")
    club_names = {c.team_id: c.name for c in clubs}
    mine = sorted((s for s in spells if s.manager_id == manager_id), key=lambda s: (s.start, s.end or "9999"),
                  reverse=True)
    trophies: list[str] = []
    for h in sorted(honours, key=lambda h: (h.season, h.competition), reverse=True):
        end = _season_end(h.season)
        # In charge when the season ended (30 June of its second year).
        if any(s.team_id == h.team_id and s.start <= end and (s.end is None or s.end >= end) for s in mine):
            trophies.append(f"{h.competition} {h.season} with {club_names.get(h.team_id, h.team_id)}")
    summary, section = _docs_for(docs, "manager", manager_id)
    attribution = [_cite(d) for d in (summary, section) if d is not None]
    if mine or manager.source_ids.wikidata:
        attribution.append(WIKIDATA_ATTRIBUTION)
    return ManagerProfile(
        manager_id=manager_id, name=manager.name, nationality=manager.nationality,
        tenures=[f"{club_names.get(s.team_id, s.team_id)} ({_month(s.start)} to {_month(s.end)})" for s in mine],
        trophies=trophies, formations=manager_formations(manager_id, matches),
        philosophy=_text(summary, section), attribution=_unique(attribution))


@dataclass(frozen=True)
class Knowledge:
    """Rows of the knowledge tables, with profile builders."""

    clubs: Sequence[Club] = ()
    managers: Sequence[Manager] = ()
    spells: Sequence[ManagerSpell] = ()
    honours: Sequence[Honour] = ()
    docs: Sequence[TextDoc] = ()
    matches: Sequence[Match] = ()
    rivals: Mapping[str, Sequence[str]] = field(default_factory=dict)

    @classmethod
    def from_tables(cls, tables: Mapping[str, Sequence], rivals: Mapping[str, Sequence[str]] | None = None) -> Knowledge:
        return cls(clubs=tables.get("clubs", ()), managers=tables.get("managers", ()),
                   spells=tables.get("manager_spells", ()), honours=tables.get("honours", ()),
                   docs=tables.get("text_docs", ()), matches=tables.get("matches", ()), rivals=rivals or {})

    def club(self, team_id: str) -> ClubProfile:
        return club_profile(team_id, clubs=self.clubs, honours=self.honours, spells=self.spells,
                            managers=self.managers, docs=self.docs, rivals=self.rivals)

    def manager(self, manager_id: str) -> ManagerProfile:
        return manager_profile(manager_id, managers=self.managers, clubs=self.clubs, spells=self.spells,
                               honours=self.honours, docs=self.docs, matches=self.matches)
