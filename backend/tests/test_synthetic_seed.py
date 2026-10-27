import asyncio
import json
from pathlib import Path

import pytest

from matchlens.dataset.export import export_match, generate
from matchlens.domain.models import Event, MatchMeta, TrackingFrame
from matchlens.ingest.adapters import feed_order
from matchlens.interpret.engine import MatchEngine
from matchlens.dataset.season import digest
from matchlens.simulator.calibration import NEUTRAL
from matchlens.store.base import MemoryStore, to_doc
from matchlens.store import seed


@pytest.fixture(scope='module')
def single_match(tmp_path_factory):
    folder = tmp_path_factory.mktemp('season')
    match = generate(12, NEUTRAL, 'ml-season-1-01')
    entry = export_match(match, NEUTRAL, folder)
    (folder/'manifest.json').write_text('{"synthetic":true}')
    return folder, entry


class FailingStore(MemoryStore):
    fail = True
    async def upsert_many(self, container, run_id, docs):
        if self.fail and container == 'frames':
            self.fail = False
            raise RuntimeError('injected failure')
        await super().upsert_many(container, run_id, docs)


def test_seed_resume_and_idempotency(single_match, monkeypatch):
    folder, entry = single_match
    monkeypatch.setattr(seed, 'inspect_dataset', lambda _: {'matches':[entry]})
    async def check():
        store = FailingStore()
        await store.open()
        pointer = to_doc('active','_active',{'active_run_id':'existing'})
        await store.upsert_many('runs','_active',[pointer])
        with pytest.raises(RuntimeError, match='injected'):
            await seed.seed_dataset(store, folder)
        run_id = f"synthetic-v2-{entry['match_id']}-{digest(folder/'manifest.json')[:12]}"
        assert await store.read('runs',run_id,'synthetic-complete') is None
        first = await seed.seed_dataset(store, folder)
        snapshot = {k:dict(v) for k,v in store.items.items()}
        second = await seed.seed_dataset(store, folder)
        assert first == second
        assert store.items == snapshot
        assert await store.read('runs','_active','active') == pointer
        run = await store.read('runs',run_id,run_id)
        meta = await store.read('runs',run_id,'meta')
        assert run['status'] == 'finished' and run['score'] == entry['final_score']
        assert meta['meta']['match_id'] == entry['match_id']
        stored_stats = await store.read('runs',run_id,'stats:full_time')
        assert stored_stats
        engine = MatchEngine(MatchMeta.model_validate(meta['meta']))
        events = [Event.model_validate({k:v for k,v in d.items() if k not in ('id','run_id','minute_label','pass_quality')}) for d in await store.query('events',run_id,order_by='sequence')]
        frames = [TrackingFrame.model_validate({k:v for k,v in d.items() if k not in ('id','run_id')}) for d in await store.query('frames',run_id,order_by='elapsed')]
        for item in feed_order(events, frames):
            if isinstance(item, Event): engine.ingest_event(item)
            else: engine.ingest_frame(item)
        assert engine.state()['score'] == run['score']
        assert {t.team_id:engine.team_stats(t.team_id) for t in (engine.meta.home,engine.meta.away)} == stored_stats['teams']
        assert {pid:engine.player_stats(pid) for pid in engine.meta.players} == stored_stats['players']
        from matchlens.agents.contracts import FanProfile
        from matchlens.agents.services import personalise_feed
        from matchlens.runtime.manager import SessionManager
        from matchlens.store.repository import Repository
        manager = SessionManager()
        manager.attach(Repository(store, None))
        session = await manager.open_run(run_id)
        feed = personalise_feed(session, FanProfile())
        assert feed and all(item['stage'] == 'data' and item['fact_check'] == 'data_only' for item in feed)
        assert any(item['signal_type'] == 'goal' for item in feed)
        vote = to_doc('later-vote',run_id,{'kind':'rating','value':5})
        await store.upsert_many('fan_votes',run_id,[vote])
        decision = to_doc('later-decision',run_id,{'agent':'narrator','reason':'fan asked'})
        await store.upsert_many('decisions',run_id,[decision])
        await seed.seed_dataset(store,folder)
        assert await store.read('fan_votes',run_id,'later-vote') == vote
        assert await store.read('decisions',run_id,'later-decision') == decision
    asyncio.run(check())


def test_tampered_profile_rejected(tmp_path):
    (tmp_path/'profile.json').write_text('{}')
    (tmp_path/'manifest.json').write_text(json.dumps({'format':seed.VERSION, 'schema':seed.VERSION, 'source':'synthetic-season', 'season':'fictional-2026', 'calibration':'2024-25 domestic aggregates; 2023-24 validation priors', 'synthetic':True, 'profile_sha256':'wrong'}))
    with pytest.raises(ValueError, match='profile digest'):
        seed.inspect_dataset(tmp_path)


