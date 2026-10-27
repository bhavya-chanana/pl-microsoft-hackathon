"""``python -m matchlens.tacticsml.train {shards,fit,export,evaluate}``."""

from __future__ import annotations

import argparse
import importlib
import sys


def _lazy(module: str, argv: list[str]) -> int:
    try:
        mod = importlib.import_module(f"matchlens.tacticsml.train.{module}")
    except ModuleNotFoundError as exc:
        if exc.name != f"matchlens.tacticsml.train.{module}":
            raise
        print(f"train.{module} is not available yet (written by another work unit): {exc}", file=sys.stderr)
        return 2
    return int(mod.main(argv) or 0)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train", description=__doc__)
    parser.add_argument("command", choices=("shards", "fit", "export", "evaluate"))
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    cmd, rest = argv[0], argv[1:]
    parser.parse_args([cmd])
    if cmd == "fit":
        from matchlens.tacticsml.train import fit  # imports torch (tactics-ml group)

        fp = argparse.ArgumentParser(prog="python -m matchlens.tacticsml.train fit")
        fit.add_arguments(fp)
        return fit.main(fp.parse_args(rest))
    return _lazy({"shards": "build", "export": "export", "evaluate": "evaluate"}[cmd], rest)


if __name__ == "__main__":
    raise SystemExit(main())
