"""Where league data lives and how politely we fetch it.

Configuration (environment):
    MATCHLENS_LEAGUEDATA_DIR   data folder (default backend/data/local/leaguedata, gitignored)
    MATCHLENS_LEAGUEDATA_UA    User-Agent for downloads
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DIR = BACKEND_DIR / "data" / "local" / "leaguedata"

# Minimum seconds between requests to the same host.
HOST_DELAY: dict[str, float] = {
    "understat.com": 2.0,
    "www.football-data.co.uk": 1.0,
    "fantasy.premierleague.com": 1.0,
    "api.clubelo.com": 1.0,
    "query.wikidata.org": 1.0,
    "en.wikipedia.org": 0.5,
    "raw.githubusercontent.com": 0.2,
}
DEFAULT_DELAY = 1.0


@dataclass(frozen=True)
class Config:
    root: Path = DEFAULT_DIR
    user_agent: str = "matchlens-research/0.1 (hackathon; contact via repository)"
    host_delay: dict[str, float] = field(default_factory=lambda: dict(HOST_DELAY))

    @classmethod
    def from_env(cls) -> Config:
        root = os.getenv("MATCHLENS_LEAGUEDATA_DIR")
        ua = os.getenv("MATCHLENS_LEAGUEDATA_UA")
        return cls(root=Path(root) if root else DEFAULT_DIR, **({"user_agent": ua} if ua else {}))

    def raw_dir(self, source: str) -> Path:
        """Downloads exactly as received, one file per request."""
        return self.root / "raw" / source

    @property
    def tables_dir(self) -> Path:
        """Normalised Parquet tables (build.py)."""
        return self.root / "tables"

    @property
    def curated_dir(self) -> Path:
        """Versioned curated dataset (curate.py): curated/v<N>/."""
        return self.root / "curated"

    @property
    def reasoning_dir(self) -> Path:
        return self.root / "reasoning"

    @property
    def kb_dir(self) -> Path:
        """Runtime knowledge bundle (stdlib-readable JSON/JSONL) served to agents."""
        return self.root / "kb"

    @property
    def overrides_path(self) -> Path:
        """Hand-maintained identity fixes: {"club": {...}, "player": {...}, "manager": {...}}."""
        return self.root / "overrides.json"
