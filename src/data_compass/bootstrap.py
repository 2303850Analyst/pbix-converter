from __future__ import annotations

from collections.abc import Sequence

from pbix_doc.runtime import install_runtime_guards


def main(argv: Sequence[str] | None = None) -> int:
    install_runtime_guards()
    from .cli import main as cli_main

    return cli_main(argv)
