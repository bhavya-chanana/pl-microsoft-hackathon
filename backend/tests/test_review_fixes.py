"""Guards added after the Tactics Brain code review."""

from __future__ import annotations

import pytest

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError, fetch_bytes
from matchlens.tacticsml import registry


def test_cache_path_cannot_escape_the_data_folder(tmp_path):
    cfg = Config(root=tmp_path / "data")
    escape = cfg.raw_dir("understat") / ".." / ".." / ".." / "evil.json"
    with pytest.raises(FetchError, match="outside"):
        fetch_bytes("https://example.invalid/x", escape, cfg)


def test_unloadable_weights_fall_back_to_the_baseline(monkeypatch):
    from matchlens.tacticsml import learned

    def boom(*_a, **_k):
        raise ValueError("Object arrays cannot be loaded when allow_pickle=False")

    monkeypatch.setenv("MATCHLENS_EVALUATOR", "learned")
    monkeypatch.setattr(learned, "weights_available", lambda *a, **k: True)
    monkeypatch.setattr(learned.LearnedModel, "load", classmethod(boom))
    registry.get_tactical_model.cache_clear()
    try:
        model = registry.get_tactical_model()
    finally:
        registry.get_tactical_model.cache_clear()
    assert model.version.startswith("baseline")
