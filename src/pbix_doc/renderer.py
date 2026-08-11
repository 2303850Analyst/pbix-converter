from __future__ import annotations

import os
import re
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .ooxml import validate_docx
from .projector import (
    CodeBlock,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    SafeDocumentModel,
    SectionBreakBlock,
    TableBlock,
    clean_text,
)
from .runtime import atomic_temp_prefix, cleanup_stale_artifacts


CONTENT_WIDTH_DXA = 9_360
LANDSCAPE_CONTENT_WIDTH_DXA = 14_400
BLUE = "2E5E8C"
DARK_BLUE = "17365D"
LIGHT_BLUE = "E8EEF5"
LIGHT_GRAY = "F2F4F7"
MUTED = "5F6B76"
RED = "9B1C1C"
GOLD = "7A5A00"


def render_document(model: SafeDocumentModel) -> bytes:
    document = Document()
    _configure_document(document)
    _add_cover(document, model)
    for block in model.blocks:
        if isinstance(block, HeadingBlock):
            _add_heading(document, block)
        elif isinstance(block, ParagraphBlock):
            _add_paragraph(document, block)
        elif isinstance(block, ListBlock):
            _add_list(document, block)
        elif isinstance(block, TableBlock):
            _add_table(document, block)
        elif isinstance(block, CodeBlock):
            _add_code(document, block)
        elif isinstance(block, SectionBreakBlock):
            _add_section_break(document, block)
    stream = BytesIO()
    document.save(stream)
    return _canonicalize_package(stream.getvalue())


def atomic_write(output_path: Path, payload: bytes) -> None:
    output_path = output_path.resolve()
    if output_path.suffix.casefold() != ".docx":
        raise ValueError("Выходной файл должен иметь расширение .docx")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(output_path.parent)
    cleanup_stale_artifacts(output_path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=atomic_temp_prefix(),
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        validate_docx(temporary)
        os.replace(temporary, output_path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _configure_document(document: DocumentObject) -> None:
    section = document.sections[0]
    _configure_section(section, "landscape")
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)

    normal = document.styles["Normal"]
    _set_style_font(normal, "Arial", 10, "000000")
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.05

    settings = {
        "Heading 1": (14, BLUE, 9, 4),
        "Heading 2": (12, BLUE, 7, 3),
        "Heading 3": (11, DARK_BLUE, 5, 2),
    }
    for name, (size, color, before, after) in settings.items():
        style = document.styles[name]
        _set_style_font(style, "Arial", size, color, bold=True)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Number"):
        style = document.styles[name]
        _set_style_font(style, "Arial", 10, "000000")
        style.paragraph_format.left_indent = Inches(0.3)
        style.paragraph_format.first_line_indent = Inches(-0.15)
        style.paragraph_format.space_after = Pt(1)
        style.paragraph_format.line_spacing = 1.05

    document.core_properties.title = "Документация Power BI"
    document.core_properties.subject = "Локальная документация PBIX для Confluence"
    document.core_properties.author = "PBIX Converter"
    document.core_properties.last_modified_by = "PBIX Converter"
    document.core_properties.comments = "Generated locally; no external relationships"


def _add_cover(document: DocumentObject, model: SafeDocumentModel) -> None:
    generated = model.generated_at.replace(tzinfo=None)
    document.core_properties.created = generated
    document.core_properties.modified = generated
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_after = Pt(12)

    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.space_after = Pt(5)
    run = kicker.add_run("POWER BI · ТЕХНИЧЕСКОЕ РУКОВОДСТВО")
    _set_run_font(run, "Arial", 9, BLUE, bold=True)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(8)
    run = title.add_run(clean_text(model.title))
    _set_run_font(run, "Arial", 22, DARK_BLUE, bold=True)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(10)
    run = subtitle.add_run(clean_text(model.subtitle))
    _set_run_font(run, "Arial", 11, MUTED)

    cover_table = TableBlock(
        ("Параметр", "Значение"),
        [
            ("Исходный файл", model.source_name),
            ("Статус", model.overall_status),
            ("Сформирован", model.generated_at.isoformat()),
            ("Режим", "Локальная обработка в защищённой среде VDI"),
            ("Назначение", "Ручной импорт DOCX в Atlassian Confluence Data Center"),
        ],
        (2_300, 7_060),
    )
    _add_table(document, cover_table)
    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note.paragraph_format.space_before = Pt(6)
    run = note.add_run("Детальные строки данных не включаются; сохранённые значения фильтров берутся только из метаданных отчёта.")
    _set_run_font(run, "Arial", 8.5, MUTED, italic=True)

    document.add_section(WD_SECTION.NEW_PAGE)


def _canonicalize_package(payload: bytes) -> bytes:
    source = BytesIO(payload)
    target = BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for name in sorted(archive.namelist(), key=str.casefold):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, archive.read(name))
    return target.getvalue()


