from __future__ import annotations

import ctypes
import sys
from collections.abc import Sequence

from .runtime import RuntimePolicyViolation, install_runtime_guards


def verify_bundled_dependencies() -> None:
    import apsw
    import numpy
    import pandas
    import pbixray  # noqa: F401
    import tkinter
    import xmhuffman  # noqa: F401
    from docx import Document
    from importlib.metadata import version
    from xpress8 import Xpress8
    from xpress9 import Xpress9

    connection = apsw.Connection(":memory:")
    try:
        if tuple(connection.execute("SELECT 1")) != ((1,),):
            raise RuntimeError("APSW self-check failed")
    finally:
        connection.close()
    if numpy.asarray([1]).item() != 1 or pandas.DataFrame({"value": [1]}).iat[0, 0] != 1:
        raise RuntimeError("Data stack self-check failed")
    Xpress8()
    Xpress9()
    Document()
    if not tkinter.Tcl().eval("info patchlevel"):
        raise RuntimeError("Tcl/Tk self-check failed")
    version("pbixray")


def _show_error(title: str, message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    install_runtime_guards()
    try:
        verify_bundled_dependencies()
    except RuntimePolicyViolation:
        if args != ["--self-check"]:
            _show_error("PBIX Converter", "Операция заблокирована политикой безопасности VDI.")
        return 4
    except Exception:
        if args != ["--self-check"]:
            _show_error(
                "PBIX Converter",
                "Пакет приложения повреждён или распакован не полностью. "
                "Скачайте ZIP повторно и извлеките все файлы.",
            )
        return 5
    if args == ["--self-check"]:
        return 0

    try:
        from .cli import main as cli_main

        return cli_main(args)
    except RuntimePolicyViolation:
        _show_error("PBIX Converter", "Операция заблокирована политикой безопасности VDI.")
        return 4
