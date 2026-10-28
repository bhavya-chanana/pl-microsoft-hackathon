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


# --- explicit Azure target (no network) ---------------------------------------------------

AZ = 'https://cosmos-7npbct7a7fan6.documents.azure.com:443/'


def _argv(endpoint, *extra, db='matchlens'):
    return ['--dataset', 'unused', '--endpoint', endpoint, '--database', db, *extra]


def _run_main(monkeypatch, argv):
    """main() with the dataset check and the apply step stubbed; returns True when _apply was reached."""
    called = []
    monkeypatch.setattr(seed, 'inspect_dataset', lambda _: {'matches': []})
    async def fake_apply(args, manifest): called.append(args)
    monkeypatch.setattr(seed, '_apply', fake_apply)
    seed.main(argv)
    return called


def test_localhost_without_azure_still_works(monkeypatch):
    assert _run_main(monkeypatch, _argv('http://127.0.0.1:8081', db='matchlens_codex')) == []
    (args,) = _run_main(monkeypatch, _argv('http://localhost:8081', '--apply'))
    assert not args.azure and seed.store_settings(args).key == seed.EMULATOR_KEY


@pytest.mark.parametrize('argv', [
    _argv(AZ),                                                    # azure host, no --azure
    _argv(AZ, '--apply'),
    _argv('https://cosmos-7npbct7a7fan6.documents.azure.com/', '--apply'),
    _argv('http://127.0.0.1:8081', '--confirm-azure', 'x'),       # confirm without --azure
    _argv('http://127.0.0.1', '--azure'),                         # localhost is not an Azure target
    _argv('http://127.0.0.1:8081', '--azure'),
    _argv('http://cosmos-7npbct7a7fan6.documents.azure.com:443/', '--azure'),          # http
    _argv('https://evil.example.com/', '--azure'),                                    # non-Azure host
    _argv('https://cosmos-7npbct7a7fan6.documents.azure.com.evil.com/', '--azure'),
    _argv('https://x.documents.azure.com@evil.com/', '--azure'),
    _argv('https://user:pw@cosmos-7npbct7a7fan6.documents.azure.com:443/', '--azure'),  # credentials
    _argv('https://cosmos-7npbct7a7fan6.documents.azure.com:8081/', '--azure'),
    _argv('https://cosmos-7npbct7a7fan6.documents.azure.com/dbs?x=1', '--azure'),
    _argv(AZ, '--azure', '--apply'),                                                  # missing confirmation
    _argv(AZ, '--azure', '--apply', '--confirm-azure', 'cosmos-other'),                # mismatch
    _argv(AZ, '--azure', '--apply', '--confirm-azure', 'COSMOS-7NPBCT7A7FAN6'),
    _argv(AZ, '--azure', '--confirm-azure', 'cosmos-other'),                           # mismatch even on preview
])
def test_target_rejected(monkeypatch, argv):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2


def test_azure_accepted_with_confirmation(monkeypatch):
    assert _run_main(monkeypatch, _argv(AZ, '--azure')) == []                  # preview needs no confirmation
    assert _run_main(monkeypatch, _argv('https://cosmos-7npbct7a7fan6.documents.azure.com/', '--azure')) == []
    (args,) = _run_main(monkeypatch, _argv(AZ, '--azure', '--apply', '--confirm-azure', 'cosmos-7npbct7a7fan6'))
    assert seed.store_settings(args).key is None


class _FakeContainer:
    def __init__(self, name, log, status=None): self.name, self.log, self.status = name, log, status
    async def read(self):
        self.log.append(('read', self.name))
        if self.status:
            from azure.cosmos.exceptions import CosmosHttpResponseError
            raise CosmosHttpResponseError(status_code=self.status,
                message='Request blocked: principal [11111111-2222-3333-4444-555555555555] does not have RBAC permissions')


class _FakeDatabase:
    def __init__(self, log, status): self.log, self.status = log, status
    def get_container_client(self, name): return _FakeContainer(name, self.log, self.status)
    async def create_container_if_not_exists(self, **_): raise AssertionError('must not create containers')


class _FakeClient:
    instances = []
    status = None
    def __init__(self, endpoint, credential):
        self.endpoint, self.credential, self.log, self.closed = endpoint, credential, [], False
        _FakeClient.instances.append(self)
    def get_database_client(self, name):
        self.log.append(('database', name))
        return _FakeDatabase(self.log, _FakeClient.status)
    async def create_database_if_not_exists(self, *_a, **_k): raise AssertionError('must not create a database')
    async def close(self): self.closed = True


class _FakeCredential:
    instances = []
    def __init__(self, **kwargs): self.kwargs, self.closed = kwargs, False; _FakeCredential.instances.append(self)
    async def close(self): self.closed = True
    async def get_token(self, *_scopes): raise AssertionError('no token needed here')


@pytest.fixture
def fake_azure(monkeypatch):
    import azure.identity.aio
    from matchlens.store import cosmos
    _FakeClient.instances, _FakeClient.status, _FakeCredential.instances = [], None, []
    monkeypatch.setattr(cosmos, 'CosmosClient', _FakeClient)
    monkeypatch.setattr(azure.identity.aio, 'DefaultAzureCredential', _FakeCredential)
    monkeypatch.setenv('MATCHLENS_COSMOS_KEY', 'must-not-be-used')
    return _FakeClient


def _azure_args():
    import argparse
    ns = argparse.Namespace(dataset=Path('unused'), endpoint=AZ, database='matchlens', apply=True, azure=True,
                            confirm_azure='cosmos-7npbct7a7fan6')
    return ns


def test_azure_apply_uses_entra_credential_and_never_creates(fake_azure, monkeypatch, tmp_path):
    from matchlens.store.base import CONTAINERS
    seen = []
    async def fake_seed(store, folder, manifest=None):
        seen.append(store.settings)
        return {'run-a': {}}
    monkeypatch.setattr(seed, 'seed_dataset', fake_seed)
    asyncio.run(seed._apply(_azure_args(), {'matches': []}))
    (client,) = fake_azure.instances
    assert client.endpoint == AZ
    assert isinstance(client.credential, _FakeCredential) and client.credential.closed and client.closed
    assert seen[0].key is None and seen[0].database == 'matchlens'
    read = [name for kind, name in client.log if kind == 'read']
    assert set(read) >= set(CONTAINERS)            # every existing container validated with .read()
    assert ('database', 'matchlens') in client.log


def test_azure_403_names_the_role_and_principal(fake_azure, monkeypatch):
    fake_azure.status = 403
    async def boom(*_a, **_k): raise AssertionError('seeding must not start')
    monkeypatch.setattr(seed, 'seed_dataset', boom)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(seed._apply(_azure_args(), {'matches': []}))
    text = str(exc.value)
    assert 'Built-in Data Contributor' in text and 'cosmos-7npbct7a7fan6' in text
    assert '11111111-2222-3333-4444-555555555555' in text
    assert fake_azure.instances[0].closed


def test_emulator_apply_still_uses_emulator_key(monkeypatch):
    import argparse
    args = argparse.Namespace(endpoint='http://127.0.0.1:8081', database='d', azure=False)
    settings = seed.store_settings(args)
    assert settings.key == seed.EMULATOR_KEY and settings.endpoint == 'http://127.0.0.1:8081'
