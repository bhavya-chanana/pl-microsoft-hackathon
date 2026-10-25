"""Real league data: fetch, normalise, curate, and turn into tactical knowledge.

Sources (see docs/LEAGUEDATA.md for licences and attribution): Understat
(shots with xG, per-match PPDA), Transfermarkt datasets (lineups, formations,
managers, Champions League), football-data.co.uk (results, odds), ClubElo,
Fantasy Premier League, StatsBomb open data, Wikidata and Wikipedia.

Raw downloads and derived tables stay local under ``backend/data/local/leaguedata``
(gitignored): several sources forbid redistribution. Tests use fictional
fixtures only.
"""
