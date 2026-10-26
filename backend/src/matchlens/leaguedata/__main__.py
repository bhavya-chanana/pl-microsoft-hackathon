"""Command line for the league dataset: ``python -m matchlens.leaguedata <command>``.

    fetch      download raw files from the selected sources (resumable, cached)
    build      normalise raw files into Parquet tables            (leaguedata.build)
    curate     version the curated dataset                        (leaguedata.curate)
    reason     run the match reasoner                             (leaguedata.reasoner)
    playbooks  aggregate reasonings into team playbooks           (leaguedata.playbook)
    export     write the runtime knowledge bundle                 (leaguedata.kb)
    status     show what is on disk

Shared options: ``--leagues EPL,LA_LIGA``, ``--seasons 2023-24,2024-25``,
``--sources understat,football_data``, ``--limit N`` (cap per league-season, for
smoke tests), ``--refresh`` (re-download), ``--data-dir`` (default
``MATCHLENS_LEAGUEDATA_DIR`` or backend/data/local/leaguedata).

A source that cannot be fetched (missing module, network error, not
implemented) is logged and skipped; the others still run. The exit code is 1 if
any source failed.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from matchlens.leaguedata.config import Config
from matchlens.leaguedata.http import FetchError
from matchlens.leaguedata.schema import DEFAULT_LEAGUES, SEASONS, TABLES, League
from matchlens.leaguedata.sources import SOURCES

log = logging.getLogger("matchlens.leaguedata")

# command -> module whose ``main(cfg, args)`` does the work (owned by other modules).
STAGES = {
    "build": "matchlens.leaguedata.build",
    "curate": "matchlens.leaguedata.curate",
    "reason": "matchlens.leaguedata.reasoner",
    "playbooks": "matchlens.leaguedata.playbook",
    "export": "matchlens.leaguedata.kb",
}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _leagues(value: str) -> list[League]:
    try:
        return [League(name.upper()) for name in _csv(value)]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{exc}; choose from {', '.join(m.value for m in League)}") from exc


def _seasons(value: str) -> list[str]:
    seasons = _csv(value)
    for s in seasons:
        if len(s) != 7 or s[4] != "-" or not (s[:4] + s[5:]).isdigit():
            raise argparse.ArgumentTypeError(f"season must look like 2023-24, got {s!r}")
    return seasons


def _sources(value: str) -> list[str]:
    names = _csv(value)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown source(s) {unknown}; choose from {', '.join(SOURCES)}")
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m matchlens.leaguedata", description=__doc__.split("\n")[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (("fetch", "download raw data"), ("build", "build normalised tables"),
                           ("curate", "build the curated dataset"), ("reason", "reason over matches"),
                           ("playbooks", "build team playbooks"), ("export", "export the knowledge bundle"),
                           ("status", "show what is on disk")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--leagues", type=_leagues, default=list(DEFAULT_LEAGUES),
                       help="comma-separated, default " + ",".join(m.value for m in DEFAULT_LEAGUES))
        p.add_argument("--seasons", type=_seasons, default=list(SEASONS),
                       help="comma-separated, default " + ",".join(SEASONS))
        p.add_argument("--sources", type=_sources, default=list(SOURCES),
                       help="comma-separated, default all: " + ",".join(SOURCES))
        p.add_argument("--limit", type=int, default=None, help="cap per league-season (smoke tests)")
        p.add_argument("--refresh", action="store_true", help="re-download files already on disk")
        p.add_argument("--data-dir", type=Path, default=None, help="override the data folder")
    return parser


def run_fetch(cfg: Config, args: argparse.Namespace) -> int:
    """Fetch every selected source; returns the number of sources that failed."""
    failed = 0
    for name in args.sources:
        try:
            module = importlib.import_module(SOURCES[name])
            new = module.fetch(cfg, args.leagues, args.seasons, limit=args.limit, refresh=args.refresh)
        except (ImportError, FetchError, NotImplementedError) as exc:
            failed += 1
            log.error("source %s skipped: %s: %s", name, type(exc).__name__, exc)
        except Exception:  # a bug in one adapter must not stop the others
            failed += 1
            log.exception("source %s crashed", name)
        else:
            log.info("source %s: %d new files", name, new)
            print(f"{name}: {new} new files")
    return failed


def run_stage(command: str, cfg: Config, args: argparse.Namespace) -> int:
    path = STAGES[command]
    try:
        module = importlib.import_module(path)
    except ModuleNotFoundError as exc:
        if exc.name and path.startswith(exc.name):
            print(f"'{command}' is not available: module {path} does not exist yet", file=sys.stderr)
            return 2
        raise
    result = module.main(cfg, args)
    return result if isinstance(result, int) else 0


def _row_count(path: Path) -> int | None:
    if path.suffix == ".jsonl":
        with path.open("rb") as f:
            return sum(1 for _ in f)
    if path.suffix == ".parquet":
        try:
            import duckdb
        except ImportError:
            return None
        return duckdb.sql(f"select count(*) from read_parquet('{path}')").fetchone()[0]
    return None


def run_status(cfg: Config, args: argparse.Namespace) -> int:
    print(f"data folder: {cfg.root}")
    raw = cfg.root / "raw"
    print("raw files:")
    for name in SOURCES:
        folder = raw / name
        files = [p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")] if folder.is_dir() else []
        size = sum(p.stat().st_size for p in files) / 1e6
        print(f"  {name:<14}{len(files):>7} files {size:>9.1f} MB")
    print("tables:")
    shown = False
    for table in TABLES:
        for path in sorted(cfg.tables_dir.glob(f"{table}.*")):
            count = _row_count(path)
            print(f"  {table:<18}{'?' if count is None else count:>10} rows  ({path.name})")
            shown = True
    if not shown:
        print("  none yet (run: build)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if args.data_dir is not None:
        cfg = Config(root=args.data_dir, user_agent=cfg.user_agent, host_delay=cfg.host_delay)
    if args.command == "fetch":
        return 1 if run_fetch(cfg, args) else 0
    if args.command == "status":
        return run_status(cfg, args)
    return run_stage(args.command, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
