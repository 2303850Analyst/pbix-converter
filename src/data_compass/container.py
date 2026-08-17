from __future__ import annotations

import json
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, NodeEvent, ScalarEvent
from yaml.nodes import MappingNode


MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 2_000
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_YAML_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_NODES = 50_000
MAX_DEPTH = 50
MAX_STRING = 1_000_000
ALLOWED_FAMILIES = {"metadata.yaml", "databases", "datasets", "charts", "dashboards"}


class UnsafeBundleError(ValueError):
    pass


class StrictSafeLoader(yaml.SafeLoader):
    pass


def _mapping(loader: StrictSafeLoader, node: MappingNode, deep: bool = False) -> dict[object, object]:
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise UnsafeBundleError(f"YAML содержит повторяющийся ключ: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


StrictSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(slots=True, frozen=True)
class BundleMember:
    path: str
    payload: bytes


class CompassBundle:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self._archive: zipfile.ZipFile | None = None
        self.members: dict[str, zipfile.ZipInfo] = {}
        self.unknown_members: list[str] = []

    def __enter__(self) -> CompassBundle:
        if self.path.suffix.casefold() != ".zip":
            raise ValueError("Входной файл должен иметь расширение .zip")
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if self.path.stat().st_size > MAX_INPUT_BYTES:
            raise UnsafeBundleError("ZIP превышает допустимый размер")
        try:
            self._archive = zipfile.ZipFile(self.path)
            self._preflight()
        except Exception:
            if self._archive:
                self._archive.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self._archive:
            self._archive.close()

    def yaml_objects(self, family: str) -> list[tuple[str, dict[str, Any]]]:
        prefix = f"{family}/" if family else ""
        result: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(self.members, key=str.casefold):
            if family == "metadata":
                if path != "metadata.yaml":
                    continue
            elif not path.startswith(prefix):
                continue
            result.append((path, load_yaml(self.read(path), path)))
        return result

    def read(self, path: str) -> bytes:
        if not self._archive or path not in self.members:
            raise KeyError(path)
        info = self.members[path]
        with self._archive.open(info) as stream:
            payload = stream.read(MAX_MEMBER_BYTES + 1)
        if len(payload) > MAX_MEMBER_BYTES:
            raise UnsafeBundleError(f"Слишком большой объект архива: {path}")
        return payload

    def _preflight(self) -> None:
        assert self._archive is not None
        infos = [info for info in self._archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_MEMBERS:
            raise UnsafeBundleError("В ZIP слишком много файлов")
        stripped = _strip_outer_directory([info.filename for info in infos])
        normalized: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info, path in zip(infos, stripped, strict=True):
            _validate_member(info, path)
            key = path.casefold()
            if key in {item.casefold() for item in normalized}:
                raise UnsafeBundleError(f"Повторяющийся путь в ZIP: {path}")
            normalized[path] = info
            total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise UnsafeBundleError("Распакованный ZIP превышает допустимый размер")
        if "metadata.yaml" not in normalized:
            raise UnsafeBundleError("В ZIP отсутствует metadata.yaml")
        self.members = normalized
        self.unknown_members = [
            path
            for path in normalized
            if path != "metadata.yaml" and path.split("/", 1)[0] not in ALLOWED_FAMILIES
        ]


def _strip_outer_directory(names: list[str]) -> list[str]:
    prepared = [name.replace("\\", "/").strip("/") for name in names]
    for original, name in zip(names, prepared, strict=True):
        if original.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", original):
            raise UnsafeBundleError(f"Недопустимый путь ZIP: {original}")
        if any(part in {"", ".", ".."} for part in name.split("/")):
            raise UnsafeBundleError(f"Недопустимый путь ZIP: {original}")
    roots = {name.split("/", 1)[0] for name in prepared if "/" in name}
    has_root_file = any("/" not in name for name in prepared)
    if len(roots) == 1 and not has_root_file:
        prefix = next(iter(roots)) + "/"
        return [name[len(prefix) :] for name in prepared]
    return prepared


def _validate_member(info: zipfile.ZipInfo, path: str) -> None:
    normalized = posixpath.normpath(path)
    if not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        raise UnsafeBundleError(f"Недопустимый путь ZIP: {path}")
    if normalized == ".." or normalized.startswith("../") or normalized != path:
        raise UnsafeBundleError(f"Недопустимый путь ZIP: {path}")
    if info.flag_bits & 0x1:
        raise UnsafeBundleError(f"Зашифрованный ZIP member: {path}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise UnsafeBundleError(f"Символическая ссылка в ZIP: {path}")
    if info.file_size > MAX_MEMBER_BYTES:
        raise UnsafeBundleError(f"Слишком большой объект архива: {path}")
    ratio = info.file_size / max(1, info.compress_size)
    if ratio > MAX_COMPRESSION_RATIO:
        raise UnsafeBundleError(f"Подозрительное сжатие ZIP: {path}")
    if not path.casefold().endswith((".yaml", ".yml")):
        raise UnsafeBundleError(f"Неподдерживаемый файл в export bundle: {path}")


def load_yaml(payload: bytes, source: str) -> dict[str, Any]:
    if len(payload) > MAX_YAML_BYTES:
        raise UnsafeBundleError(f"YAML превышает лимит: {source}")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsafeBundleError(f"YAML должен быть UTF-8: {source}") from error
    try:
        _validate_yaml_events(text, source)
        value = yaml.load(text, Loader=StrictSafeLoader)
    except UnsafeBundleError:
        raise
    except yaml.YAMLError as error:
        raise UnsafeBundleError(f"Некорректный YAML: {source}") from error
    if not isinstance(value, dict):
        raise UnsafeBundleError(f"Корневой YAML-объект должен быть mapping: {source}")
    return value


def parse_json_object(value: object, source: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        _validate_python_tree(value)
        return value
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_JSON_BYTES:
        raise UnsafeBundleError(f"Некорректный JSON metadata: {source}")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise UnsafeBundleError(f"Некорректный JSON metadata: {source}") from error
    if not isinstance(parsed, dict):
        raise UnsafeBundleError(f"JSON metadata должен быть object: {source}")
    _validate_python_tree(parsed)
    return parsed


def _validate_yaml_events(text: str, source: str) -> None:
    count = 0
    depth = 0
    for event in yaml.parse(text, Loader=StrictSafeLoader):
        if isinstance(event, AliasEvent) or (isinstance(event, NodeEvent) and event.anchor is not None):
            raise UnsafeBundleError(f"YAML anchors и aliases запрещены: {source}")
        if isinstance(event, (CollectionStartEvent, ScalarEvent)):
            count += 1
        if isinstance(event, CollectionStartEvent):
            depth += 1
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise UnsafeBundleError("YAML превышает структурный лимит")
        if isinstance(event, ScalarEvent) and len(event.value) > MAX_STRING:
            raise UnsafeBundleError("YAML содержит слишком длинную строку")


def _validate_python_tree(value: object) -> None:
    count = 0
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise UnsafeBundleError("JSON превышает структурный лимит")
        if isinstance(item, str) and len(item) > MAX_STRING:
            raise UnsafeBundleError("JSON содержит слишком длинную строку")
        if isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise UnsafeBundleError("JSON содержит нечисловое значение")
