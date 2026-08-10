from __future__ import annotations

import sys
from collections.abc import Sequence

from .runtime import RuntimePolicyViolation, install_runtime_guards


def main(argv: Sequence[str] | None = None) -> int:
    install_runtime_guards()
    try:
        from .cli import main as cli_main

        return cli_main(argv)
    except RuntimePolicyViolation as error:
        print(f"Критическая ошибка политики VDI: {error}", file=sys.stderr)
        return 4
