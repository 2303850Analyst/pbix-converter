from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .domain import (
    BookmarkInfo,
    ColumnInfo,
    DocumentationModel,
    FilterInfo,
    MeasureInfo,
    PageInfo,
    QueryInfo,
    VisualInfo,
)
from .policy import METADATA_EXCLUSION_POLICY


MAX_DAX_CHARS = 8_000
MAX_DEPENDENCIES = 100
MAX_TABLE_ROWS = 500
MAX_FIELD_REGISTRY_ROWS = 5_000
MAX_PAGE_COMPOSITION_ROWS = 2_000
MAX_VISUALS = 1_000
TABLE_WIDTH_DXA = 9_360
LANDSCAPE_TABLE_WIDTH_DXA = 14_400
OVERFLOW = "<PARTIAL — ЧАСТЬ СВЕДЕНИЙ НЕ ВКЛЮЧЕНА ИЗ-ЗА ЛИМИТА ДОКУМЕНТА>"
DAX_QUALIFIED_REFERENCE_PATTERN = re.compile(
    r"(?:(?:'((?:[^']|'')+)')|([A-Za-z_$][\w$ .-]*))\s*\[([^\]]+)\]"
)
TECHNICAL_VISUAL_NAME_PATTERN = re.compile(
    r"^(?:[0-9a-f]{16,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


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


@dataclass(slots=True)
class DaxProjection:
    text: str | None
    status: str
    references: list[str]
    functions: list[str]
    reason: str | None = None


@dataclass(slots=True)
class FieldRegistryEntry:
    object_type: str
    table: str
    name: str
    data_type: str | None = None
    format_string: str | None = None
    dax_expression: str | None = None
    source_column: str | None = None
    native_data_type: str | None = None
    summarize_by: str | None = None
    aliases: set[str] = field(default_factory=set)
    pages: set[str] = field(default_factory=set)
    visuals: set[str] = field(default_factory=set)
    aggregations: set[str] = field(default_factory=set)
    dax_usages: set[str] = field(default_factory=set)


def overall_status(model: DocumentationModel) -> str:
    if not model.report.pages:
        return "FAILED"
    if any(issue.severity in {"ERROR", "WARNING"} for issue in model.issues):
        return "PARTIAL"
    return "OK"


def project_document(model: DocumentationModel) -> SafeDocumentModel:
    report = model.report
    pages = sorted(report.pages, key=lambda item: item.order)
    page_ids = {page.id: f"P{index:02d}" for index, page in enumerate(pages, 1)}
    visual_ids: dict[tuple[str, str], str] = {}
    visual_counter = 1
    for page in pages:
        for visual in page.visuals:
            local_id = f"V{visual_counter:03d}"
            visual_counter += 1
            visual_ids[(page.id, visual.id)] = local_id
    bookmark_ids = {
        bookmark.id: f"B{index:02d}"
        for index, bookmark in enumerate(sorted(report.bookmarks, key=lambda item: item.order), 1)
    }
    blocks: list[DocumentBlock] = []
    blocks.extend(_report_overview(model, pages))
    blocks.extend(_field_registry(model, pages, page_ids, visual_ids))
    blocks.extend(_page_composition(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_bookmark_register(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_filter_register(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_difference_index(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_semantic_model(model))
    blocks.extend(_security_and_sources(model))

    return SafeDocumentModel(
        title=clean_text(report.title) or model.file.path.stem,
        subtitle="Руководство по расчётам, контекстам и навигации Power BI",
        source_name=clean_text(model.file.path.name),
        generated_at=model.generated_at,
        overall_status=overall_status(model),
        blocks=blocks,
    )


def _report_overview(model: DocumentationModel, pages: list[PageInfo]) -> list[DocumentBlock]:
    report = model.report
    semantic = model.semantic_model
    visuals = [visual for page in pages for visual in page.visuals]
    fields = [
        item
        for visual in visuals
        for item in visual.fields
        if not _is_excluded_metadata(item.table)
    ]
    unresolved = sum(item.status != "RESOLVED" for item in fields)
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Об отчёте"),
        ParagraphBlock(
            "Документ описывает устройство отчёта по доступным локальным метаданным. "
            "Он не вычисляет DAX и не подтверждает фактические значения показателей.",
            "lead",
        ),
        TableBlock(
            ("Параметр", "Значение"),
            [
                ("Файл", clean_text(model.file.path.name)),
                ("Формат отчёта", f"{report.report_format} {report.format_version or ''}".strip()),
                ("Сформирован", model.generated_at.isoformat(timespec="seconds")),
                ("Полнота извлечения", f"{_overall_status_label(overall_status(model))}; {_issue_summary(model)}"),
                (
                    "Структура",
                    f"Листов: {len(pages)}; визуалов: {len(visuals)}; "
                    f"закладок: {len(report.bookmarks)}; переходов: {len(report.navigation)}",
                ),
                ("Ссылки на поля", f"Разрешено {len(fields) - unresolved} из {len(fields)}"),
                (
                    "Семантическая модель",
                    f"{_semantic_status_label(semantic.status)}; "
                    f"таблиц: {sum(not _is_excluded_metadata(item.name) for item in semantic.tables)}; "
                    f"мер: {sum(not _is_excluded_metadata(item.table) for item in semantic.measures)}",
                ),
            ],
            (2_700, 6_660),
        ),
    ]
    return blocks


def _field_registry(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
) -> list[DocumentBlock]:
    entries: dict[tuple[str, str, str], FieldRegistryEntry] = {}
    by_name: dict[tuple[str, str], list[FieldRegistryEntry]] = defaultdict(list)

    def add_entry(entry: FieldRegistryEntry) -> FieldRegistryEntry:
        key = (entry.object_type, entry.table.casefold(), entry.name.casefold())
        current = entries.get(key)
        if current is None:
            entries[key] = entry
            by_name[(entry.table.casefold(), entry.name.casefold())].append(entry)
            return entry
        return current

    for table in model.semantic_model.tables:
        if _is_excluded_metadata(table.name):
            continue
        for column in table.columns:
            add_entry(
                FieldRegistryEntry(
                    "COLUMN",
                    column.table,
                    column.name,
                    column.data_type,
                    column.format_string,
                    column.expression,
                    column.source_column,
                    column.native_data_type,
                    column.summarize_by,
                )
            )
    for measure in model.semantic_model.measures:
        if _is_excluded_metadata(measure.table):
            continue
        add_entry(
            FieldRegistryEntry(
                "MEASURE",
                measure.table,
                measure.name,
                measure.data_type,
                measure.format_string,
                measure.expression,
            )
        )

    def resolve_entry(table: str | None, name: str, object_type: str) -> FieldRegistryEntry:
        normalized_type = "MEASURE" if object_type == "MEASURE" else "COLUMN"
        normalized_table = table or "<UNKNOWN>"
        exact = entries.get((normalized_type, normalized_table.casefold(), name.casefold()))
        if exact:
            return exact
        matches = by_name.get((normalized_table.casefold(), name.casefold()), [])
        if len(matches) == 1:
            return matches[0]
        return add_entry(FieldRegistryEntry(normalized_type, normalized_table, name))

    for page in pages:
        page_label = _page_ref(page, page_ids)
        for visual in page.visuals:
            visual_label = (
                f"{_visual_ref(visual, visual_ids[(page.id, visual.id)])}, "
                f"{_page_ref(page, page_ids)}"
            )
            for item in visual.fields:
                if _is_excluded_metadata(item.table):
                    continue
                entry = resolve_entry(item.table, item.name, item.object_type)
                entry.aliases.add(
                    _entity_name(
                        _visual_display_name(visual, visual_ids[(page.id, visual.id)]),
                        "Элемент без названия",
                    )
                )
                entry.pages.add(page_label)
                entry.visuals.add(visual_label)
                if item.aggregation:
                    entry.aggregations.add(clean_text(item.aggregation))
            for item in visual.filters:
                if _is_excluded_metadata(item.field):
                    continue
                parsed = _qualified_parts(item.field)
                if parsed:
                    entry = resolve_entry(parsed[0], parsed[1], "COLUMN")
                    entry.pages.add(f"{page_label} (фильтр визуала)")
                    entry.visuals.add(visual_label)

        for item in page.filters:
            if _is_excluded_metadata(item.field):
                continue
            parsed = _qualified_parts(item.field)
            if parsed:
                entry = resolve_entry(parsed[0], parsed[1], "COLUMN")
                entry.aliases.add("Фильтр листа")
                entry.pages.add(f"{page_label} (фильтр листа)")

    for item in model.report.report_filters:
        if _is_excluded_metadata(item.field):
            continue
        parsed = _qualified_parts(item.field)
        if parsed:
            entry = resolve_entry(parsed[0], parsed[1], "COLUMN")
            entry.aliases.add("Фильтр уровня отчёта")
            entry.pages.add("Все листы (фильтр уровня отчёта)")

    formula_owners: list[tuple[FieldRegistryEntry, DaxProjection]] = []
    for entry in entries.values():
        if entry.dax_expression:
            formula_owners.append((entry, sanitize_dax(entry.dax_expression)))
    for owner, dax in formula_owners:
        owner_label = f"{owner.table}[{owner.name}]"
        for reference in dax.references:
            target = _resolve_registry_reference(reference, owner, entries, by_name)
            if target:
                target.dax_usages.add(owner_label)

    rows: list[tuple[str, ...]] = []
    ordered = sorted(
        (entry for entry in entries.values() if not _is_excluded_metadata(entry.table)),
        key=lambda item: (item.table.casefold(), item.name.casefold(), item.object_type),
    )
    for entry in ordered[:MAX_FIELD_REGISTRY_ROWS]:
        dax = sanitize_dax(entry.dax_expression)
        dax_text = dax.text or (dax.reason if entry.dax_expression else "—")
        aggregations = set(entry.aggregations)
        if entry.summarize_by:
            aggregations.add(clean_text(entry.summarize_by))
        rows.append(
            (
                f"{clean_text(entry.table)}[{clean_text(entry.name)}]",
                _joined(entry.aliases),
                _display_or_dash(entry.data_type),
                "",
                clean_text(dax_text, preserve_newlines=True, limit=MAX_DAX_CHARS + len(OVERFLOW)),
                _joined(entry.pages),
                _source_name(entry),
                _source_type(entry),
                _joined(entry.dax_usages),
                _joined(entry.visuals),
                _aggregation_text(aggregations),
            )
        )
    if len(ordered) > MAX_FIELD_REGISTRY_ROWS:
        rows.append((OVERFLOW,) + ("",) * 10)

    blocks: list[DocumentBlock] = [
        SectionBreakBlock("landscape"),
        HeadingBlock(1, "Реестр полей отчёта"),
        ParagraphBlock(
            "Реестр объединяет локальную семантическую модель, DAX и найденные привязки отчёта. "
            "Колонка «Объяснение» намеренно оставлена пустой для ручного заполнения разработчиком. "
            "NativeQuery не выполняется; показываются только безопасно извлечённые имя и тип.",
            "lead",
        ),
    ]
    if rows:
        blocks.append(
            TableBlock(
                (
                    "Таблица[Поле]",
                    "Варианты названий",
                    "Тип данных в PBI",
                    "Объяснение",
                    "DAX",
                    "Упоминается на листе",
                    "Наименование поля таблицы в источнике",
                    "Тип данных в источнике",
                    "Используется в DAX",
                    "Используется в визуалах",
                    "Варианты агрегаций",
                ),
                rows,
                (1_250, 1_250, 900, 1_050, 1_600, 1_100, 1_450, 1_450, 1_200, 1_500, 1_650),
                font_size=7.2,
                allow_row_split=True,
            )
        )
    else:
        blocks.append(ParagraphBlock("Поля в доступной локальной метадате не найдены.", "warning"))
    blocks.append(SectionBreakBlock("landscape"))
    return blocks


def _page_composition(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    page_by_id = {page.id: page for page in pages}
    visual_by_key = {
        (page.id, visual.id): visual
        for page in pages
        for visual in page.visuals
    }
    measure_by_key = {
        (item.table.casefold(), item.name.casefold()): item
        for item in model.semantic_model.measures
        if not _is_excluded_metadata(item.table)
    }
    column_by_key = {
        (column.table.casefold(), column.name.casefold()): column
        for table in model.semantic_model.tables
        if not _is_excluded_metadata(table.name)
        for column in table.columns
    }
    bookmarks = sorted(model.report.bookmarks, key=lambda item: item.order)
    def visual_row(page: PageInfo, visual: VisualInfo) -> tuple[str, ...]:
        related = [
            bookmark
            for bookmark in bookmarks
            if visual.id in bookmark.affected_visual_ids
            and bookmark.associated_page_id in {None, page.id}
        ]
        return (
            visual_ids[(page.id, visual.id)],
            _entity_name(
                _visual_display_name(visual, visual_ids[(page.id, visual.id)]),
                "Элемент без названия",
            ),
            _visual_type_label(visual),
            _visual_sources(visual),
            _visual_calculations(visual, measure_by_key, column_by_key),
            _composition_context(model, page, visual),
            "; ".join(_bookmark_ref(item, bookmark_ids) for item in related) or "—",
            _actions_text(model, page.id, visual, page_ids, bookmark_ids),
        )

    def bookmark_row(bookmark: BookmarkInfo) -> tuple[str, ...]:
        page = page_by_id.get(bookmark.associated_page_id or "")
        affected = _bookmark_visuals(bookmark, pages)
        fields = "; ".join(
            dict.fromkeys(
                value
                for _, visual in affected
                for value in _visual_sources(visual).split("; ")
                if value != "—"
            )
        ) or "—"
        calculations = "; ".join(
            dict.fromkeys(
                value
                for _, visual in affected
                for value in [_visual_calculations(visual, measure_by_key, column_by_key)]
                if value != "—"
            )
        ) or "—"
        type_label = "Закладка"
        if clean_text(bookmark.group):
            type_label += f"; группа «{clean_text(bookmark.group)}»"
        return (
            bookmark_ids[bookmark.id],
            _entity_name(_bookmark_display_name(bookmark), "Закладка без названия"),
            type_label,
            fields,
            calculations,
            _bookmark_state_text(bookmark),
            "—",
            _bookmark_actions(
                model,
                bookmark,
                page_by_id,
                visual_by_key,
                page_ids,
                visual_ids,
            ),
        )
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Состав листа"),
        ParagraphBlock(
            "Одна строка соответствует визуалу, элементу управления или закладке. "
            "Каждый лист показан отдельно. В колонке «Элемент» указан локальный ID; "
            "в остальных колонках ссылки содержат тип, название и ID.",
            "lead",
        ),
    ]
    remaining = MAX_PAGE_COMPOSITION_ROWS
    for page in pages:
        blocks.append(HeadingBlock(2, _page_ref(page, page_ids)))
        page_rows = [visual_row(page, visual) for visual in page.visuals]
        page_rows.extend(bookmark_row(item) for item in bookmarks if item.associated_page_id == page.id)
        shown = page_rows[: max(0, remaining)]
        remaining -= len(shown)
        if len(page_rows) > len(shown):
            shown.append((OVERFLOW,) + ("",) * 7)
        if not shown:
            blocks.append(ParagraphBlock("Элементы и привязанные закладки не найдены."))
            continue
        blocks.append(
            TableBlock(
                (
                    "Элемент",
                    "Название",
                    "Тип и назначение",
                    "Поля и показатели",
                    "Расчёт / DAX",
                    "Контекст и фильтры",
                    "Связанные закладки",
                    "Действие / переход",
                ),
                shown,
                (650, 1_400, 1_400, 1_900, 2_600, 1_900, 2_000, 2_550),
                font_size=7.2,
                allow_row_split=True,
            )
        )
        if remaining <= 0:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
            break
    return blocks


def _bookmark_register(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    bookmarks = sorted(model.report.bookmarks, key=lambda item: item.order)
    navigation = list(model.report.navigation)
    blocks: list[DocumentBlock] = [HeadingBlock(1, "Переходы и действия (Все закладки)")]
    if not bookmarks and not navigation:
        blocks.append(ParagraphBlock("Закладки и переходы не найдены."))
        return blocks
    page_by_id = {page.id: page for page in pages}
    visual_by_key = {
        (page.id, visual.id): (page, visual)
        for page in pages
        for visual in page.visuals
    }
    visual_only_by_key = {key: visual for key, (_, visual) in visual_by_key.items()}
    rows: list[tuple[str, ...]] = []
    for bookmark in bookmarks[:MAX_TABLE_ROWS]:
        page = page_by_id.get(bookmark.associated_page_id or "")
        triggers: list[str] = []
        for edge in model.report.navigation:
            if edge.target_kind != "BOOKMARK" or edge.target_id != bookmark.id:
                continue
            source = visual_by_key.get((edge.source_page_id, edge.source_visual_id))
            if source:
                source_page, visual = source
                triggers.append(
                    f"{_visual_ref(visual, visual_ids.get((source_page.id, visual.id), '?'))}, "
                    f"{_page_ref(source_page, page_ids)}"
                )
        if not triggers:
            triggers.extend(
                f"указание metadata {_entity_name(value, 'Элемент без названия')}"
                for value in bookmark.trigger_refs
                if clean_text(value)
            )
        affected = [
            _visual_ref(visual, visual_ids.get((affected_page.id, visual.id), "?"))
            for affected_page, visual in _bookmark_visuals(bookmark, pages)
        ]
        rows.append(
            (
                _bookmark_ref(bookmark, bookmark_ids, compact=True),
                (
                    f"Закладка; группа {_entity_name(bookmark.group, 'Группа без названия')}"
                    if clean_text(bookmark.group)
                    else "Закладка"
                ),
                _page_ref(page, page_ids, compact=True) if page else "—",
                "; ".join(dict.fromkeys(triggers)) or "—",
                f"Сохранённое состояние на {_page_ref(page, page_ids)}" if page else "—",
                "; ".join(dict.fromkeys(affected)) or "—",
                _bookmark_filter_state_text(bookmark),
                _bookmark_effect_text(bookmark, page),
            )
        )
    for edge in navigation:
        if edge.target_kind == "BOOKMARK":
            continue
        source_page = page_by_id.get(edge.source_page_id)
        source_ref = _edge_source_visual_ref(edge, visual_only_by_key, visual_ids)
        target = _edge_target(edge, page_ids, bookmark_ids)
        rows.append(
            (
                source_ref,
                _action_label(edge.action_type),
                _page_ref(source_page, page_ids, compact=True) if source_page else "—",
                source_ref,
                target,
                "—",
                "—",
                _navigation_effect(edge.action_type, edge.target_kind, target),
            )
        )
    shown = rows[:MAX_TABLE_ROWS]
    blocks.append(
        TableBlock(
            (
                "Элемент / закладка",
                "Тип действия",
                "Исходный лист",
                "Откуда вызывается",
                "Переход / цель",
                "Затронутые элементы",
                "Фильтры и сохранённое состояние",
                "Эффект для пользователя",
            ),
            shown,
            (1_400, 1_200, 1_400, 2_100, 2_000, 2_200, 2_200, 1_900),
            font_size=7.2,
            allow_row_split=True,
        )
    )
    if len(rows) > MAX_TABLE_ROWS:
        blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    return blocks


def _filter_register(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    rows: list[tuple[str, ...]] = []
    bookmarks = sorted(model.report.bookmarks, key=lambda item: item.order)

    def append_filter(
        item: FilterInfo,
        scope: str,
        source: str,
        applies_to: str,
        related: Iterable[BookmarkInfo] = (),
    ) -> None:
        rows.append(
            (
                scope,
                source,
                clean_text(item.field) or "—",
                _filter_type_label(item.filter_type),
                _saved_values_text(item.saved_values, item.saved_value_count),
                applies_to,
                "; ".join(_bookmark_ref(value, bookmark_ids) for value in related) or "—",
                _filter_extraction_status(item),
            )
        )

    for item in _visible_filters(model.report.report_filters):
        append_filter(item, "Отчёт", "Весь отчёт", "Все листы и визуалы")
    for page in pages:
        for item in _visible_filters(page.filters):
            append_filter(
                item,
                "Лист",
                _page_ref(page, page_ids),
                f"Все визуалы на {_page_ref(page, page_ids)}",
            )
        for visual in page.visuals:
            related = [
                bookmark
                for bookmark in bookmarks
                if visual.id in bookmark.affected_visual_ids
                and bookmark.associated_page_id in {None, page.id}
            ]
            for item in _visible_filters(visual.filters):
                visual_ref = _visual_ref(visual, visual_ids[(page.id, visual.id)])
                append_filter(
                    item,
                    "Визуал",
                    f"{visual_ref}, {_page_ref(page, page_ids)}",
                    visual_ref,
                    related,
                )

    blocks: list[DocumentBlock] = [HeadingBlock(1, "Все фильтры")]
    if not rows:
        blocks.append(ParagraphBlock("Фильтры не найдены."))
        return blocks
    shown = rows[:MAX_TABLE_ROWS]
    blocks.append(
        TableBlock(
            (
                "Область",
                "Источник фильтра",
                "Поле",
                "Условие",
                "Сохранённые значения",
                "Применяется к",
                "Связанная закладка / переход",
                "Статус извлечения",
            ),
            shown,
            (900, 1_800, 1_700, 1_300, 1_900, 2_300, 2_600, 1_900),
            font_size=7.2,
            allow_row_split=True,
        )
    )
    if len(rows) > MAX_TABLE_ROWS:
        blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    return blocks


def _entity_name(name: object, fallback: str) -> str:
    return f"**«{clean_text(name) or fallback}»**"


def _named_ref(name: object, local_id: str, fallback: str, *, kind: str | None = None) -> str:
    prefix = f"{kind} " if kind else ""
    return f"{prefix}{_entity_name(name, fallback)} ({local_id})"


def _page_ref(page: PageInfo, page_ids: dict[str, str], *, compact: bool = False) -> str:
    kind = None if compact else _page_kind(page)
    return _named_ref(page.name, page_ids.get(page.id, "?"), "Лист без названия", kind=kind)


def _page_kind(page: PageInfo) -> str:
    page_type = clean_text(page.page_type).casefold()
    return "tooltip-лист" if "tooltip" in page_type else "лист"


def _visual_ref(visual: VisualInfo, local_id: str) -> str:
    return _named_ref(
        _visual_display_name(visual, local_id),
        local_id,
        "Элемент без названия",
        kind=_visual_reference_kind(visual),
    )


def _visual_reference_kind(visual: VisualInfo) -> str:
    label = _visual_type_label(visual).split(";", 1)[0]
    mapping = {
        "Кнопка навигации": "кнопка",
        "Кнопка": "кнопка",
        "Навигатор закладок": "навигатор закладок",
        "Навигатор листов": "навигатор листов",
        "Срез": "срез",
        "Текстовый блок": "текстовый блок",
        "Изображение": "изображение",
        "Фигура": "фигура",
        "Матрица": "матрица",
        "Таблица": "таблица-визуал",
        "Карточка KPI": "карточка",
        "Диаграмма": "диаграмма",
    }
    return mapping.get(label, "визуал")


def _bookmark_ref(
    bookmark: BookmarkInfo,
    bookmark_ids: dict[str, str],
    *,
    compact: bool = False,
) -> str:
    return _named_ref(
        _bookmark_display_name(bookmark),
        bookmark_ids.get(bookmark.id, "?"),
        "Закладка без названия",
        kind=None if compact else "закладка",
    )


def _visual_display_name(visual: VisualInfo, local_id: str) -> str:
    title = _human_visual_title(visual)
    if title:
        return title
    match = re.search(r"(\d+)$", clean_text(local_id))
    number = str(int(match.group(1))) if match else clean_text(local_id) or "?"
    return f"Без названия {number}"


def _human_visual_title(visual: VisualInfo) -> str | None:
    title = clean_text(visual.title)
    technical_title = title.strip("{} ")
    if (
        not title
        or title.casefold() == clean_text(visual.id).casefold()
        or TECHNICAL_VISUAL_NAME_PATTERN.fullmatch(technical_title)
    ):
        return None
    return title


def _bookmark_display_name(bookmark: BookmarkInfo) -> str:
    return clean_text(bookmark.name) or "Закладка без названия"


def _visual_type_label(visual: VisualInfo) -> str:
    technical = clean_text(visual.technical_type)
    folded = technical.casefold()
    if "bookmarknavigator" in folded:
        label = "Навигатор закладок"
    elif "pagenavigator" in folded:
        label = "Навигатор листов"
    elif "button" in folded:
        label = "Кнопка навигации" if visual.actions else "Кнопка"
    elif "slicer" in folded:
        label = "Срез"
    elif "textbox" in folded or folded == "text":
        label = "Текстовый блок"
    elif "image" in folded:
        label = "Изображение"
    elif "shape" in folded:
        label = "Фигура"
    elif "matrix" in folded:
        label = "Матрица"
    elif "table" in folded:
        label = "Таблица"
    elif "card" in folded or "kpi" in folded or "gauge" in folded:
        label = "Карточка KPI"
    elif "chart" in folded or "map" in folded or "funnel" in folded:
        label = "Диаграмма"
    elif visual.category == "CUSTOM_VISUAL":
        label = f"Пользовательский визуал ({technical})"
    else:
        label = f"Визуал ({technical})" if technical else "Визуал"
    return f"{label}; скрыт" if not visual.visible else label


def _visual_calculations(
    visual: VisualInfo,
    measure_by_key: dict[tuple[str, str], MeasureInfo],
    column_by_key: dict[tuple[str, str], ColumnInfo],
) -> str:
    values: list[str] = []
    for item in visual.fields:
        if _is_excluded_metadata(item.table):
            continue
        qualified = clean_text(item.qualified_name)
        key = ((item.table or "").casefold(), item.name.casefold())
        if item.aggregation:
            values.append(f"{qualified}: {clean_text(item.aggregation)}")
        measure = measure_by_key.get(key)
        column = column_by_key.get(key)
        expression = measure.expression if measure else column.expression if column else None
        dax = sanitize_dax(expression)
        if dax.text:
            values.append(f"{qualified}: {dax.text}")
        elif expression:
            values.append(f"{qualified}: {dax.reason or 'формула недоступна'}")
    return "; ".join(dict.fromkeys(values)) or "—"


def _composition_context(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
) -> str:
    items = [_filter_reference_text(item) for item in _visible_filters(model.report.report_filters)]
    items.extend(_filter_reference_text(item) for item in _visible_filters(page.filters))
    items.extend(_filter_reference_text(item) for item in _visible_filters(visual.filters))
    return "; ".join(dict.fromkeys(clean_text(item) for item in items)) or "—"


def _bookmark_visuals(
    bookmark: BookmarkInfo,
    pages: list[PageInfo],
) -> list[tuple[PageInfo, VisualInfo]]:
    return [
        (page, visual)
        for page in pages
        if bookmark.associated_page_id in {None, page.id}
        for visual in page.visuals
        if visual.id in bookmark.affected_visual_ids
    ]


def _bookmark_actions(
    model: DocumentationModel,
    bookmark: BookmarkInfo,
    page_by_id: dict[str, PageInfo],
    visual_by_key: dict[tuple[str, str], VisualInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
) -> str:
    values: list[str] = []
    page = page_by_id.get(bookmark.associated_page_id or "")
    if page:
        values.append(f"Открывает сохранённое состояние: {_page_ref(page, page_ids)}")
    for edge in model.report.navigation:
        if edge.target_kind != "BOOKMARK" or edge.target_id != bookmark.id:
            continue
        key = (edge.source_page_id, edge.source_visual_id)
        visual = visual_by_key.get(key)
        if visual:
            values.append(
                f"Точка входа: {_visual_ref(visual, visual_ids.get(key, '?'))}"
            )
    return "; ".join(dict.fromkeys(values)) or "—"


def _source_name(entry: FieldRegistryEntry) -> str:
    if entry.source_column:
        return clean_text(entry.source_column)
    return "—"


def _source_type(entry: FieldRegistryEntry) -> str:
    if entry.native_data_type:
        value = clean_text(entry.native_data_type)
        return re.sub(r"(^|;\s*)M:\s*", r"\1", value)
    return "—"


def _qualified_parts(value: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"\s*(?:'((?:[^']|'')+)'|([^\[]+))\[([^\]]+)\]\s*", value or "")
    if not match:
        return None
    table = (match.group(1) or match.group(2) or "").replace("''", "'").strip()
    name = match.group(3).strip()
    return (table, name) if table and name else None


def _resolve_registry_reference(
    reference: str,
    owner: FieldRegistryEntry,
    entries: dict[tuple[str, str, str], FieldRegistryEntry],
    by_name: dict[tuple[str, str], list[FieldRegistryEntry]],
) -> FieldRegistryEntry | None:
    if reference.startswith("[") and reference.endswith("]"):
        name = reference[1:-1].casefold()
        measures = [
            entry
            for (object_type, _, field_name), entry in entries.items()
            if object_type == "MEASURE" and field_name == name
        ]
        if len(measures) == 1:
            return measures[0]
        same_table = entries.get(("COLUMN", owner.table.casefold(), name))
        return same_table
    parsed = _qualified_parts(reference)
    if not parsed:
        return None
    matches = by_name.get((parsed[0].casefold(), parsed[1].casefold()), [])
    return matches[0] if len(matches) == 1 else None


def _joined(values: Iterable[str]) -> str:
    ordered = sorted({clean_text(value) for value in values if clean_text(value)}, key=str.casefold)
    return "; ".join(ordered) or "—"


def _aggregation_text(values: Iterable[str]) -> str:
    priority = {
        "по умолчанию": 0,
        "без агрегации": 1,
        "sum": 2,
        "average": 3,
        "count": 4,
        "distinct_count": 5,
        "count_non_null": 6,
        "min": 7,
        "max": 8,
        "median": 9,
        "standard_deviation": 10,
        "variance": 11,
    }
    ordered = sorted(
        {clean_text(value) for value in values if clean_text(value)},
        key=lambda value: (priority.get(value.casefold(), 99), value.casefold()),
    )
    return ", ".join(ordered) or "—"


def _difference_index(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    groups: dict[str, list[tuple[PageInfo, VisualInfo]]] = defaultdict(list)
    for page in pages:
        for visual in page.visuals:
            title = _human_visual_title(visual)
            if title:
                groups[title.casefold()].append((page, visual))
    repeated = [items for items in groups.values() if len(items) > 1]
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Одинаковое название — возможные различия"),
        ParagraphBlock(
            "Раздел сравнивает только найденные определения и контексты. "
            "Различие метаданных означает, что значения могут различаться, но не доказывает расхождение результата.",
            "lead",
        ),
    ]
    if not repeated:
        blocks.append(ParagraphBlock("Повторяющиеся видимые названия визуалов не найдены."))
        return blocks

    for items in sorted(repeated, key=lambda group: clean_text(group[0][1].title).casefold()):
        title = clean_text(items[0][1].title)
        semantic_signatures = {_semantic_signature(visual) for _, visual in items}
        context_signatures = {
            _context_signature(model, page, visual, bookmark_ids) for page, visual in items
        }
        if len(semantic_signatures) > 1:
            classification = "РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ"
        elif len(context_signatures) > 1:
            classification = "ВОЗМОЖНОЕ ВЛИЯНИЕ КОНТЕКСТА"
        else:
            classification = "UNRESOLVED"
        blocks.extend(
            [
                HeadingBlock(2, f"Показатель {_entity_name(title, 'Без названия')}"),
                ParagraphBlock(f"Классификация: {classification}", "status"),
                TableBlock(
                    ("Расположение", "Визуал", "Объект модели", "Метод", "Контекст"),
                    [
                        (
                            _page_ref(page, page_ids),
                            _visual_ref(visual, visual_ids[(page.id, visual.id)]),
                            _visual_sources(visual),
                            _visual_method(visual),
                            _context_text(model, page, visual, bookmark_ids),
                        )
                        for page, visual in items[:MAX_TABLE_ROWS]
                    ],
                    (1_800, 1_000, 2_250, 1_650, 2_660),
                ),
            ]
        )
        if len(items) > MAX_TABLE_ROWS:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    return blocks


def _semantic_model(model: DocumentationModel) -> list[DocumentBlock]:
    semantic = model.semantic_model
    tables = [item for item in semantic.tables if not _is_excluded_metadata(item.name)]
    measures = [item for item in semantic.measures if not _is_excluded_metadata(item.table)]
    relationships = [
        item
        for item in semantic.relationships
        if not _is_internal_field(item.from_object) and not _is_internal_field(item.to_object)
    ]
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Семантическая модель и связи"),
        ParagraphBlock(f"Статус локальной модели: {_display_or_dash(semantic.status)}", "status"),
    ]
    if not tables and not measures:
        blocks.append(ParagraphBlock("Локальная семантическая модель недоступна.", "warning"))
        return blocks
    if tables:
        blocks.extend(
            [
                HeadingBlock(2, "Таблицы"),
                TableBlock(
                    ("Таблица", "Скрыта", "Режим", "Колонок"),
                    [
                        (
                            f"таблица {_entity_name(item.name, 'Таблица без названия')}",
                            yes_no(item.hidden),
                            clean_text(item.storage_mode or "—"),
                            str(len(item.columns)),
                        )
                        for item in tables[:MAX_TABLE_ROWS]
                    ],
                    (3_000, 1_400, 2_600, 2_360),
                ),
                HeadingBlock(2, "Колонки и безопасные примеры"),
            ]
        )
        columns = [
            column
            for table in tables
            for column in table.columns
            if not _is_excluded_metadata(column.table)
        ]
        blocks.append(
            TableBlock(
                ("Таблица / колонка", "Тип", "Скрыта", "Уникальных", "Примеры / статус"),
                [
                    (
                        f"{clean_text(item.table)}[{clean_text(item.name)}]",
                        _display_or_dash(item.data_type),
                        yes_no(item.hidden),
                        str(item.cardinality) if item.cardinality is not None else "—",
                        "; ".join(clean_text(value) for value in item.examples)
                        or _example_status_label(item.example_status),
                    )
                    for item in columns[:MAX_TABLE_ROWS]
                ],
                (2_600, 1_350, 1_100, 1_350, 2_960),
            )
        )
        if len(columns) > MAX_TABLE_ROWS:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    if relationships:
        blocks.extend(
            [
                HeadingBlock(2, "Связи"),
                TableBlock(
                    ("Из", "В", "Кардинальность", "Фильтрация", "Статус / RLS"),
                    [
                        (
                            clean_text(item.from_object),
                            clean_text(item.to_object),
                            clean_text(item.cardinality or "—"),
                            _cross_filter_label(item.filter_direction),
                            f"Активна: {yes_no(item.active)}; передача RLS: "
                            f"{_security_filter_label(item.security_filter)}",
                        )
                        for item in relationships[:MAX_TABLE_ROWS]
                    ],
                    (2_250, 2_250, 1_500, 1_550, 1_810),
                ),
            ]
        )
    return blocks


def _security_and_sources(model: DocumentationModel) -> list[DocumentBlock]:
    semantic = model.semantic_model
    rls = [item for item in semantic.rls if not _is_excluded_metadata(item.table)]
    ols = [item for item in semantic.ols if not _is_excluded_metadata(item.table)]
    blocks: list[DocumentBlock] = [HeadingBlock(1, "Power Query, источники и безопасность")]
    blocks.append(HeadingBlock(2, "Power Query и подключения"))
    queries = [item for item in semantic.queries if not _is_excluded_metadata(item.name)]
    if queries:
        shown_queries = queries[:MAX_TABLE_ROWS]
        blocks.append(
            TableBlock(
                (
                    "Запрос",
                    "Тип",
                    "Коннектор",
                    "Сервер / база / путь",
                    "Строка подключения",
                ),
                [
                    (
                        f"запрос {_entity_name(item.name, 'Запрос без названия')}",
                        _query_kind_label(item.kind),
                        ", ".join(item.connection_types) or "—",
                        "; ".join(item.source_details) or "—",
                        _query_connection_cell(item),
                    )
                    for item in shown_queries
                ],
                (2_300, 1_500, 1_900, 4_100, 4_600),
                font_size=7.2,
                allow_row_split=True,
            )
        )
        if len(queries) > len(shown_queries):
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    else:
        blocks.append(ParagraphBlock("Метаданные Power Query не найдены или недоступны."))
    if semantic.connections:
        blocks.extend(
            [
                HeadingBlock(3, "Подключения модели"),
                TableBlock(
                    ("Тип", "Сервер / база / путь", "Строка подключения"),
                    [
                        (
                            _display_or_dash(item.connection_type),
                            "; ".join(item.source_details) or "—",
                            clean_text(item.connection_string) or "—",
                        )
                        for item in semantic.connections[:MAX_TABLE_ROWS]
                    ],
                    (2_200, 5_300, 6_900),
                    font_size=7.5,
                    allow_row_split=True,
                ),
            ]
        )
        if len(semantic.connections) > MAX_TABLE_ROWS:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    elif not queries:
        blocks.append(
            ParagraphBlock(
                "Типы подключений: "
                + (", ".join(clean_text(value) for value in semantic.connection_types) or "не определены"),
                "note",
            )
        )
    blocks.append(HeadingBlock(2, "RLS и OLS"))
    if rls:
        rows: list[tuple[str, ...]] = []
        for rule in rls[:MAX_TABLE_ROWS]:
            dax = sanitize_dax(rule.expression)
            rows.append(
                (
                    f"роль {_entity_name(rule.role, 'Роль без названия')}",
                    f"таблица {_entity_name(rule.table, 'Таблица не указана')}" if rule.table else "—",
                    clean_text(dax.text or dax.reason or "PARTIAL"),
                    clean_text(rule.permission or "—"),
                )
            )
        blocks.append(TableBlock(("Роль", "Таблица", "DAX-фильтр", "Разрешение"), rows, (2_100, 1_900, 3_760, 1_600)))
    else:
        blocks.append(ParagraphBlock("Метаданные RLS не найдены или недоступны."))
    if ols:
        blocks.append(
            TableBlock(
                ("Роль", "Таблица", "Колонка", "Разрешение"),
                [
                    (
                        f"роль {_entity_name(rule.role, 'Роль без названия')}",
                        f"таблица {_entity_name(rule.table, 'Таблица не указана')}" if rule.table else "—",
                        clean_text(rule.column or "—"),
                        clean_text(rule.permission or "—"),
                    )
                    for rule in ols[:MAX_TABLE_ROWS]
                ],
                (2_400, 2_300, 2_660, 2_000),
            )
        )
    else:
        blocks.append(ParagraphBlock("Метаданные OLS не найдены или недоступны."))
    return blocks


def _query_connection_cell(query: QueryInfo) -> str:
    parts = [
        clean_text(value, preserve_newlines=True, limit=None)
        for value in query.connection_strings
    ]
    expressions = [
        clean_text(value, preserve_newlines=True, limit=None)
        for value in query.expressions
        if clean_text(value, preserve_newlines=True, limit=None)
    ]
    for index, expression in enumerate(expressions, 1):
        label = "M-код" if len(expressions) == 1 else f"M-код, вариант {index}"
        parts.append(f"{label}:\n{expression}")
    return "\n\n".join(parts) or "—"


def sanitize_dax(expression: str | None) -> DaxProjection:
    if not expression:
        return DaxProjection(None, "NOT_AVAILABLE", [], [], "Определение DAX недоступно")
    text = clean_text(expression, preserve_newlines=True)
    output: list[str] = []
    index = 0
    malformed = False
    while index < len(text):
        if text.startswith("//", index):
            output.append(" ")
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            output.append(" ")
            end = text.find("*/", index + 2)
            if end < 0:
                malformed = True
                break
            index = end + 2
            continue
        char = text[index]
        if char == '"':
            index += 1
            closed = False
            while index < len(text):
                if text[index] == '"':
                    if index + 1 < len(text) and text[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                index += 1
            if not closed:
                malformed = True
                break
            output.append('"<VALUE>"')
            continue
        if char == "'":
            start = index
            index += 1
            closed = False
            while index < len(text):
                if text[index] == "'":
                    if index + 1 < len(text) and text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                index += 1
            if not closed:
                malformed = True
                break
            output.append(text[start:index])
            continue
        output.append(char)
        index += 1
    if malformed:
        return DaxProjection(None, "PARTIAL", [], [], "DAX скрыт: выражение не удалось безопасно разобрать")
    sanitized = _compact_dax_whitespace("".join(output))
    sanitized, redacted = _redact_excluded_dax_references(sanitized)
    references = _dax_references(sanitized)
    functions = sorted(
        {match.group(1).upper() for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", sanitized)}
    )
    status = "PARTIAL" if redacted else "OK"
    if len(sanitized) > MAX_DAX_CHARS:
        sanitized = sanitized[:MAX_DAX_CHARS].rstrip() + f" {OVERFLOW}"
        status = "PARTIAL"
    return DaxProjection(sanitized, status, references[:MAX_DEPENDENCIES], functions)


def _compact_dax_whitespace(expression: str) -> str:
    protected: list[str] = []
    output: list[str] = []
    index = 0
    while index < len(expression):
        delimiter = expression[index]
        if delimiter not in {'"', "'", "["}:
            output.append(delimiter)
            index += 1
            continue
        closing = "]" if delimiter == "[" else delimiter
        end = index + 1
        while end < len(expression):
            if expression[end] != closing:
                end += 1
                continue
            if end + 1 < len(expression) and expression[end + 1] == closing:
                end += 2
                continue
            end += 1
            break
        token = expression[index:end]
        marker = f"\ue000{len(protected)}\ue001"
        protected.append(token)
        output.append(marker)
        index = end

    compact = re.sub(r"\s+", " ", "".join(output)).strip()
    compact = re.sub(r"\s*[,;]\s*", lambda match: f"{match.group(0).strip()} ", compact)
    compact = re.sub(r"\s+\(", "(", compact)
    compact = re.sub(r"\(\s+", "(", compact)
    compact = re.sub(r"\s+\)", ")", compact)
    compact = re.sub(r"\{\s+", "{", compact)
    compact = re.sub(r"\s+\}", "}", compact)
    for index, token in enumerate(protected):
        compact = compact.replace(f"\ue000{index}\ue001", token)
    return compact.strip()


def _redact_excluded_dax_references(expression: str) -> tuple[str, bool]:
    redacted = False

    def replace(match: re.Match[str]) -> str:
        nonlocal redacted
        table = (match.group(1) or match.group(2) or "").replace("''", "'").strip()
        if not _is_excluded_metadata(table):
            return match.group(0)
        redacted = True
        return "<СЛУЖЕБНАЯ ССЫЛКА>"

    return DAX_QUALIFIED_REFERENCE_PATTERN.sub(replace, expression), redacted


def _dax_references(expression: str) -> list[str]:
    qualified: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in DAX_QUALIFIED_REFERENCE_PATTERN.finditer(expression):
        table = (match.group(1) or match.group(2) or "").replace("''", "'").strip()
        qualified.append(f"{table}[{match.group(3).strip()}]")
        spans.append(match.span())
    unqualified = []
    for match in re.finditer(r"\[([^\]]+)\]", expression):
        if not any(start <= match.start() < end for start, end in spans):
            unqualified.append(f"[{match.group(1).strip()}]")
    return list(dict.fromkeys(qualified + unqualified))


def _context_items(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
    bookmark_ids: dict[str, str],
) -> list[str]:
    items = [_filter_reference_text(item) for item in _visible_filters(model.report.report_filters)]
    items.extend(_filter_reference_text(item) for item in _visible_filters(page.filters))
    items.extend(_filter_reference_text(item) for item in _visible_filters(visual.filters))
    for bookmark in model.report.bookmarks:
        if bookmark.associated_page_id == page.id and visual.id in bookmark.affected_visual_ids:
            items.append(
                f"{_bookmark_ref(bookmark, bookmark_ids)}: "
                f"{_bookmark_state_text(bookmark)}"
            )
    return [clean_text(item) for item in items]


def _context_text(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
    bookmark_ids: dict[str, str],
) -> str:
    items = _context_items(model, page, visual, bookmark_ids)
    return "; ".join(items) if items else "Явные фильтры и закладки не обнаружены"


def _context_signature(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
    bookmark_ids: dict[str, str],
) -> tuple[str, ...]:
    return tuple(sorted(_context_items(model, page, visual, bookmark_ids), key=str.casefold))


def _semantic_signature(visual: VisualInfo) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        sorted(
            (
                item.qualified_name.casefold(),
                item.object_type.casefold(),
                (item.role or "").casefold(),
                (item.aggregation or "").casefold(),
            )
            for item in visual.fields
            if not _is_excluded_metadata(item.table)
        )
    )


def _visual_sources(visual: VisualInfo) -> str:
    return "; ".join(
        dict.fromkeys(
            clean_text(item.qualified_name)
            for item in visual.fields
            if not _is_excluded_metadata(item.table)
        )
    ) or "—"


def _visual_method(visual: VisualInfo, *, include_sources: bool = False) -> str:
    values = []
    for item in visual.fields:
        if _is_excluded_metadata(item.table):
            continue
        method = _method_label(item.aggregation, item.object_type, item.usage)
        value = f"{item.qualified_name}: {method}" if include_sources else method
        values.append(clean_text(value))
    return "; ".join(dict.fromkeys(values)) or "—"


def _actions_text(
    model: DocumentationModel,
    page_id: str,
    visual: VisualInfo,
    page_ids: dict[str, str],
    bookmark_ids: dict[str, str],
) -> str:
    edges = [
        edge
        for edge in model.report.navigation
        if edge.source_page_id == page_id and edge.source_visual_id == visual.id
    ]
    return "; ".join(
        f"{_action_label(edge.action_type)} → {_edge_target(edge, page_ids, bookmark_ids)}" for edge in edges
    ) or "—"


def _edge_source_page_ref(
    edge: object,
    page_by_id: dict[str, PageInfo],
    page_ids: dict[str, str],
) -> str:
    page_id = getattr(edge, "source_page_id")
    page = page_by_id.get(page_id)
    if page:
        return _page_ref(page, page_ids)
    return _named_ref(
        getattr(edge, "source_page_name"),
        page_ids.get(page_id, "?"),
        "Лист без названия",
        kind="лист",
    )


def _edge_source_visual_ref(
    edge: object,
    visual_by_key: dict[tuple[str, str], VisualInfo],
    visual_ids: dict[tuple[str, str], str],
) -> str:
    key = (getattr(edge, "source_page_id"), getattr(edge, "source_visual_id"))
    visual = visual_by_key.get(key)
    if visual:
        return _visual_ref(visual, visual_ids.get(key, "?"))
    return _named_ref(
        getattr(edge, "source_visual_title"),
        visual_ids.get(key, "?"),
        "Элемент без названия",
        kind="элемент",
    )


def _edge_target(edge: object, page_ids: dict[str, str], bookmark_ids: dict[str, str]) -> str:
    target_kind = getattr(edge, "target_kind")
    target_id = getattr(edge, "target_id")
    target_name = clean_text(getattr(edge, "target_name") or "—")
    if target_kind == "PAGE" and getattr(edge, "target_page_id") in page_ids:
        return _named_ref(
            target_name,
            page_ids[getattr(edge, "target_page_id")],
            "Лист без названия",
            kind="лист",
        )
    if target_kind == "BOOKMARK" and target_id in bookmark_ids:
        page = getattr(edge, "target_page_id")
        result = _named_ref(
            target_name,
            bookmark_ids[target_id],
            "Закладка без названия",
            kind="закладка",
        )
        if page in page_ids:
            page_name = clean_text(getattr(edge, "target_page_name") or "")
            result += f", {_named_ref(page_name, page_ids[page], 'Лист без названия', kind='лист')}"
        return result
    return f"{_target_kind_label(clean_text(target_kind))}: {target_name}"


def _visible_filters(filters: Iterable[FilterInfo]) -> list[FilterInfo]:
    return [item for item in filters if not _is_excluded_metadata(item.field)]


def _filter_reference_text(item: FilterInfo) -> str:
    return f"{_filter_scope_label(item.scope)} по полю {clean_text(item.field)}"


def _saved_values_text(values: Iterable[str], count: int) -> str:
    shown = [_literal_display(value) for value in values if clean_text(value)]
    text = ", ".join(shown) or "—"
    hidden_count = max(0, count - len(shown))
    return f"{text} … ещё {hidden_count}" if hidden_count else text


def _literal_display(value: str) -> str:
    text = clean_text(value)
    return {"true": "Да", "false": "Нет", "null": "Пусто"}.get(text.casefold(), text)


def _bookmark_state_text(bookmark: BookmarkInfo) -> str:
    return _bookmark_filter_state_text(bookmark)


def _bookmark_filter_state_text(bookmark: BookmarkInfo) -> str:
    scope = _scope_label(bookmark.state_scope)
    if scope == "—":
        return "—"
    suffix = "; значения полей: —" if bookmark.saved_value_count else ""
    return f"{scope}{suffix}"


def _bookmark_effect_text(bookmark: BookmarkInfo, page: PageInfo | None) -> str:
    if bookmark.affected_visual_ids:
        return "Восстанавливает сохранённое состояние указанных элементов"
    if page:
        return "Восстанавливает сохранённое состояние листа"
    return "Восстанавливает сохранённое состояние"


def _filter_extraction_status(item: FilterInfo) -> str:
    if not clean_text(item.field):
        return "Поле не определено"
    shown = len([value for value in item.saved_values if clean_text(value)])
    if item.saved_value_count > shown:
        return "Поле определено; показана часть значений"
    if shown:
        return "Поле и значения определены"
    return "Поле определено; значения не сохранены"


def _filter_scope_label(value: str) -> str:
    labels = {
        "REPORT": "Фильтр отчёта",
        "PAGE": "Фильтр листа",
        "VISUAL": "Фильтр визуала",
    }
    return labels.get(value.upper(), clean_text(value).replace("_", " ").capitalize())


def _filter_type_label(value: str) -> str:
    labels = {
        "in": "Выбраны значения",
        "not in": "Исключены значения",
        "between": "Диапазон",
        "contains": "Содержит",
        "not contains": "Не содержит",
        "starts with": "Начинается с",
        "ends with": "Заканчивается на",
        "greater than": "Больше",
        "greater than or equal": "Больше или равно",
        "less than": "Меньше",
        "less than or equal": "Меньше или равно",
        "equals": "Равно",
        "not equals": "Не равно",
        "top n": "Первые N",
        "categorical": "Выбраны значения",
        "advanced": "Условие",
        "relative": "Относительный",
        "relative date": "Относительная дата",
        "topn": "Первые N",
        "basic": "Базовый",
        "unknown": "—",
    }
    normalized = clean_text(value).replace("_", " ")
    return labels.get(normalized.casefold(), normalized or "—")


def _security_filter_label(value: str | None) -> str:
    raw = clean_text(value).casefold()
    codes = {
        "1": "по направлению связи",
        "2": "в обе стороны",
        "3": "не передаётся",
    }
    if raw in codes:
        return codes[raw]
    normalized = re.sub(r"[^a-z]", "", raw)
    labels = {
        "bothdirections": "в обе стороны",
        "onedirection": "по направлению связи",
        "none": "не передаётся",
        "true": "включена",
        "false": "не передаётся",
    }
    return labels.get(normalized, _display_or_dash(value))


def _query_kind_label(value: str | None) -> str:
    normalized = clean_text(value).replace("_", " ").casefold()
    return {
        "power query": "Power Query",
        "query": "Power Query",
        "function": "Функция Power Query",
    }.get(normalized, _display_or_dash(value))


def _cross_filter_label(value: str | None) -> str:
    raw = clean_text(value).casefold()
    labels = {
        "1": "Односторонняя",
        "2": "Двусторонняя",
        "3": "Автоматическая",
        "single": "Односторонняя",
        "onedirection": "Односторонняя",
        "both": "Двусторонняя",
        "bothdirections": "Двусторонняя",
        "automatic": "Автоматическая",
    }
    normalized = re.sub(r"[^a-z]", "", raw)
    return labels.get(raw, labels.get(normalized, _display_or_dash(value)))


def _issue_summary(model: DocumentationModel) -> str:
    errors = sum(item.severity == "ERROR" for item in model.issues)
    warnings = sum(item.severity == "WARNING" for item in model.issues)
    return f"Ошибок: {errors}; предупреждений: {warnings}"


def _overall_status_label(value: str) -> str:
    return {"OK": "Полное", "PARTIAL": "Частичное", "FAILED": "Не сформировано"}.get(
        clean_text(value).upper(),
        clean_text(value) or "—",
    )


def _semantic_status_label(value: str) -> str:
    return {
        "EMBEDDED": "Доступна",
        "NO_MODEL": "Локальная модель отсутствует",
        "PARTIAL": "Доступна частично",
        "FAILED": "Недоступна",
    }.get(clean_text(value).upper(), _display_or_dash(value))


def _method_label(aggregation: str | None, object_type: str, usage: str) -> str:
    if aggregation:
        return "агрегация" if aggregation.casefold() == "aggregation" else clean_text(aggregation)
    if object_type == "MEASURE":
        return "мера DAX"
    labels = {"VISUAL_DATA": "поле визуала", "SLICER": "срез", "TOOLTIP": "подсказка"}
    return labels.get(usage, clean_text(usage))


def _action_label(value: str) -> str:
    labels = {
        "PAGE_NAVIGATION": "Переход на лист",
        "BOOKMARK_NAVIGATION": "Переход по закладке",
        "REPORT_TOOLTIP": "Tooltip",
        "DRILLTHROUGH": "Drillthrough",
        "WEB_URL": "Внешняя ссылка",
    }
    return labels.get(value, clean_text(value).replace("_", " ").title())


def _navigation_effect(action_type: str, target_kind: str, target: str) -> str:
    if action_type == "REPORT_TOOLTIP":
        return f"Показывает {target}"
    if action_type == "DRILLTHROUGH":
        return f"Открывает детализацию: {target}"
    if target_kind == "PAGE":
        return f"Открывает {target}"
    if target_kind == "EXTERNAL":
        return f"Открывает внешнюю цель: {target}"
    return f"Активирует {target}"


def _target_kind_label(value: str) -> str:
    return {"PAGE": "лист", "BOOKMARK": "закладка", "EXTERNAL": "внешняя цель"}.get(
        value, clean_text(value).lower()
    )


def _scope_label(value: str | None) -> str:
    if not value:
        return "—"
    labels = {
        "CAPTURED_STATE": "сохранённое состояние",
        "DATA": "данные",
        "DISPLAY": "отображение",
        "CURRENT_PAGE": "текущий лист",
    }
    return labels.get(value, clean_text(value).replace("_", " ").lower())


def _is_excluded_metadata(value: str | None) -> bool:
    return METADATA_EXCLUSION_POLICY.excludes(value)


def _is_internal_field(value: str | None) -> bool:
    return _is_excluded_metadata(value)


def _display_or_dash(value: object) -> str:
    text = clean_text(value)
    technical_empty = {
        "",
        "not_available",
        "not_found_or_not_available",
        "unknown",
        "none",
        "null",
    }
    return "—" if text.casefold() in technical_empty else text


def _example_status_label(value: str | None) -> str:
    labels = {
        "OK": "—",
        "PROFILED": "—",
        "NO_VALUES": "Нет значений",
        "NOT_PROFILED": "Не проверялось",
        "PROFILE_FAILED": "Не удалось получить",
        "IDENTIFIER_OR_HIDDEN": "Служебное или скрытое поле",
        "HIGH_OR_UNKNOWN_CARDINALITY": "Нет примеров: >200 значений или число неизвестно",
        "DATE_VALUES_HIDDEN": "Дата; примеры не выводятся",
        "NUMERIC_VALUES_HIDDEN": "Числовое поле; примеры не выводятся",
    }
    return labels.get(clean_text(value).upper(), "—")


def clean_text(value: object, *, preserve_newlines: bool = False, limit: int | None = 4_000) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\t\n\r" or ord(char) >= 32)
    if preserve_newlines:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    else:
        text = " ".join(text.replace("\t", " ").splitlines())
    text = text.strip()
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def yes_no(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Да" if value else "Нет"
