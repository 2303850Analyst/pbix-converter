from __future__ import annotations

import ctypes
import sys
from collections.abc import Sequence

from pbix_doc.runtime import RuntimePolicyViolation, install_runtime_guards


def verify_bundled_dependencies() -> None:
    import tkinter

    import yaml
    from docx import Document

    if not yaml.safe_load("ready: true").get("ready"):
        raise RuntimeError("YAML self-check failed")
    Document()
    if not tkinter.Tcl().eval("info patchlevel"):
        raise RuntimeError("Tcl/Tk self-check failed")


def _show_error(title: str, message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    install_runtime_guards()
    try:
        verify_bundled_dependencies()
    except RuntimePolicyViolation:
        if args != ["--self-check"]:
            _show_error("Data Compass Converter", "Операция заблокирована политикой безопасности VDI.")
        return 4
    except Exception:
        if args != ["--self-check"]:
            _show_error("Data Compass Converter", "Файл приложения повреждён. Скачайте его повторно.")
        return 5
    if args == ["--self-check"]:
        return 0
    try:
        from .cli import main as cli_main

        return cli_main(args)
    except RuntimePolicyViolation:
        _show_error("Data Compass Converter", "Операция заблокирована политикой безопасности VDI.")
        return 4