def test_profile_reads_source_without_changes(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    from matchlens.dataset.season import profile, season_plan
    tables = tmp_path/'tables'
    tables.mkdir()
    con = duckdb.connect()
    con.execute("""COPY (SELECT 'm1' match_key, 'EPL' league, '2024-25' season, 2 home_goals, 1 away_goals,
      '4-3-3' home_formation, '4-2-3-1' away_formation, NULL::DOUBLE home_xg, NULL::DOUBLE away_xg
      UNION ALL SELECT 'm2','EPL','2023-24',1,1,'4-3-3','4-3-3',1.0,1.0) TO ? (FORMAT PARQUET)""", [str(tables/'matches.parquet')])
    con.execute('COPY (SELECT \'m1\' match_key, \'goal\' kind, 22 AS "minute") TO ? (FORMAT PARQUET)', [str(tables/'match_events.parquet')])
    con.execute("""COPY (SELECT 'm2' match_key, 1.0 xg, 10 shots, 3 shots_on_target, 4 corners, 9 fouls)
      TO ? (FORMAT PARQUET)""", [str(tables/'team_match_stats.parquet')])
    before = {p.name:digest(p) for p in tables.iterdir()}
    result = profile(tmp_path)
    assert result['population']['matches'] == 1
    assert result['xg_2024_25_coverage'] == {'home':0, 'away':0}
    assert result['earlier_team_stats']['xg']['count'] == 1
    assert before == {p.name:digest(p) for p in tables.iterdir()}
    plan = season_plan(result, 123)
    assert len(plan) == 12
    for meta, story, fixture_seed, styles, target in plan:
        assert sorted(target.values()) == [1,2]
        assert len(story.beats) == 3 and all(16 <= b.minute <= 30 for b in story.beats)
        assert all(b.player_id.startswith(b.team_id + '-') for b in story.beats)
        assert story.scripted_goals_only
    assert [p[4] for p in season_plan(result,123)] == [p[4] for p in plan]
    result['score'].update(distribution={'0-0':1},home_mean=0,away_mean=0,home_win=0,draw=1,away_win=0)
    assert all(not item[1].beats for item in season_plan(result,123))
    result['population']['season'] = '2023-24'
    with pytest.raises(ValueError, match='cohort'):
        season_plan(result,123)


def test_reverify_rejects_extra_documents_and_corrupt_marker(single_match, monkeypatch):
    import copy
    folder, entry = single_match
    monkeypatch.setattr(seed, 'inspect_dataset', lambda _: {'matches':[entry]})
    async def check():
        store = MemoryStore()
        await store.open()
        await seed.seed_dataset(store, folder)
        clean = copy.deepcopy(store.items)
        run_id = f"synthetic-v2-{entry['match_id']}-{digest(folder/'manifest.json')[:12]}"
        for container in ('events', 'frames', 'signals', 'insights', 'runs'):
            store.items = copy.deepcopy(clean)
            if container in ('events', 'frames'):
                sample = next(doc for (rid,_),doc in store.items[container].items() if rid == run_id)
                extra = {**sample, 'id':'unexpected-extra'}
            else:
                extra = to_doc('unexpected-extra',run_id,{'unexpected':True})
            await store.upsert_many(container,run_id,[extra])
            with pytest.raises(RuntimeError,match=f'verification failed for {container}'):
                await seed.seed_dataset(store,folder)
        store.items = copy.deepcopy(clean)
        marker = await store.read('runs',run_id,'synthetic-complete')
        marker['counts']['events'] += 1
        await store.upsert_many('runs',run_id,[marker])
        with pytest.raises(ValueError,match='completion marker conflicts'):
            await seed.seed_dataset(store,folder)
        store.items = copy.deepcopy(clean)
        await seed.seed_dataset(store,folder)
        assert store.items == clean
    asyncio.run(check())


def test_data_insights_follow_correction_revisions(tmp_path):
    from matchlens.simulator.storyline import comeback_storyline
    story = comeback_storyline()
    entry = export_match(generate(7,story,'ml-season-correction-test'),story,tmp_path)
    _, docs = seed.build_documents(tmp_path,entry,'abc123')
    insights = [d for d in docs['insights']]
    assert any(i['stage'] == 'retracted' and i['signal_type'] == 'milestone' for i in insights)
    assert any(i['signal_type'] == 'goal' and i['revision'] == 1 and i['supersedes_revision'] == 0 for i in insights)
    assert all(i['fact_check']['status'] in ('data_only','blocked') and not i['agents'] for i in insights)
    assert docs['overlays']
