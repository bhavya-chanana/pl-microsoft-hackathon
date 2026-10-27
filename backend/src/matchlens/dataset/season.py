"""Generate a reproducible fictional season from aggregate-only local calibration.

Run from backend: python -m matchlens.dataset.season --source CURATED_V1 --out data/generated/season-v1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from matchlens.dataset.export import export_match
from matchlens.domain.models import MatchMeta, Side
from matchlens.simulator.engine import MatchSimulator
from matchlens.simulator.roster import _build_team
from matchlens.simulator.storyline import Beat, BeatType, Storyline
from matchlens.simulator.style import SimStyle

VERSION = 'matchlens-fictional-season/2'
PROFILE_VERSION = 'aggregate-profile/2'
LEAGUES = ('EPL', 'LA_LIGA', 'SERIE_A', 'BUNDESLIGA', 'LIGUE_1')
TEAMS = [('AXV', 'Axeville Athletic', 'Axeville', '#1F6FEB'), ('BVD', 'Brindle Vale', 'Brindle', '#E8A33D'),
         ('CRF', 'Cairnford United', 'Cairnford', '#A32D41'), ('DWO', 'Dovewick Orbit', 'Dovewick', '#32845D')]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def profile(source: Path) -> dict:
    """Only aggregated cohorts leave DuckDB; never export a source row or identifier."""
    import duckdb
    tables = source / 'tables'
    con = duckdb.connect(':memory:')
    for name in ('matches', 'match_events', 'team_match_stats'):
        path = tables / f'{name}.parquet'
        if not path.is_file():
            raise FileNotFoundError(path)
        # read_parquet is read-only. Views do not materialize source data.
        escaped = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{escaped}')")
    leagues = ','.join("'" + x + "'" for x in LEAGUES)
    where = f"league IN ({leagues}) AND season='2024-25'"
    counts = con.execute(f'''SELECT count(*), count(home_goals), count(away_goals),
        avg(home_goals), avg(away_goals), avg(CASE WHEN home_goals > away_goals THEN 1 ELSE 0 END),
        avg(CASE WHEN home_goals = away_goals THEN 1 ELSE 0 END),
        avg(CASE WHEN home_goals < away_goals THEN 1 ELSE 0 END),
        count(home_formation), count(away_formation), count(home_xg), count(away_xg)
        FROM matches WHERE {where} AND home_goals IS NOT NULL AND away_goals IS NOT NULL''').fetchone()
    formations = dict(con.execute(f'''SELECT formation, count(*) FROM (
       SELECT home_formation formation FROM matches WHERE {where} UNION ALL
       SELECT away_formation formation FROM matches WHERE {where}) WHERE formation IS NOT NULL GROUP BY 1 ORDER BY 1''').fetchall())
    events = dict(con.execute(f'''SELECT e.kind, count(*) FROM match_events e JOIN matches m USING(match_key)
      WHERE m.{where.replace(' AND ', ' AND m.')} AND e.kind IN ('goal','penalty_goal','own_goal','yellow','second_yellow','red') GROUP BY 1 ORDER BY 1''').fetchall())
    bins = dict(con.execute(f'''SELECT CAST(least(6, greatest(1, ceil(e.minute / 15.0))) AS INTEGER) bin, count(*)
      FROM match_events e JOIN matches m USING(match_key) WHERE m.{where.replace(' AND ', ' AND m.')}
      AND e.kind IN ('goal','penalty_goal','own_goal') AND e.minute IS NOT NULL GROUP BY 1 ORDER BY 1''').fetchall())
    score_distribution = {f'{h}-{a}': n for h, a, n in con.execute(f"SELECT home_goals, away_goals, count(*) FROM matches WHERE {where} AND home_goals IS NOT NULL AND away_goals IS NOT NULL GROUP BY 1,2 ORDER BY 1,2").fetchall()}
    event_matches = con.execute(f"SELECT count(DISTINCT e.match_key) FROM match_events e JOIN matches m USING(match_key) WHERE m.{where.replace(' AND ', ' AND m.')}").fetchone()[0]
    priors = {}
    for col in ('xg','shots','shots_on_target','corners','fouls'):
        n, mean = con.execute(f'''SELECT count(s.{col}), avg(s.{col}) FROM team_match_stats s JOIN matches m USING(match_key)
          WHERE m.league IN ({leagues}) AND m.season='2023-24' ''').fetchone()
        by_league = dict(con.execute(f"SELECT m.league, count(s.{col}) FROM team_match_stats s JOIN matches m USING(match_key) WHERE m.league IN ({leagues}) AND m.season='2023-24' GROUP BY 1 ORDER BY 1").fetchall())
        priors[col] = {'count': n, 'coverage_by_league': by_league, 'mean_per_team_match': mean, 'season': '2023-24', 'usage': 'validation_only'}
    con.close()
    return {'version': PROFILE_VERSION, 'population': {'leagues': list(LEAGUES), 'season': '2024-25', 'matches': counts[0]},
       'score': {'distribution': score_distribution, 'home_mean': counts[3], 'away_mean': counts[4], 'home_win': counts[5], 'draw': counts[6], 'away_win': counts[7], 'coverage': {'home': counts[1], 'away': counts[2]}, 'usage': 'sample_score_distribution'},
       'formations': {'counts': formations, 'coverage': counts[8]+counts[9], 'usage': 'sample_supported_formations'},
       'events': {'counts': events, 'goal_minute_15m_bins': bins, 'matches_with_events': event_matches, 'coverage_note': 'event rows only; incomplete event coverage possible', 'usage': 'sample_goal_time_bins; cards validation_only'},
       'xg_2024_25_coverage': {'home': counts[10], 'away': counts[11]}, 'earlier_team_stats': priors,
       'limitations': ['No match-level source records or identities are exported.', 'Simulator pass and tracking dynamics use existing synthetic reference, not this population.', '2023-24 priors are validation only.']}


def _team(spec: tuple, side: Side, formation: str):
    tid, name, short, colour = spec
    # Invented labels with no source names; stable identity across fixtures.
    roster = [(f'{short} Player {i:02d}', f'{short} {i:02d}', 'Fictional', 7.5 if i in (1,12) else 8.4 + (i % 6)*0.2, 0, 0) for i in range(1,17)]
    return _build_team(tid, name, short, side, colour, formation, roster)


def validate_profile(p: dict) -> None:
    """Validate the aggregate contract before it is used for any generation."""
    import math
    import re
    if set(p) != {'version','population','score','formations','events','xg_2024_25_coverage','earlier_team_stats','limitations'}:
        raise ValueError('invalid profile schema')
    if p.get('version') != PROFILE_VERSION or p.get('population', {}).get('season') != '2024-25' or p['population'].get('leagues') != list(LEAGUES):
        raise ValueError('unexpected profile version or cohort')
    n = p['population'].get('matches')
    if type(n) is not int or n < 1:
        raise ValueError('empty population')
    scores = p['score']['distribution']
    if not isinstance(scores, dict) or not scores or any(not re.fullmatch(r'[0-9]+-[0-9]+', k) or type(v) is not int or v <= 0 or any(int(x)>20 for x in k.split('-')) for k,v in scores.items()) or sum(scores.values()) != n:
        raise ValueError('invalid score distribution')
    if p['score']['coverage'] != {'home':n, 'away':n} or p['score']['usage'] != 'sample_score_distribution':
        raise ValueError('invalid score coverage or usage')
    pairs = [(int(k.split('-')[0]),int(k.split('-')[1]),v) for k,v in scores.items()]
    expected = {'home_mean':sum(h*c for h,a,c in pairs)/n, 'away_mean':sum(a*c for h,a,c in pairs)/n,
                'home_win':sum(c for h,a,c in pairs if h>a)/n, 'draw':sum(c for h,a,c in pairs if h==a)/n,
                'away_win':sum(c for h,a,c in pairs if h<a)/n}
    if any(not isinstance(p['score'].get(k),(int,float)) or not math.isclose(p['score'][k],v,abs_tol=1e-9) for k,v in expected.items()):
        raise ValueError('score statistics inconsistent with distribution')
    formations = p['formations']['counts']
    if not isinstance(formations, dict) or any(not re.fullmatch(r'[0-9]+(?:-[0-9]+)+', str(k)) or type(v) is not int or v <= 0 for k,v in formations.items()) or sum(formations.values()) != p['formations']['coverage'] or not sum(formations.get(f,0) for f in ('4-3-3','4-2-3-1')):
        raise ValueError('invalid formation counts')
    bins = p['events']['goal_minute_15m_bins']
    if not isinstance(bins, dict) or not bins or any(str(k) not in ('1','2','3','4','5','6') or type(v) is not int or v <= 0 for k,v in bins.items()):
        raise ValueError('invalid goal time bins')
    ev = p['events']
    if type(ev['matches_with_events']) is not int or not 0 <= ev['matches_with_events'] <= n:
        raise ValueError('invalid event coverage')
    if set(ev['counts']) - {'goal','penalty_goal','own_goal','yellow','second_yellow','red'} or any(type(v) is not int or v < 0 for v in ev['counts'].values()):
        raise ValueError('invalid event counts')
    if sum(bins.values()) > sum(ev['counts'].get(k,0) for k in ('goal','penalty_goal','own_goal')):
        raise ValueError('invalid goal timing coverage')
    if p['formations']['usage'] != 'sample_supported_formations' or ev['usage'] != 'sample_goal_time_bins; cards validation_only':
        raise ValueError('invalid aggregate usage')
    if set(p['xg_2024_25_coverage']) != {'home','away'} or any(type(v) is not int or not 0 <= v <= n for v in p['xg_2024_25_coverage'].values()):
        raise ValueError('invalid xg coverage')
    if set(p['earlier_team_stats']) != {'xg','shots','shots_on_target','corners','fouls'}:
        raise ValueError('invalid prior schema')
    for key in ('xg','shots','shots_on_target','corners','fouls'):
        prior = p['earlier_team_stats'][key]
        if set(prior['coverage_by_league']) - set(LEAGUES) or any(type(v) is not int or v < 0 for v in prior['coverage_by_league'].values()):
            raise ValueError('invalid prior cohorts')
        if prior['season'] != '2023-24' or prior['usage'] != 'validation_only' or type(prior['count']) is not int or prior['count'] < 0 or sum(prior['coverage_by_league'].values()) != prior['count']:
            raise ValueError('invalid prior coverage')
        value = prior['mean_per_team_match']
        if (prior['count'] == 0 and value is not None) or (prior['count'] > 0 and (not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0)):
            raise ValueError('invalid prior mean')


def season_plan(p: dict, seed: int) -> list[tuple[MatchMeta, Storyline, int, dict[str, SimStyle], dict[str, int]]]:
    """Reconstruct all choices from aggregates and seed; no source rows are sampled."""
    validate_profile(p)
    if type(seed) is not int or seed < 0:
        raise ValueError('seed must be a non-negative integer')
    rng = random.Random(seed)
    # Stable fictional style variation, independent of all real team identities.
    styles = {t[0]: SimStyle(shot_appetite=round(rng.uniform(.92,1.08),4), tempo=round(rng.uniform(.95,1.05),4)) for t in TEAMS}
    formations = ('4-3-3','4-2-3-1')
    fw = [p['formations']['counts'].get(f,0) for f in formations]
    score_keys = sorted(p['score']['distribution'], key=lambda x: tuple(map(int,x.split('-'))))
    score_weights = [p['score']['distribution'][k] for k in score_keys]
    bin_counts = {int(k):v for k,v in p['events']['goal_minute_15m_bins'].items()}
    plan = []
    for home in TEAMS:
        for away in TEAMS:
            if home == away: continue
            index = len(plan)+1
            mid = f'ml-season-{seed}-{index:02d}'
            ht, hp = _team(home, Side.HOME, rng.choices(formations,fw)[0])
            at, ap = _team(away, Side.AWAY, rng.choices(formations,fw)[0])
            hs, aws = map(int,rng.choices(score_keys,score_weights)[0].split('-'))
            target = {home[0]:hs,away[0]:aws}
            # Choose unique available minutes to keep beats well separated from
            # half-time/full-time boundaries. Bin weights reflect the aggregate;
            # minutes within each bin are uniform and never copy source records.
            available = {b:[m for m in range((b-1)*15+1,b*15+1) if m not in (44,45,89,90)] for b in range(1,7)}
            beats = []
            for team, goals in ((home[0],hs),(away[0],aws)):
                for _ in range(goals):
                    usable = [b for b in range(1,7) if available[b] and bin_counts.get(b,0)>0]
                    if not usable: raise ValueError('insufficient goal minute choices')
                    b = rng.choices(usable,[bin_counts[b] for b in usable])[0]
                    minute = rng.choice(available[b]); available[b].remove(minute)
                    scorer = rng.choices(range(2,12),[1,1,1,1,2,2,3,4,5,4])[0]
                    beats.append(Beat(minute=minute,type=BeatType.GOAL,team_id=team,player_id=f'{team}-{scorer:02d}'))
            story = Storyline(title='Fictional season fixture', summary='Independently sampled synthetic goals and timing.', beats=beats, scripted_goals_only=True)
            meta = MatchMeta(match_id=mid,competition='Matchlens Fictional League',venue=f'{home[1]} Ground',kickoff_utc=f'2026-11-{index:02d}T14:00:00Z',home=ht,away=at,players={player.player_id:player for player in hp+ap},storyline_title=story.title)
            plan.append((meta,story,seed*100+index,{home[0]:styles[home[0]],away[0]:styles[away[0]]},target))
    return plan


def standings_for(entries: list[dict]) -> list[dict]:
    standings = {t[0]: {'team_id': t[0], 'played': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'goals_for': 0, 'goals_against': 0, 'points': 0} for t in TEAMS}
    fixtures = [(h,a) for h in TEAMS for a in TEAMS if h != a]
    for entry,(home,away) in zip(entries,fixtures,strict=True):
        hs, aws = entry['final_score'][home[0]], entry['final_score'][away[0]]
        for tid,gf,ga in ((home[0],hs,aws),(away[0],aws,hs)):
            row = standings[tid]; row['played'] += 1; row['goals_for'] += gf; row['goals_against'] += ga
            row['wins' if gf > ga else 'draws' if gf == ga else 'losses'] += 1
            row['points'] += 3 if gf > ga else 1 if gf == ga else 0
    return sorted(standings.values(),key=lambda r:(-r['points'],-(r['goals_for']-r['goals_against']),-r['goals_for'],r['team_id']))


def generate_season(source: Path, out: Path, seed: int = 20261027) -> dict:
    allowed = (Path(__file__).resolve().parents[3] / 'data' / 'generated').resolve()
    if not out.resolve().is_relative_to(allowed): raise ValueError(f'output must be inside {allowed}')
    if out.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(out.resolve()): raise ValueError('source and output must be separate')
    p = profile(source)
    plan = season_plan(p,seed)
    out.mkdir(parents=True,exist_ok=True)
    write_json(out/'profile.json',p)
    entries = []
    for meta,story,match_seed,styles,target in plan:
        entry = export_match(MatchSimulator(seed=match_seed,meta=meta,storyline=story,styles=styles).run(),story,out)
        if entry['final_score'] != target: raise RuntimeError(f'{meta.match_id}: sampled goals did not complete')
        entry['target_score'] = target
        entry['styles'] = {k:v.model_dump(mode='json') for k,v in styles.items()}
        entry['files'] = {name:digest(out/meta.match_id/name) for name in ('meta.json','events.jsonl','tracking.jsonl.gz')}
        entries.append(entry)
    manifest = {'format':VERSION,'schema':VERSION,'source':'synthetic-season','season':'fictional-2026','calibration':'2024-25 domestic aggregates; 2023-24 validation priors','synthetic':True,'seed':seed,'generator':'MatchSimulator scripted sampled goals','profile_sha256':digest(out/'profile.json'),'matches':entries,'standings':standings_for(entries),'provenance':'Independently simulated fictional teams and players. Aggregate source profile only; no source match records.'}
    write_json(out/'manifest.json',manifest)
    return manifest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261027)
    args = parser.parse_args()
    result = generate_season(args.source, args.out, args.seed)
    print(f"Generated {len(result['matches'])} fictional matches in {args.out}")

if __name__ == '__main__':
    main()
