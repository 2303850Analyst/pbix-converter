from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .container import UnsafeBundleError
from .pipeline import ConversionCancelled, OutputFailure, generate_documentation


def _emit(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(message, file=stream, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-compass-doc",
        description="Local Data Compass / Superset ZIP to DOCX documentation generator",
    )
    parser.add_argument("input", nargs="?", type=Path, help="Input export .zip")
    parser.add_argument("output", nargs="?", type=Path, help="Output .docx")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input is None and args.output is None:
        from .gui import launch_gui

        return launch_gui()
    if args.input is None or args.output is None:
        _emit("Ошибка: укажите входной ZIP и выходной DOCX", error=True)
        return 1
    try:
        result = generate_documentation(args.input, args.output, _emit)
    except OutputFailure as error:
        _emit(f"Ошибка сохранения: {error}", error=True)
        return 3
    except ConversionCancelled:
        _emit("Операция отменена", error=True)
        return 1
    except (UnsafeBundleError, FileNotFoundError, ValueError, OSError) as error:
        _emit(f"Ошибка входного файла: {error}", error=True)
        return 1
    _emit(f"Статус: {result.status}")
    return 0 if result.status == "OK" else 2
