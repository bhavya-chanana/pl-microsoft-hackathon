"""Preview or strictly import a generated fictional season into a local Cosmos emulator."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from matchlens.agents import templates
from matchlens.agents.context import signal_pack
from matchlens.agents.contracts import FactCheckReport, FactCheckStatus, Insight
from matchlens.agents.coordinator import Coordinator, Plan
from matchlens.agents.pipeline import build_insight, build_overlay, data_variants
from matchlens.agents.producer import OverlayScheduler
from matchlens.dataset.export import export_match, load_match
from matchlens.dataset.season import VERSION, digest, season_plan, standings_for
from matchlens.domain.models import Event, EventType
from matchlens.ingest.adapters import feed_order
from matchlens.interpret.engine import MatchEngine
from matchlens.interpret.signals import SignalType
from matchlens.simulator.engine import MatchSimulator
from matchlens.store.base import CONTAINERS, EMULATOR_KEY, Store, StoreSettings, from_doc, to_doc


SEED_SCHEMA = 'synthetic-archive/2'


def inspect_dataset(folder: Path) -> dict:
    manifest = json.loads((folder / 'manifest.json').read_text())
    if (manifest.get('format') != VERSION or manifest.get('schema') != VERSION or manifest.get('source') != 'synthetic-season'
            or manifest.get('season') != 'fictional-2026' or manifest.get('synthetic') is not True
            or manifest.get('calibration') != '2024-25 domestic aggregates; 2023-24 validation priors'):
        raise ValueError('expected a synthetic season manifest')
    if digest(folder/'profile.json') != manifest['profile_sha256']:
        raise ValueError('profile digest mismatch')
    profile = json.loads((folder/'profile.json').read_text())
    plan = season_plan(profile, manifest.get('seed'))
    if len(manifest['matches']) != len(plan):
        raise ValueError('expected 12 fixtures')
    for e, (meta, story, match_seed, styles, target) in zip(manifest['matches'],plan,strict=True):
        mid = meta.match_id
        if e.get('match_id') != mid or e.get('path') != mid or e.get('seed') != match_seed:
            raise ValueError('invalid fixture identity or seed')
        if e.get('target_score') != target or e.get('styles') != {k:v.model_dump(mode='json') for k,v in styles.items()}:
            raise ValueError('fixture choices do not match profile and seed')
        for name in ('meta.json', 'events.jsonl', 'tracking.jsonl.gz'):
            if digest(folder/mid/name) != e['files'][name]:
                raise ValueError(f'digest mismatch: {mid}/{name}')
        # Regenerate the entire match from profile and seed, including its fictional
        # roster, formations, sampled goals, times and style. A hash alone does not
        # establish that the feed was independently simulated.
        with tempfile.TemporaryDirectory() as tmp:
            replay = MatchSimulator(seed=match_seed,meta=meta,storyline=story,styles=styles).run()
            regenerated = export_match(replay,story,Path(tmp))
            for name in ('meta.json','events.jsonl','tracking.jsonl.gz'):
                if digest(Path(tmp)/mid/name) != e['files'][name]:
                    raise ValueError(f'feed is not reproducible synthetic output: {mid}/{name}')
            for key in ('match_id','seed','storyline','scripted','path','final_score','events','tracking_frames'):
                if regenerated[key] != e[key]:
                    raise ValueError(f'manifest mismatch: {mid}/{key}')
            if regenerated['final_score'] != target:
                raise ValueError(f'sampled score mismatch: {mid}')
    if manifest.get('standings') != standings_for(manifest['matches']):
        raise ValueError('standings do not match fixtures')
    return manifest


def build_documents(folder: Path, entry: dict, manifest_hash: str) -> tuple[str, dict[str, list[dict]]]:
    match = load_match(folder/entry['path'])
    run_id = f"synthetic-v2-{match.meta.match_id}-{manifest_hash[:12]}"
    engine = MatchEngine(match.meta)
    docs: dict[str, list[dict]] = {k: [] for k in ('runs','events','frames','signals','insights','overlays')}
    signals = {}
    insights = {}
    overlays = {}
    coordinator = Coordinator()
    scheduler = OverlayScheduler()
    stats = {}
    for item in feed_order(match.events, match.tracking):
        if isinstance(item, Event):
            new = engine.ingest_event(item)
            q = engine.pass_quality(item.event_id)
            payload = {**item.model_dump(mode='json'), 'minute_label': item.minute_label}
            if q: payload['pass_quality'] = q.model_dump()
            docs['events'].append(to_doc(item.event_id, run_id, payload))
            if item.type in (EventType.HALF_TIME, EventType.FULL_TIME):
                phase = item.type.value
                stats[phase] = {'doc_type': 'stats', 'phase': phase,
                    'teams': {t.team_id:engine.team_stats(t.team_id) for t in (match.meta.home,match.meta.away)},
                    'players': {pid:engine.player_stats(pid) for pid in match.meta.players}}
        else:
            new = engine.ingest_frame(item)
            docs['frames'].append(to_doc(f'{item.elapsed:.1f}', run_id, item.model_dump(mode='json')))
        for sig in new:
            payload = sig.model_dump(mode='json')
            signals[f"{sig.signal_id}@r{sig.revision}"] = payload
            if sig.type == SignalType.CORRECTION:
                for sid in filter(None, str(sig.facts.get('removed_signals') or '').split(',')):
                    if sid.startswith('correction:'): continue
                    coordinator.retracted.add(sid)
                    if sid in insights:
                        insights[sid] = Insight.model_validate({**insights[sid], 'stage':'retracted',
                            'fact_check':{'status':'blocked','actions':['withdrawn: superseded by a correction']}}).model_dump(mode='json')
                    if sid in overlays:
                        overlays[sid] = {**overlays[sid], 'stage':'retracted'}
            if coordinator.decide(sig).plan == Plan.SKIP: continue
            pack = signal_pack(engine,sig)
            head, body = templates.data_text(pack)
            previous = insights.get(sig.signal_id)
            insight = build_insight(sig, stage='data', headline=head, explanation=body,
                key_numbers=templates.key_numbers(pack), variants=data_variants(pack),
                report=FactCheckReport(status=FactCheckStatus.DATA_ONLY))
            if previous: insight.supersedes_revision = previous['revision']
            insights[sig.signal_id] = insight.model_dump(mode='json')
            if sig.priority <= 4:
                overlay = build_overlay(templates.overlay_plan(pack),insight)
                if sig.team_id:
                    overlay = overlay.model_copy(update={'accent_colour':match.meta.team(sig.team_id).colour})
                placed = scheduler.place(overlay,now=engine.c.elapsed)
                for oid,duration in scheduler.take_trimmed().items():
                    if oid in overlays and oid != overlay.overlay_id:
                        overlays[oid] = {**overlays[oid], 'duration_s':duration}
                if placed: overlays[placed.overlay_id] = placed.model_dump(mode='json')
    state = engine.state()
    if state['status'] != 'full_time' or state['score'] != entry['final_score']:
        raise ValueError(f'{match.meta.match_id}: invalid final state')
    if len(match.events) != entry['events'] or len(match.tracking) != entry['tracking_frames']:
        raise ValueError('feed counts mismatch')
    docs['signals'] = [to_doc(k,run_id,v) for k,v in signals.items()]
    docs['insights'] = [to_doc(k,run_id,v) for k,v in insights.items()]
    docs['overlays'] = [to_doc(k,run_id,v) for k,v in overlays.items()]
    stamp = match.meta.kickoff_utc
    run = {'doc_type':'run', 'match_id':match.meta.match_id, 'seed':match.seed, 'storyline':match.meta.storyline_title,
           'status':'finished', 'speed':1.0, 'paused':False, 'position_elapsed':state['elapsed'],
           'minute_label':state['minute_label'], 'score':state['score'], 'state':state, 'match_status':state['status'],
           'created_at':stamp, 'updated_at':stamp, 'finished_at':stamp, 'resumes':0,
           'teams':[{'team_id':t.team_id,'short_name':t.short_name} for t in (match.meta.home,match.meta.away)],
           'synthetic':True, 'source':'synthetic-season', 'synthetic_schema':VERSION, 'seed_schema':SEED_SCHEMA, 'season':'fictional-2026', 'calibration':'2024-25 domestic aggregates; 2023-24 validation priors', 'synthetic_manifest_sha256':manifest_hash}
    docs['runs'] = [to_doc(run_id,run_id,run), to_doc('meta',run_id,{'doc_type':'meta','meta':match.meta.model_dump(mode='json')})]
    docs['runs'] += [to_doc(f'stats:{phase}',run_id,value) for phase,value in stats.items()]
    return run_id, docs


async def seed_dataset(store: Store, folder: Path, manifest: dict | None = None) -> dict:
    """Idempotent upserts with readback validation and per-run completion marker last."""
    manifest = manifest if manifest is not None else inspect_dataset(folder)
    mh = digest(folder/'manifest.json')
    counts = {}
    for entry in manifest['matches']:
        run_id, docs = build_documents(folder,entry,mh)
        marker = await store.read('runs',run_id,'synthetic-complete')
        expected_counts = {k:len(v) for k,v in docs.items()}
        marker_doc = to_doc('synthetic-complete',run_id,{'doc_type':'synthetic_complete', 'manifest_sha256':mh,
                             'counts':expected_counts, 'seed_schema':SEED_SCHEMA})
        if marker is not None and from_doc(marker) != from_doc(marker_doc):
            raise ValueError('existing completion marker conflicts with expected manifest or counts')
        # Completed archives are verified without modification. A partial import
        # repeats deterministic upserts, writing run metadata after the feed.
        if marker is None:
            for container in ('events', 'frames', 'signals', 'insights', 'overlays', 'runs'):
                rows = docs[container]
                for i in range(0,len(rows),100):
                    await store.upsert_many(container,run_id,rows[i:i+100])
        # Seed-owned documents are exact; later fan interactions and their
        # agent-run records are outside the seed's ownership.
        for container in ('runs','events','frames','signals','insights','overlays','decisions'):
            rows = docs.get(container,[])
            expected = {d['id']:from_doc(d) for d in rows}
            if len(expected) != len(rows):
                raise RuntimeError(f'duplicate expected IDs in {container}')
            if container == 'runs' and marker is not None:
                expected['synthetic-complete'] = from_doc(marker_doc)
            actual = await store.query(container,run_id)
            lookup = {d['id']:from_doc(d) for d in actual}
            if len(lookup) != len(actual) or lookup.keys() != expected.keys():
                raise RuntimeError(f'verification failed for {container}: unexpected or missing document IDs')
            for doc_id, payload in expected.items():
                if lookup[doc_id] != payload:
                    raise RuntimeError(f'verification failed for {container}/{doc_id}')
        if marker is not None:
            counts[run_id] = expected_counts
            continue
        await store.upsert_many('runs',run_id,[marker_doc])
        saved = await store.read('runs',run_id,'synthetic-complete')
        if saved is None or from_doc(saved) != from_doc(marker_doc):
            raise RuntimeError('completion marker verification failed')
        counts[run_id] = {k:len(v) for k,v in docs.items()}
    return counts


async def _apply(args, manifest):
    from matchlens.store.cosmos import CosmosStore
    store = CosmosStore(StoreSettings(kind='cosmos', endpoint=args.endpoint, database=args.database, key=EMULATOR_KEY))
    try:
        await store.open()
        for name in CONTAINERS:
            await store._containers[name].read()
        result = await seed_dataset(store,args.dataset,manifest)
        print(f'Imported and verified {len(result)} synthetic archives')
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--database',required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    u = urlparse(args.endpoint)
    if u.scheme not in ('http','https') or u.hostname not in ('localhost','127.0.0.1') or u.username or u.password or not u.port:
        parser.error('explicit localhost emulator endpoint and port required')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.database):
        parser.error('invalid database name')
    manifest = inspect_dataset(args.dataset)
    print(f"Preview: {len(manifest['matches'])} synthetic archives; target {args.endpoint}, database {args.database}; apply={args.apply}")
    if args.apply: asyncio.run(_apply(args,manifest))

if __name__ == '__main__':
    main()
