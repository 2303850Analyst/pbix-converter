from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from . import __version__
from .gui import launch_gui
from .pipeline import Options, generate_documentation
from .renderer import overall_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbix-doc", description="Local PBIX to TXT documentation generator")
    parser.add_argument("input", nargs="?", type=Path, help="Input .pbix file")
    parser.add_argument("output", nargs="?", type=Path, help="Output .txt file")
    parser.add_argument("--examples", type=int, default=5, help="Maximum categorical examples per column")
    parser.add_argument("--no-examples", action="store_true", help="Disable column value profiling")
    parser.add_argument("--temp-dir", type=Path, help="Local temporary directory")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    limit = 0 if args.no_examples else max(0, args.examples)
    if args.input is None and args.output is None:
        return launch_gui(limit)
    if args.input is None or args.output is None:
        raise SystemExit("Для CLI необходимо указать и входной PBIX, и выходной TXT")

    model = generate_documentation(
        args.input,
        args.output,
        Options(limit, args.temp_dir),
        lambda message: print(message, flush=True),
    )
    status = overall_status(model)
    print(f"Статус: {status}")
    return 0 if status == "OK" else 2
