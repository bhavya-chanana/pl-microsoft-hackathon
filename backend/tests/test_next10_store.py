"""The next10 store keeps away from the deployed app's database, and Azure is seeded only after the emulator."""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from matchlens.store import base
from matchlens.store.next10 import (
    CONTAINERS,
    Next10Settings,
    Next10Store,
    bundle_hash,
    expected_counts,
    load_bundle,
    seed,
)

ROOT = Path(__file__).resolve().parents[2]


def write_bundle(folder: Path, tag: str = "a") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    matches = [{"id": "fx-1", "match_id": "fx-1", "home": "Westmoor", "away": "Eastbrook", "tag": tag}]
    preds = [{"id": f"p{i}", "match_id": "fx-1", "t_issued": i * 30, "horizon_s": 60, "probs": {"home_shot": 0.1}}
             for i in range(150)]
    (folder / "matches.jsonl").write_text("\n".join(json.dumps(d) for d in matches))
    (folder / "predictions.jsonl").write_text("\n".join(json.dumps(d) for d in preds))
    return folder


def test_app_database_is_refused():
    with pytest.raises(ValueError, match="deployed app"):
        Next10Settings(database="matchlens")
    with pytest.raises(ValueError):
        Next10Settings(database=" MatchLens ")


def test_default_database_is_not_the_apps(monkeypatch):
    # Same account is fine; the database is what isolates the data.
    monkeypatch.delenv("MATCHLENS_COSMOS_DATABASE", raising=False)
    assert Next10Settings().database != base.StoreSettings.from_env().database


def test_bundle_validation(tmp_path):
    folder = write_bundle(tmp_path / "b")
    bundle = load_bundle(folder)
    assert expected_counts(bundle) == {"matches": {"fx-1": 1}, "predictions": {"fx-1": 150}}
    (folder / "nope.jsonl").write_text("{}")
    with pytest.raises(ValueError, match="unknown container"):
        load_bundle(folder)
    (folder / "nope.jsonl").unlink()
    (folder / "events.jsonl").write_text(json.dumps({"id": "e1"}))
    with pytest.raises(ValueError, match="partition"):
        load_bundle(folder)


def test_azure_is_refused_without_a_matching_emulator_check(tmp_path):
    folder = write_bundle(tmp_path / "b")
    azure = Next10Settings(endpoint="https://example.documents.azure.com:443/")
    with pytest.raises(PermissionError, match="emulator"):
        asyncio.run(seed(folder, azure, checks_dir=tmp_path / "checks", confirm_azure=True))
    # A check recorded for a different bundle does not count.
    (tmp_path / "checks").mkdir()
    (tmp_path / "checks" / f"{bundle_hash(folder)}.json").write_text(json.dumps(
        {"endpoint": "http://localhost:8091", "counts": {"matches": {"fx-1": 2}}}))
    with pytest.raises(PermissionError, match="does not match"):
        asyncio.run(seed(folder, azure, checks_dir=tmp_path / "checks", confirm_azure=True))


def test_bicep_declares_every_next10_container():
    bicep = (ROOT / "infra" / "next10-cosmos.bicep").read_text()
    declared = {name: (part, tuple(re.findall(r"'(\w+)'", unindexed)), vec or None)
                for name, part, unindexed, vec in re.findall(
                    r"\{ name: '(\w+)', partition: '(\w+)', unindexed: \[(.*?)\], vector: '(\w*)'", bicep)}
    assert declared == {c.name: (c.partition, c.unindexed, c.vector[0] if c.vector else None)
                        for c in CONTAINERS.values()}
    assert "existing" in bicep and "'matchlens_next10'" in bicep
    assert "next10" not in (ROOT / "infra" / "main.bicep").read_text()  # azd up never deploys it


@pytest.mark.skipif(not os.getenv("MATCHLENS_NEXT10_TEST_ENDPOINT"), reason="needs the next10 Cosmos emulator")
def test_emulator_seed_records_the_check_that_unlocks_azure(tmp_path):
    folder = write_bundle(tmp_path / "b", tag=uuid.uuid4().hex)
    settings = Next10Settings(endpoint=os.environ["MATCHLENS_NEXT10_TEST_ENDPOINT"],
                              database=f"next10_test_{uuid.uuid4().hex[:8]}", key=base.EMULATOR_KEY)
    checks = tmp_path / "checks"
    result = asyncio.run(seed(folder, settings, checks_dir=checks))
    assert result["written"] == {"matches": 1, "predictions": 150}
    assert (checks / f"{result['bundle']}.json").exists()

    async def cleanup() -> None:
        async with Next10Store(settings) as store:
            await store._client.delete_database(settings.database)
    asyncio.run(cleanup())

    # The recorded check now lets the same bundle through the gate (Azure itself is not contacted here).
    from matchlens.store.next10 import require_emulator_check
    require_emulator_check(result["bundle"], expected_counts(load_bundle(folder)), checks)
