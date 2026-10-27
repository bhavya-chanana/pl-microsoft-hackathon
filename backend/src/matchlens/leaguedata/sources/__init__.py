"""Source adapters. Each module exposes the same two functions:

    fetch(cfg: Config, leagues: Sequence[League], seasons: Sequence[str], *,
          limit: int | None = None, refresh: bool = False) -> int
        Download raw files into cfg.raw_dir(<source>); returns the number of new files.
        Leagues a source does not cover are skipped silently.

    normalize(cfg: Config) -> dict[str, list[BaseModel]]
        Read the raw files (no network) and return rows keyed by table name
        (schema.TABLES). Ids are provisional source-scoped slugs
        ("<source>:<id>"); build.py resolves them through ids.py.

SOURCES maps the CLI name to the module path; modules are imported lazily so a
missing optional dependency only affects its own source.
"""

from __future__ import annotations

SOURCES: dict[str, str] = {
    "understat": "matchlens.leaguedata.sources.understat",
    "football_data": "matchlens.leaguedata.sources.footballdata",
    "clubelo": "matchlens.leaguedata.sources.clubelo",
    "transfermarkt": "matchlens.leaguedata.sources.transfermarkt",
    "fpl": "matchlens.leaguedata.sources.fpl",
    "statsbomb": "matchlens.leaguedata.sources.statsbomb",
    "openfootball": "matchlens.leaguedata.sources.openfootball",
    "wikidata": "matchlens.leaguedata.sources.wikidata",
    "wikipedia": "matchlens.leaguedata.sources.wikipedia",
}
