# League data (`leaguedata/`)

The league dataset behind the Tactics Brain. It is built from public sources for the Premier League, La Liga, Serie A, the Champions League and, optionally, the Bundesliga and Ligue 1, for seasons 2019-20 to 2025-26.

Real names are allowed for this feature only (BRD §9.3 amendment). Everything downloaded or derived stays under `backend/data/local/leaguedata/`, which is gitignored. Test fixtures use fictional names.

## Pipeline

```
fetch  ->  raw/<source>/...              cached responses, resumable
build  ->  tables/<table>.parquet        normalised rows (schema.py), ids resolved (ids.py)
curate ->  curated/v<N>/                 validated, deduplicated, quality flags, splits, dataset card
reason ->  reasoning/<match_key>.json    MatchReasoning per match (strong model, fact-checked)
playbooks ->                             Playbook per team-season
export ->  kb/                           the knowledge-base bundle (profiles, playbooks, reasonings)
```

| Command | What it does |
|---|---|
| `npm run data:fetch -- --leagues EPL,LA_LIGA --seasons 2023-24 --limit 10` | Download raw data. Already-cached files are skipped. `--refresh` re-downloads, `--sources understat,clubelo` narrows it |
| `npm run data:build` | Normalise every source into Parquet tables (DuckDB) |
| `npm run data:curate` | Write the next `curated/v<N>/` and update `curated/LATEST` |
| `npm run data:reason -- --limit 10` | Run the match reasoner. Resumes by digest hash. `MATCHLENS_LLM=offline` uses templates |
| `npm run data:playbooks` | Aggregate pattern tags into team-season playbooks |
| `npm run data:export` | Write the knowledge-base bundle (playbooks, profiles, reasonings) to `kb/` |
| `npm run data:status` | Row counts per stage |

The backend serves the knowledge base at `/api/kb/*` only when `MATCHLENS_KB_DIR` points at a bundle.

## Sources and licences

| Source | Gives | Coverage | Licence and terms |
|---|---|---|---|
| Understat | Matches, xG, shots with coordinates, lineups, PPDA, deep completions, player xG/xA | 5 leagues, no UCL | No published licence. Personal and research use; do not redistribute |
| football-data.co.uk | Results, half-time scores, shots, corners, cards, referees, closing odds | 5 leagues | Free to use with attribution |
| ClubElo | Elo ratings by date | All clubs | Free with attribution |
| Transfermarkt (via the `transfermarkt-datasets` project) | Lineups, formations, managers, goal/card/substitution events, market values, UCL games | 5 leagues + UCL | The dataset is CC0; the underlying data is scraped from Transfermarkt, whose terms restrict reuse. Local use only |
| Fantasy Premier League | Player positions and season history | EPL only | Unofficial API. Local use only |
| StatsBomb open data | Full events and 360 frames for selected competitions (xT fit, Track C training) | Selected | StatsBomb open-data user agreement. Attribution required |
| openfootball | Results and UCL stages (Football.TXT) | 5 leagues + UCL | CC0 / public domain |
| Wikidata | Clubs, honours, manager spells, player birth date, height and nationality | All | CC0 |
| Wikipedia | Club, manager and player summaries; History and Playing style sections | All | CC BY-SA 4.0. Every text keeps its URL and retrieval date |

`curated/v<N>/share/` holds only the redistributable subset: Wikidata, openfootball, and StatsBomb-derived data with attribution. Nothing else may leave the machine without checking the terms above.

## Identity

- Every entity gets our own slug. Provider ids are kept in `source_ids`, and every cross-source link is recorded in `id_links` with a confidence and a method (`exact_id`, `name_club_season`, `name_birth`, `fuzzy`, `override`, ...).
- Wrong links are fixed in `overrides.json` next to the tables, never in code.
- `match_key` = `{league}:{season}:{date}:{home}-{away}`.
- Wikidata squad membership is stale. It is used for player bio fields only, never to decide who plays for whom.

## Curated dataset

`curated/v<N>/` is the only input for the reasoner, the knowledge base, the tactician and Track C training. It contains:

- Validated tables, with rejected rows listed alongside each table.
- Quality flags on matches (`has_xg`, `has_lineup`, `has_formation`, `source_conflict`, ...).
- `coverage.json` per league and season.
- Deterministic 80/10/10 splits by match.
- A `DATASET_CARD.md`.
- A manifest with sha256 hashes.

## Known gaps

- There is no free tracking or pass-level event data for these leagues in 2019-26. Team style comes from aggregate proxies (PPDA, deep completions, xG profiles, situation shares, game-state behaviour), not from passes.
- There is no Understat xG for the Champions League. UCL digests say so, and UCL style leans on the league matches.
- ClubElo is sometimes unavailable. Matches then lack Elo, and odds-implied probabilities stand in.
- Rivalries are not in any source. `ClubProfile.rivals` is empty unless supplied.
- Mentality metrics are computed from match timelines only. The reasoner and tactician treat them as a tiebreaker, never as evidence that overrides data.
