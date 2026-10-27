"""Training-data sources. Each module exposes ``iter_samples(...)`` and ``build_shards(...)``.

``iter_samples`` yields ``(TFrame, Labels, meta)`` (see ``_common``); ``limit`` caps the number of matches.
"""

from __future__ import annotations

from importlib import import_module

SOURCES = ("synthetic", "statsbomb360", "skillcorner", "sportec", "pff", "metrica")


def load(name: str):
    """Import a source module by name (so torch-free callers do not import kloppy needlessly)."""
    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}; choose from {', '.join(SOURCES)}")
    return import_module(f"{__name__}.{name}")
