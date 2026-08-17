from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class HeadingBlock:
    level: int
    text: str


@dataclass(slots=True)
class ParagraphBlock:
    text: str
    kind: str = "normal"


@dataclass(slots=True)
class ListBlock:
    items: list[str]
    ordered: bool = False


@dataclass(slots=True)
class TableBlock:
    headers: tuple[str, ...]
    rows: list[tuple[str, ...]]
    widths_dxa: tuple[int, ...]
    font_size: float | None = None
    allow_row_split: bool = False


@dataclass(slots=True)
class SectionBreakBlock:
    orientation: str


DocumentBlock = HeadingBlock | ParagraphBlock | ListBlock | TableBlock | SectionBreakBlock


@dataclass(slots=True)
class SafeDocumentModel:
    title: str
    subtitle: str
    source_name: str
    generated_at: datetime
    overall_status: str
    blocks: list[DocumentBlock] = field(default_factory=list)
    product_name: str = "PBIX Converter"
    product_subject: str = "Локальная документация PBIX для Confluence"


def clean_text(value: object, *, preserve_newlines: bool = False, limit: int | None = 4_000) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\t\n\r" or ord(char) >= 32)
    if preserve_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = " ".join(text.replace("\t", " ").splitlines())
    text = text.strip()
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"
