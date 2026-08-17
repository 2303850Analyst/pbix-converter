from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .model import (
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    SafeDocumentModel,
    SectionBreakBlock,
    TableBlock,
    clean_text,
)
from .ooxml import validate_docx


PORTRAIT_WIDTH = 9_360
LANDSCAPE_WIDTH = 14_400
BLUE = "2E5E8C"
DARK_BLUE = "17365D"
LIGHT_BLUE = "E8EEF5"


def render_document(model: SafeDocumentModel) -> bytes:
    document = Document()
    _configure_document(document, model)
    generated = model.generated_at.replace(tzinfo=None)
    document.core_properties.created = generated
    document.core_properties.modified = generated
    for block in model.blocks:
        if isinstance(block, HeadingBlock):
            _heading(document, block)
        elif isinstance(block, ParagraphBlock):
            _paragraph(document, block)
        elif isinstance(block, ListBlock):
            _list(document, block)
        elif isinstance(block, TableBlock):
            _table(document, block)
        elif isinstance(block, SectionBreakBlock):
            section = document.add_section(WD_SECTION.NEW_PAGE)
            _section(section, block.orientation)
    stream = BytesIO()
    document.save(stream)
    return _canonicalize(stream.getvalue())


def atomic_write(
    output_path: Path,
    payload: bytes,
    *,
    prefix: str = ".doc_converter_output_",
    before_commit: Callable[[], None] | None = None,
) -> None:
    output_path = output_path.resolve()
    if output_path.suffix.casefold() != ".docx":
        raise ValueError("Выходной файл должен иметь расширение .docx")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(output_path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=prefix, suffix=".tmp", dir=output_path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        validate_docx(temporary)
        if before_commit:
            before_commit()
        os.replace(temporary, output_path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _configure_document(document: object, model: SafeDocumentModel) -> None:
    section = document.sections[0]
    _section(section, "landscape")
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)
    normal = document.styles["Normal"]
    _style_font(normal, 10, "000000")
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.05
    for name, size, color, before, after in (
        ("Heading 1", 14, BLUE, 9, 4),
        ("Heading 2", 12, BLUE, 7, 3),
        ("Heading 3", 11, DARK_BLUE, 5, 2),
    ):
        style = document.styles[name]
        _style_font(style, size, color, bold=True)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
    document.core_properties.title = model.title
    document.core_properties.subject = model.product_subject
    document.core_properties.author = model.product_name
    document.core_properties.last_modified_by = model.product_name
    document.core_properties.comments = "Generated locally; no external relationships"


def _section(section: object, orientation: str) -> None:
    if orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height, margin = Inches(11.69), Inches(8.27), Inches(0.4)
    elif orientation == "portrait":
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height, margin = Inches(8.5), Inches(11), Inches(1)
    else:
        raise ValueError(f"Неизвестная ориентация секции: {orientation}")
    section.top_margin = section.right_margin = section.bottom_margin = section.left_margin = margin


def _heading(document: object, block: HeadingBlock) -> None:
    level = min(3, max(1, block.level))
    paragraph = document.add_heading("", level=level)
    size, color = {1: (14, BLUE), 2: (12, BLUE), 3: (11, DARK_BLUE)}[level]
    _inline(paragraph, clean_text(block.text, limit=500), size, color, bold=True)


def _paragraph(document: object, block: ParagraphBlock) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(3)
    if block.kind in {"lead", "note", "warning", "status"}:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "F2F4F7" if block.kind in {"lead", "note"} else "FFF4CE")
        paragraph._p.get_or_add_pPr().append(shading)
        paragraph.paragraph_format.left_indent = paragraph.paragraph_format.right_indent = Inches(0.12)
    color = "9B1C1C" if block.kind == "warning" else "7A5A00" if block.kind == "status" else "000000"
    _inline(
        paragraph,
        clean_text(block.text, preserve_newlines=True, limit=12_000),
        9.5 if block.kind != "normal" else 10,
        color,
        bold=block.kind == "status",
    )


def _list(document: object, block: ListBlock) -> None:
    style = "List Number" if block.ordered else "List Bullet"
    for item in block.items:
        _inline(document.add_paragraph(style=style), clean_text(item, limit=2_000), 10, "000000")