def _add_heading(document: DocumentObject, block: HeadingBlock) -> None:
    text = clean_text(block.text, limit=500)
    level = min(3, max(1, block.level))
    paragraph = document.add_heading("", level=level)
    size, color = {1: (14, BLUE), 2: (12, BLUE), 3: (11, DARK_BLUE)}[level]
    _add_inline_text(paragraph, text, font_size=size, color=color, bold=True)


def _add_section_break(document: DocumentObject, block: SectionBreakBlock) -> None:
    section = document.add_section(WD_SECTION.NEW_PAGE)
    _configure_section(section, block.orientation)


def _configure_section(section: object, orientation: str) -> None:
    if orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Inches(11.69)
        section.page_height = Inches(8.27)
        margin = Inches(0.4)
    elif orientation == "portrait":
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        margin = Inches(1)
    else:
        raise ValueError(f"Неизвестная ориентация секции: {orientation}")
    section.top_margin = margin
    section.right_margin = margin
    section.bottom_margin = margin
    section.left_margin = margin


def _add_paragraph(document: DocumentObject, block: ParagraphBlock) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(3)
    if block.kind in {"lead", "note", "warning", "status"}:
        _shade_paragraph(paragraph, LIGHT_GRAY if block.kind in {"lead", "note"} else "FFF4CE")
        paragraph.paragraph_format.left_indent = Inches(0.12)
        paragraph.paragraph_format.right_indent = Inches(0.12)
        paragraph.paragraph_format.space_before = Pt(1)
        paragraph.paragraph_format.space_after = Pt(3)
    color = RED if block.kind == "warning" else GOLD if block.kind == "status" else "000000"
    _add_inline_text(
        paragraph,
        clean_text(block.text, preserve_newlines=True, limit=12_000),
        font_size=9.5 if block.kind != "normal" else 10,
        color=color,
        bold=block.kind == "status",
    )


def _add_list(document: DocumentObject, block: ListBlock) -> None:
    style = "List Number" if block.ordered else "List Bullet"
    for item in block.items:
        paragraph = document.add_paragraph(style=style)
        _add_inline_text(paragraph, clean_text(item, limit=2_000), font_size=10)


