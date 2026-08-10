from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 16 * 1024 * 1024
RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FORBIDDEN_PART_MARKERS = (
    "vbaproject",
    "/activex/",
    "/embeddings/",
    "/oleobject",
    "externallink",
)
FORBIDDEN_CONTENT_MARKERS = (
    "macroenabled",
    "vnd.ms-office.vbaproject",
    "oleobject",
    "activex",
)


class UnsafeDocxError(ValueError):
    pass


def validate_docx(path: Path) -> None:
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise UnsafeDocxError("DOCX превышает лимит 20 МБ")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            _validate_names(names)
            if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
                raise UnsafeDocxError("Распакованный OOXML превышает допустимый размер")
            if any(info.file_size > MAX_PART_BYTES for info in infos):
                raise UnsafeDocxError("Отдельная часть OOXML превышает допустимый размер")
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not required.issubset(names):
                raise UnsafeDocxError("DOCX не содержит обязательные OOXML-части")
            for info in infos:
                folded = f"/{info.filename.casefold()}"
                if any(marker in folded for marker in FORBIDDEN_PART_MARKERS):
                    raise UnsafeDocxError(f"Запрещённая OOXML-часть: {info.filename}")
                payload = archive.read(info)
                if info.filename == "[Content_Types].xml":
                    _validate_content_types(payload)
                elif info.filename.casefold().endswith(".rels"):
                    _validate_relationships(payload, info.filename)
                elif info.filename.casefold().startswith("word/") and info.filename.casefold().endswith(".xml"):
                    _validate_word_xml(payload, info.filename)
    except zipfile.BadZipFile as error:
        raise UnsafeDocxError("Повреждённый DOCX/OOXML пакет") from error


def _validate_names(names: list[str]) -> None:
    if len(names) != len({name.casefold() for name in names}):
        raise UnsafeDocxError("OOXML содержит повторяющиеся части")
    for name in names:
        normalized = posixpath.normpath(name.replace("\\", "/"))
        if name.startswith(("/", "\\")) or normalized == ".." or normalized.startswith("../"):
            raise UnsafeDocxError(f"Недопустимый путь OOXML: {name}")


def _validate_content_types(payload: bytes) -> None:
    folded = payload.decode("utf-8", errors="ignore").casefold()
    if any(marker in folded for marker in FORBIDDEN_CONTENT_MARKERS):
        raise UnsafeDocxError("DOCX содержит активный или встроенный тип содержимого")
    _parse_xml(payload, "[Content_Types].xml")


def _validate_relationships(payload: bytes, name: str) -> None:
    root = _parse_xml(payload, name)
    for relation in root.findall(f"{{{RELATIONSHIP_NS}}}Relationship"):
        if relation.attrib.get("TargetMode", "").casefold() == "external":
            raise UnsafeDocxError(f"DOCX содержит внешнюю relationship: {name}")


def _validate_word_xml(payload: bytes, name: str) -> None:
    root = _parse_xml(payload, name)
    if root.findall(f".//{{{WORD_NS}}}fldSimple") or root.findall(f".//{{{WORD_NS}}}instrText"):
        raise UnsafeDocxError(f"DOCX содержит автоматическое поле: {name}")


def _parse_xml(payload: bytes, name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise UnsafeDocxError(f"Некорректный XML в {name}") from error
