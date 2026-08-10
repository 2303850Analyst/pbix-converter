from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


WORKSPACE_PREFIX = ".pbix_doc_runtime_"
WORKSPACE_MARKER = ".pbix_doc_owner"
ATOMIC_PREFIX = ".pbix_doc_output_"

_guards_installed = False
_network_events = {
    "socket.__new__",
    "socket.bind",
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyaddr",
    "socket.gethostbyname",
    "socket.getservbyname",
    "socket.getservbyport",
    "socket.sendto",
    "urllib.Request",
}
_process_events = {
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.startfile",
    "os.system",
    "subprocess.Popen",
}
_atomic_name = re.compile(r"^\.pbix_doc_output_(\d+)_.*\.tmp$")


class RuntimePolicyViolation(BaseException):
    """Fatal policy violation that ordinary recovery must not suppress."""

    pass


class NetworkEgressViolation(RuntimePolicyViolation):
    pass


class ExternalProcessViolation(RuntimePolicyViolation):
    pass


class WorkspaceCleanupViolation(RuntimePolicyViolation):
    pass


def install_runtime_guards() -> None:
    global _guards_installed
    if _guards_installed:
        return
    sys.addaudithook(_audit_event)
    _guards_installed = True


def _audit_event(event: str, _: tuple[object, ...]) -> None:
    if event in _network_events:
        raise NetworkEgressViolation(f"Сетевая операция запрещена политикой VDI_OFFLINE_RUNTIME: {event}")
    if event in _process_events:
        raise ExternalProcessViolation(f"Запуск внешнего процесса запрещён политикой VDI_OFFLINE_RUNTIME: {event}")


@contextmanager
def managed_workspace(parent: Path) -> Iterator[Path]:
    parent = parent.resolve()
    if not parent.is_dir():
        raise FileNotFoundError(parent)
    cleanup_stale_artifacts(parent)
    workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=parent))
    try:
        marker = {"application": "pbix-doc", "pid": os.getpid()}
        workspace.joinpath(WORKSPACE_MARKER).write_text(json.dumps(marker), encoding="ascii")
        yield workspace
    finally:
        try:
            shutil.rmtree(workspace)
        except OSError as error:
            raise WorkspaceCleanupViolation(
                f"Не удалось удалить временную рабочую папку: {workspace.name}"
            ) from error
        if workspace.exists():
            raise WorkspaceCleanupViolation(
                f"Временная рабочая папка осталась после удаления: {workspace.name}"
            )


def cleanup_stale_artifacts(parent: Path) -> None:
    parent = parent.resolve()
    if not parent.is_dir():
        return
    for candidate in parent.glob(f"{WORKSPACE_PREFIX}*"):
        _remove_stale_workspace(parent, candidate)
    for candidate in parent.glob(f"{ATOMIC_PREFIX}*.tmp"):
        match = _atomic_name.match(candidate.name)
        if match and not _process_exists(int(match.group(1))):
            try:
                candidate.unlink()
            except OSError:
                pass


def atomic_temp_prefix() -> str:
    return f"{ATOMIC_PREFIX}{os.getpid()}_"


def _remove_stale_workspace(parent: Path, candidate: Path) -> None:
    try:
        if not candidate.is_dir() or candidate.is_symlink():
            return
        if candidate.resolve().parent != parent:
            return
        marker = json.loads(candidate.joinpath(WORKSPACE_MARKER).read_text(encoding="ascii"))
        if marker.get("application") != "pbix-doc" or _process_exists(int(marker["pid"])):
            return
        shutil.rmtree(candidate)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