def _table(document: object, block: TableBlock) -> None:
    if not block.headers or len(block.headers) > 12:
        raise ValueError("DOCX-таблица должна содержать от одной до двенадцати колонок")
    widths = block.widths_dxa
    total = sum(widths)
    if len(widths) != len(block.headers) or total not in {PORTRAIT_WIDTH, LANDSCAPE_WIDTH}:
        raise ValueError("Некорректная геометрия DOCX-таблицы")
    if document.sections[-1].orientation == WD_ORIENT.LANDSCAPE and total == PORTRAIT_WIDTH:
        widths = _scale(widths, LANDSCAPE_WIDTH)
        total = LANDSCAPE_WIDTH
    table = document.add_table(rows=1, cols=len(block.headers))
    table.style, table.alignment, table.autofit = "Table Grid", WD_TABLE_ALIGNMENT.LEFT, False
    font_size = block.font_size or (7.2 if len(block.headers) > 5 else 8.5)
    for index, value in enumerate(block.headers):
        _cell(table.rows[0].cells[index], value, widths[index], font_size, True)
    _repeat_header(table.rows[0])
    for values in block.rows:
        if len(values) != len(block.headers):
            raise ValueError("Строка DOCX-таблицы не совпадает с заголовком")
        row = table.add_row()
        for index, value in enumerate(values):
            _cell(row.cells[index], value, widths[index], font_size, False)
        if not block.allow_row_split:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    _geometry(table, widths, total)
    document.add_paragraph().paragraph_format.space_after = Pt(0)


def _cell(cell: object, value: object, width: int, font_size: float, header: bool) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    cell.width = Inches(width / 1440)
    properties = cell._tc.get_or_add_tcPr()
    margins = properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        properties.append(margins)
    for edge, amount in (("top", 80), ("bottom", 80), ("start", 120), ("end", 120)):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:w"), str(amount))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    _inline(paragraph, clean_text(value, preserve_newlines=True, limit=None), font_size, DARK_BLUE if header else "000000", bold=header)
    if header:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), LIGHT_BLUE)
        cell._tc.get_or_add_tcPr().append(shading)


def _geometry(table: object, widths: tuple[int, ...], total: int) -> None:
    properties = table._tbl.tblPr
    indent = properties.first_child_found_in("w:tblInd")
    if indent is None:
        indent = OxmlElement("w:tblInd")
        properties.append(indent)
    indent.set(qn("w:w"), "120")
    indent.set(qn("w:type"), "dxa")
    width = properties.first_child_found_in("w:tblW")
    if width is None:
        width = OxmlElement("w:tblW")
        properties.append(width)
    width.set(qn("w:w"), str(total))
    width.set(qn("w:type"), "dxa")
    layout = properties.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        properties.append(layout)
    layout.set(qn("w:type"), "fixed")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for value in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(value))
        grid.append(column)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.width = Inches(widths[index] / 1440)
            tc_width = cell._tc.get_or_add_tcPr().get_or_add_tcW()
            tc_width.set(qn("w:w"), str(widths[index]))
            tc_width.set(qn("w:type"), "dxa")


def _repeat_header(row: object) -> None:
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(header)


def _inline(paragraph: object, text: str, size: float, color: str, *, bold: bool = False) -> None:
    position = 0
    for match in re.finditer(r"\*\*(.+?)\*\*", text, flags=re.DOTALL):
        if match.start() > position:
            _run(paragraph.add_run(text[position : match.start()]), size, color, bold)
        _run(paragraph.add_run(match.group(1)), size, color, True)
        position = match.end()
    if position < len(text) or not paragraph.runs:
        _run(paragraph.add_run(text[position:]), size, color, bold)


def _style_font(style: object, size: float, color: str, *, bold: bool | None = None) -> None:
    style.font.name = "Arial"
    style._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    style._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    style.font.size, style.font.color.rgb = Pt(size), RGBColor.from_string(color)
    if bold is not None:
        style.font.bold = bold


def _run(run: object, size: float, color: str, bold: bool) -> None:
    run.font.name = "Arial"
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    run.font.size, run.font.color.rgb, run.bold = Pt(size), RGBColor.from_string(color), bold


def _scale(widths: tuple[int, ...], total: int) -> tuple[int, ...]:
    scaled = [round(value * total / sum(widths)) for value in widths]
    scaled[-1] += total - sum(scaled)
    return tuple(scaled)


def _canonicalize(payload: bytes) -> bytes:
    target = BytesIO()
    with zipfile.ZipFile(BytesIO(payload)) as archive, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as output:
        for name in sorted(archive.namelist(), key=str.casefold):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, archive.read(name))
    return target.getvalue()
