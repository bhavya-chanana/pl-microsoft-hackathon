"""Fictional clubs and players for synthetic matches.

All names are invented. No real club, player, competition or venue names may
appear here (Official Rules §4.4 and §4.7).
"""

from __future__ import annotations

from matchlens.domain.models import MatchMeta, Player, Position, Side, Team

# Formation slots: (role, position) in the order used by starting_xi.
FORMATIONS: dict[str, list[tuple[str, Position]]] = {
    "4-3-3": [
        ("GK", Position.GK),
        ("LB", Position.DEF),
        ("LCB", Position.DEF),
        ("RCB", Position.DEF),
        ("RB", Position.DEF),
        ("LCM", Position.MID),
        ("CM", Position.MID),
        ("RCM", Position.MID),
        ("LW", Position.FWD),
        ("ST", Position.FWD),
        ("RW", Position.FWD),
    ],
    "4-2-3-1": [
        ("GK", Position.GK),
        ("LB", Position.DEF),
        ("LCB", Position.DEF),
        ("RCB", Position.DEF),
        ("RB", Position.DEF),
        ("LDM", Position.MID),
        ("RDM", Position.MID),
        ("LAM", Position.MID),
        ("CAM", Position.MID),
        ("RAM", Position.MID),
        ("ST", Position.FWD),
    ],
}

# (name, short name, nationality, top speed m/s, career apps, career goals)
_NORTHBRIDGE = [
    ("Mateo Kalnins", "Kalnins", "Latvia", 7.6, 212, 0),
    ("Idris Okonkwo-Bell", "Okonkwo-Bell", "England", 9.3, 148, 6),
    ("Bastian Rehm", "Rehm", "Germany", 8.4, 263, 11),
    ("Kwame Asante-Lowe", "Asante-Lowe", "Ghana", 8.7, 97, 4),
    ("Luca Ferrandi", "Ferrandi", "Italy", 9.1, 174, 9),
    ("Theo Marchetti", "Marchetti", "Italy", 8.6, 121, 14),
    ("Rui Valadares", "Valadares", "Portugal", 8.3, 299, 31),
    ("Jonah Whitcombe", "Whitcombe", "England", 8.9, 88, 12),
    ("Kenji Arakawa", "Arakawa", "Japan", 9.6, 136, 38),
    ("Elias Brandvold", "Brandvold", "Norway", 9.2, 149, 99),
    ("Samir Haddou", "Haddou", "Morocco", 9.7, 64, 17),
    # Bench
    ("Owen Pritchard-Sae", "Pritchard-Sae", "Wales", 7.5, 41, 0),
    ("Nico Duvalier", "Duvalier", "France", 8.6, 59, 2),
    ("Arjun Mehrotra", "Mehrotra", "India", 8.8, 33, 3),
    ("Dario Velasquez", "Velasquez", "Spain", 9.0, 71, 8),
    ("Femi Adeyinka", "Adeyinka", "Nigeria", 9.5, 22, 5),
]

_HARBOUR = [
    ("Pavel Hrubesko", "Hrubesko", "Czechia", 7.4, 305, 0),
    ("Marcus Delacroix-Ade", "Delacroix-Ade", "France", 9.2, 133, 5),
    ("Ruben Oyelaran", "Oyelaran", "Netherlands", 8.5, 188, 13),
    ("Stellan Moberg", "Moberg", "Sweden", 8.2, 241, 9),
    ("Tiago Brancal", "Brancal", "Brazil", 9.4, 112, 7),
    ("Callum Ashdown", "Ashdown", "Scotland", 8.4, 276, 22),
    ("Yusuf Demirkan", "Demirkan", "Turkey", 8.5, 167, 10),
    ("Ilya Sorokine", "Sorokine", "Belgium", 9.3, 102, 21),
    ("Andrés Quilmes", "Quilmes", "Argentina", 8.8, 219, 56),
    ("Mikael Strandh", "Strandh", "Denmark", 9.4, 90, 19),
    ("Dominic Achterberg", "Achterberg", "Austria", 9.1, 181, 72),
    # Bench
    ("Lewis Carrow", "Carrow", "England", 7.3, 18, 0),
    ("Hamid Benali", "Benali", "Algeria", 8.7, 77, 3),
    ("Jakub Wilczek", "Wilczek", "Poland", 8.4, 95, 6),
    ("Sol Mbeki-Ross", "Mbeki-Ross", "South Africa", 9.6, 40, 9),
    ("Ezra Linwood", "Linwood", "Ireland", 9.0, 26, 4),
]

_BENCH_POSITIONS = [Position.GK, Position.DEF, Position.MID, Position.MID, Position.FWD]
_BENCH_ROLES = ["GK", "CB", "CM", "AM", "ST"]


def _build_team(
    team_id: str,
    name: str,
    short_name: str,
    side: Side,
    colour: str,
    formation: str,
    roster: list[tuple[str, str, str, float, int, int]],
) -> tuple[Team, list[Player]]:
    slots = FORMATIONS[formation]
    players: list[Player] = []
    for idx, (full, short, nat, speed, apps, goals) in enumerate(roster):
        if idx < len(slots):
            role, position = slots[idx]
        else:
            role, position = _BENCH_ROLES[idx - len(slots)], _BENCH_POSITIONS[idx - len(slots)]
        players.append(
            Player(
                player_id=f"{team_id}-{idx + 1:02d}",
                name=full,
                short_name=short,
                shirt=_shirt_number(role, idx),
                position=position,
                role=role,
                nationality=nat,
                top_speed=speed,
                career_appearances=apps,
                career_goals=goals,
            )
        )
    team = Team(
        team_id=team_id,
        name=name,
        short_name=short_name,
        side=side,
        colour=colour,
        formation=formation,
        starting_xi=[p.player_id for p in players[: len(slots)]],
        bench=[p.player_id for p in players[len(slots) :]],
    )
    return team, players


_SHIRTS = {
    "GK": 1, "RB": 2, "LB": 3, "RCB": 4, "LCB": 5, "RCM": 6, "RDM": 6, "CM": 8,
    "ST": 9, "CAM": 10, "LW": 11, "LAM": 11, "RW": 7, "RAM": 7, "LCM": 14, "LDM": 16,
}
_BENCH_SHIRTS = [13, 15, 18, 20, 21]


def _shirt_number(role: str, idx: int) -> int:
    if idx >= 11:
        return _BENCH_SHIRTS[idx - 11]
    return _SHIRTS[role]


def default_match_meta(match_id: str = "ml-demo-001", storyline_title: str | None = None) -> MatchMeta:
    home, home_players = _build_team(
        "NBR", "Northbridge FC", "Northbridge", Side.HOME, "#1F6FEB", "4-3-3", _NORTHBRIDGE
    )
    away, away_players = _build_team(
        "HBC", "Harbour City", "Harbour", Side.AWAY, "#E8A33D", "4-2-3-1", _HARBOUR
    )
    return MatchMeta(
        match_id=match_id,
        competition="Matchlens Invitational League",
        venue="Kestrel Park",
        kickoff_utc="2026-10-24T14:00:00Z",
        home=home,
        away=away,
        players={p.player_id: p for p in home_players + away_players},
        storyline_title=storyline_title,
    )