def _add_table(document: DocumentObject, block: TableBlock) -> None:
    if not block.headers or len(block.headers) > 12:
        raise ValueError("DOCX-таблица должна содержать от одной до двенадцати колонок")
    widths = block.widths_dxa
    total_width = sum(widths)
    if len(block.headers) != len(block.widths_dxa) or total_width not in {
        CONTENT_WIDTH_DXA,
        LANDSCAPE_CONTENT_WIDTH_DXA,
    }:
        raise ValueError("Некорректная геометрия DOCX-таблицы")
    if document.sections[-1].orientation == WD_ORIENT.LANDSCAPE and total_width == CONTENT_WIDTH_DXA:
        widths = _scale_widths(widths, LANDSCAPE_CONTENT_WIDTH_DXA)
        total_width = LANDSCAPE_CONTENT_WIDTH_DXA
    table = document.add_table(rows=1, cols=len(block.headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    header = table.rows[0]
    _repeat_header(header)
    font_size = block.font_size or (7.2 if len(block.headers) > 5 else 8.5)
    padding = 35 if len(block.headers) > 5 else 50
    for index, value in enumerate(block.headers):
        _set_cell_text(
            header.cells[index],
            value,
            bold=True,
            color=DARK_BLUE,
            fill=LIGHT_BLUE,
            font_size=font_size,
            padding=padding,
        )
    for values in block.rows:
        if len(values) != len(block.headers):
            raise ValueError("Строка DOCX-таблицы не совпадает с заголовком")
        row = table.add_row()
        for index, value in enumerate(values):
            _set_cell_text(row.cells[index], value, font_size=font_size, padding=padding)
    _set_table_geometry(table, widths, total_width)
    if not block.allow_row_split:
        for row in table.rows:
            _prevent_row_split(row)
    after = document.add_paragraph()
    after.paragraph_format.space_after = Pt(0)


def _add_code(document: DocumentObject, block: CodeBlock) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.12)
    paragraph.paragraph_format.right_indent = Inches(0.12)
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.line_spacing = 1.0
    _shade_paragraph(paragraph, LIGHT_GRAY)
    run = paragraph.add_run(clean_text(block.text, preserve_newlines=True, limit=12_000))
    _set_run_font(run, "Consolas", 8.5, "202124")
    if block.status != "OK":
        status = paragraph.add_run(f"\n[{clean_text(block.status)}]")
        _set_run_font(status, "Arial", 8.5, GOLD, bold=True)


def _set_cell_text(
    cell: object,
    value: object,
    *,
    bold: bool = False,
    color: str = "000000",
    fill: str | None = None,
    font_size: float = 9.2,
    padding: int = 80,
) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _set_cell_margins(cell, top=padding, bottom=padding, start=padding, end=padding)
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    paragraph.paragraph_format.keep_with_next = bold
    _add_inline_text(
        paragraph,
        clean_text(value, preserve_newlines=True, limit=None),
        font_size=font_size,
        color=color,
        bold=bold,
    )
    if fill:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(shading)


def _set_table_geometry(table: object, widths: tuple[int, ...], total_width: int) -> None:
    properties = table._tbl.tblPr
    width = properties.first_child_found_in("w:tblW")
    if width is None:
        width = OxmlElement("w:tblW")
        properties.append(width)
    width.set(qn("w:w"), str(total_width))
    width.set(qn("w:type"), "dxa")
    indent = properties.first_child_found_in("w:tblInd")
    if indent is None:
        indent = OxmlElement("w:tblInd")
        properties.append(indent)
    indent.set(qn("w:w"), "120")
    indent.set(qn("w:type"), "dxa")
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


def _scale_widths(widths: tuple[int, ...], total: int) -> tuple[int, ...]:
    source_total = sum(widths)
    scaled = [round(value * total / source_total) for value in widths]
    scaled[-1] += total - sum(scaled)
    return tuple(scaled)


def _set_cell_margins(cell: object, **values: int) -> None:
    properties = cell._tc.get_or_add_tcPr()
    margins = properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        properties.append(margins)
    for edge in ("top", "start", "bottom", "end"):
        node = margins.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            margins.append(node)
        node.set(qn("w:w"), str(values.get(edge, 80)))
        node.set(qn("w:type"), "dxa")


def _repeat_header(row: object) -> None:
    properties = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    properties.append(header)


def _prevent_row_split(row: object) -> None:
    properties = row._tr.get_or_add_trPr()
    if properties.find(qn("w:cantSplit")) is None:
        properties.append(OxmlElement("w:cantSplit"))


def _shade_paragraph(paragraph: object, fill: str) -> None:
    properties = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _add_inline_text(
    paragraph: object,
    text: str,
    *,
    font_size: float,
    color: str = "000000",
    bold: bool = False,
    italic: bool | None = None,
) -> None:
    position = 0
    for match in re.finditer(r"\*\*(.+?)\*\*", text, flags=re.DOTALL):
        if match.start() > position:
            run = paragraph.add_run(text[position : match.start()])
            _set_run_font(run, "Arial", font_size, color, bold=bold, italic=italic)
        run = paragraph.add_run(match.group(1))
        _set_run_font(run, "Arial", font_size, color, bold=True, italic=italic)
        position = match.end()
    if position < len(text) or not paragraph.runs:
        run = paragraph.add_run(text[position:])
        _set_run_font(run, "Arial", font_size, color, bold=bold, italic=italic)


def _set_style_font(
    style: object,
    name: str,
    size: float,
    color: str,
    *,
    bold: bool | None = None,
) -> None:
    style.font.name = name
    style._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    style._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        style.font.bold = bold


def _set_run_font(
    run: object,
    name: str,
    size: float,
    color: str,
    *,
    bold: bool | None = None,
    italic: bool | None = None,
) -> None:
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
