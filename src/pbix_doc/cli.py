from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .gui import launch_gui
from .pipeline import MAX_EXAMPLE_VALUES, Options, generate_documentation
from .renderer import overall_status
from .runtime import RuntimePolicyViolation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbix-doc", description="Local PBIX to TXT documentation generator")
    parser.add_argument("input", nargs="?", type=Path, help="Input .pbix file")
    parser.add_argument("output", nargs="?", type=Path, help="Output .txt file")
    parser.add_argument("--examples", type=int, default=5, help="Maximum categorical examples per column")
    parser.add_argument("--no-examples", action="store_true", help="Disable column value profiling")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    limit = 0 if args.no_examples else min(MAX_EXAMPLE_VALUES, max(0, args.examples))
    if args.input is None and args.output is None:
        return launch_gui(limit)
    if args.input is None or args.output is None:
        raise SystemExit("Для CLI необходимо указать и входной PBIX, и выходной TXT")

    try:
        model = generate_documentation(
            args.input,
            args.output,
            Options(limit),
            lambda message: print(message, flush=True),
        )
    except RuntimePolicyViolation as error:
        print(f"Критическая ошибка политики VDI: {error}", file=sys.stderr)
        return 4
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    status = overall_status(model)
    print(f"Статус: {status}")
    return 0 if status == "OK" else 2
