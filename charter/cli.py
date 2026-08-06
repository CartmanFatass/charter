"""Argument parsing and dispatch for the ``charter`` CLI."""
from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="charter",
        description="Personas, workspaces and memory for Claude Code agents across many repos.",
    )
    p.add_argument("--version", action="version", version=f"charter {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
