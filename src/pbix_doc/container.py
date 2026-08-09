from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


class PbixError(RuntimeError):
    pass


class UnsupportedPbixError(PbixError):
    pass


def normalize_part_name(name: str) -> str:
    return name.replace("\\", "/").strip("/").casefold()


class PbixContainer:
    def __init__(self, path: Path, max_json_bytes: int = 64 * 1024 * 1024) -> None:
        self.path = path
        self.max_json_bytes = max_json_bytes
        self._archive: zipfile.ZipFile | None = None
        self._parts: dict[str, str] = {}

    def __enter__(self) -> "PbixContainer":
        if not zipfile.is_zipfile(self.path):
            raise UnsupportedPbixError(
                "Файл не является читаемым PBIX-контейнером. Возможны шифрование, защита или повреждение."
            )
        self._archive = zipfile.ZipFile(self.path, "r")
        for info in self._archive.infolist():
            normalized = normalize_part_name(info.filename)
            if normalized and normalized not in self._parts:
                self._parts[normalized] = info.filename
        return self

    def __exit__(self, *_: object) -> None:
        if self._archive:
            self._archive.close()

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self._parts)

    def original_name(self, normalized_name: str) -> str:
        return self._parts[normalize_part_name(normalized_name)]

    def find_ending(self, suffix: str) -> list[str]:
        normalized = normalize_part_name(suffix)
        return sorted(name for name in self._parts if name.endswith(normalized))

    def find_containing(self, fragment: str) -> list[str]:
        normalized = normalize_part_name(fragment)
        return sorted(name for name in self._parts if normalized in name)

    def read_bytes(self, normalized_name: str) -> bytes:
        if not self._archive:
            raise RuntimeError("PBIX container is not open")
        key = normalize_part_name(normalized_name)
        original = self._parts.get(key)
        if original is None:
            raise KeyError(normalized_name)
        info = self._archive.getinfo(original)
        if info.file_size > self.max_json_bytes:
            raise PbixError(f"Метаданные слишком велики: {original} ({info.file_size} bytes)")
        return self._archive.read(original)

    def read_text(self, normalized_name: str) -> str:
        raw = self.read_bytes(normalized_name)
        for encoding in self._candidate_encodings(raw):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise PbixError(f"Не удалось определить кодировку: {normalized_name}")

    def read_json(self, normalized_name: str) -> Any:
        try:
            return json.loads(self.read_text(normalized_name))
        except json.JSONDecodeError as error:
            raise PbixError(f"Некорректный JSON в {normalized_name}: {error}") from error

    @staticmethod
    def _candidate_encodings(raw: bytes) -> tuple[str, ...]:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return ("utf-16", "utf-8-sig")
        if raw.startswith(b"\xef\xbb\xbf"):
            return ("utf-8-sig", "utf-16-le")
        if raw[:128].count(b"\x00") > 8:
            return ("utf-16-le", "utf-8-sig")
        return ("utf-8-sig", "utf-16-le")
