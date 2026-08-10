from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .domain import DocumentationModel, FilterInfo, MeasureInfo, PageInfo, VisualInfo


MAX_DAX_CHARS = 8_000
MAX_DEPENDENCIES = 100
MAX_TABLE_ROWS = 500
MAX_FIELD_REGISTRY_ROWS = 5_000
MAX_VISUALS = 1_000
TABLE_WIDTH_DXA = 9_360
LANDSCAPE_TABLE_WIDTH_DXA = 14_400
OVERFLOW = "<PARTIAL — ЧАСТЬ СВЕДЕНИЙ НЕ ВКЛЮЧЕНА ИЗ-ЗА ЛИМИТА ДОКУМЕНТА>"


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
class CodeBlock:
    text: str
    status: str = "OK"


@dataclass(slots=True)
class SectionBreakBlock:
    orientation: str


DocumentBlock = HeadingBlock | ParagraphBlock | ListBlock | TableBlock | CodeBlock | SectionBreakBlock


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
class KpiProjection:
    local_id: str
    key: tuple[str, ...]
    semantic_source: str
    display_names: list[str]
    method: str
    dependencies: list[str]
    description: str | None
    dax: DaxProjection
    usages: list[tuple[str, str, str, str, str]]
    status: str


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
    visual_by_raw: dict[str, list[str]] = defaultdict(list)
    visual_counter = 1
    for page in pages:
        for visual in page.visuals:
            local_id = f"V{visual_counter:03d}"
            visual_counter += 1
            visual_ids[(page.id, visual.id)] = local_id
            visual_by_raw[visual.id].append(local_id)
    bookmark_ids = {
        bookmark.id: f"B{index:02d}"
        for index, bookmark in enumerate(sorted(report.bookmarks, key=lambda item: item.order), 1)
    }
    kpis = _project_kpis(model, page_ids, visual_ids, bookmark_ids)

    blocks: list[DocumentBlock] = []
    blocks.extend(_passport(model, pages, page_ids))
    blocks.extend(_field_registry(model, pages, page_ids, visual_ids))
    blocks.extend(_difference_index(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_kpi_catalog(kpis))
    blocks.extend(_page_guide(model, pages, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_interactions(model, page_ids, visual_ids, visual_by_raw, bookmark_ids))
    blocks.extend(_journeys(model, page_ids, visual_ids, bookmark_ids))
    blocks.extend(_semantic_model(model))
    blocks.extend(_security_and_sources(model))
    blocks.extend(_diagnostics(model, page_ids, visual_ids, bookmark_ids))

    return SafeDocumentModel(
        title=clean_text(report.title) or model.file.path.stem,
        subtitle="Руководство по расчётам, контекстам и навигации Power BI",
        source_name=clean_text(model.file.path.name),
        generated_at=model.generated_at,
        overall_status=overall_status(model),
        blocks=blocks,
    )


def _passport(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
) -> list[DocumentBlock]:
    report = model.report
    semantic = model.semantic_model
    visuals = [visual for page in pages for visual in page.visuals]
    fields = [item for visual in visuals for item in visual.fields]
    unresolved = sum(item.status != "RESOLVED" for item in fields)
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Паспорт отчёта"),
        ParagraphBlock(
            "Документ описывает устройство отчёта по доступным локальным метаданным. "
            "Он не вычисляет DAX и не подтверждает фактические значения показателей.",
            "lead",
        ),
        TableBlock(
            ("Параметр", "Значение"),
            [
                ("Файл", clean_text(model.file.path.name)),
                ("Источник", "Выбранный файл в защищённой среде VDI"),
                ("Размер", f"{model.file.size_bytes:,} байт".replace(",", " ")),
                ("SHA-256", clean_text(model.file.sha256)),
                ("Формат отчёта", f"{report.report_format} {report.format_version or ''}".strip()),
                ("Версия генератора", clean_text(model.application_version)),
                ("Версия PBIXRay", clean_text(semantic.library_version or "NOT_AVAILABLE")),
                ("Профиль значений", "SAFE_TOP_5_CATEGORICAL"),
            ],
            (2_700, 6_660),
        ),
        HeadingBlock(2, "Статус извлечения"),
        TableBlock(
            ("Компонент", "Статус", "Подробности"),
            [
                ("Общий результат", overall_status(model), _issue_summary(model)),
                ("Листы", "OK" if pages else "FAILED", str(len(pages))),
                ("Визуалы", "OK", str(len(visuals))),
                ("Закладки", "OK", str(len(report.bookmarks))),
                ("Переходы", "OK", str(len(report.navigation))),
                (
                    "Ссылки на поля",
                    "PARTIAL" if unresolved else "OK",
                    f"Разрешено {len(fields) - unresolved} из {len(fields)}",
                ),
                (
                    "Семантическая модель",
                    semantic.status,
                    f"Таблиц: {len(semantic.tables)}; мер: {len(semantic.measures)}",
                ),
                ("RLS/OLS", "PROVISIONAL", "Формальная заглушка до боевого прогона"),
            ],
            (2_600, 1_700, 5_060),
        ),
        HeadingBlock(2, "Как читать документ"),
        TableBlock(
            ("Обозначение", "Смысл"),
            [
                ("РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ", "Отличаются объекты модели, DAX, зависимости, роли или агрегации."),
                ("ВОЗМОЖНОЕ ВЛИЯНИЕ КОНТЕКСТА", "Отличаются найденные фильтры или состояние закладки."),
                ("UNRESOLVED", "Метаданных недостаточно для надёжного вывода."),
                ("PARTIAL", "Документ полезен, но часть структуры недоступна или не распознана."),
                ("<VALUES HIDDEN>", "Фактические значения фильтров и срезов намеренно не выводятся."),
            ],
            (3_150, 6_210),
        ),
        HeadingBlock(2, "Карта листов"),
        TableBlock(
            ("ID", "Лист", "Тип", "Видимость", "Визуалов"),
            [
                (
                    page_ids[page.id],
                    clean_text(page.name),
                    clean_text(page.page_type),
                    "VISIBLE" if page.visible else "HIDDEN",
                    str(len(page.visuals)),
                )
                for page in pages
            ],
            (900, 3_000, 1_800, 1_800, 1_860),
        ),
    ]
    blocks.append(HeadingBlock(2, "Фильтры уровня отчёта"))
    if report.report_filters:
        blocks.append(_filter_table(report.report_filters))
    else:
        blocks.append(ParagraphBlock("Фильтры уровня отчёта не найдены."))
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
        page_label = f"{page_ids[page.id]} · {clean_text(page.name)}"
        for visual in page.visuals:
            visual_label = (
                f"{visual_ids[(page.id, visual.id)]} · {clean_text(visual.title)} "
                f"({page_ids[page.id]})"
            )
            for item in visual.fields:
                entry = resolve_entry(item.table, item.name, item.object_type)
                if clean_text(visual.title):
                    entry.aliases.add(clean_text(visual.title))
                entry.pages.add(page_label)
                entry.visuals.add(visual_label)
                if item.aggregation:
                    entry.aggregations.add(
                        f"{clean_text(item.aggregation)} — {page_ids[page.id]} / "
                        f"{visual_ids[(page.id, visual.id)]}"
                    )
            for item in visual.filters:
                parsed = _qualified_parts(item.field)
                if parsed:
                    entry = resolve_entry(parsed[0], parsed[1], "COLUMN")
                    entry.pages.add(f"{page_label} (фильтр визуала)")
                    entry.visuals.add(visual_label)

        for item in page.filters:
            parsed = _qualified_parts(item.field)
            if parsed:
                entry = resolve_entry(parsed[0], parsed[1], "COLUMN")
                entry.aliases.add("Фильтр листа")
                entry.pages.add(f"{page_label} (фильтр листа)")

    for item in model.report.report_filters:
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
        (entry for entry in entries.values() if not entry.table.casefold().startswith("local")),
        key=lambda item: (item.table.casefold(), item.name.casefold(), item.object_type),
    )
    for entry in ordered[:MAX_FIELD_REGISTRY_ROWS]:
        dax = sanitize_dax(entry.dax_expression)
        dax_text = dax.text or (dax.reason if entry.dax_expression else "—")
        aggregations = set(entry.aggregations)
        if entry.summarize_by:
            value = clean_text(entry.summarize_by)
            aggregations.add("По умолчанию" if value == "По умолчанию" else f"По умолчанию: {value}")
        rows.append(
            (
                f"{clean_text(entry.table)}[{clean_text(entry.name)}]",
                _joined(entry.aliases),
                clean_text(entry.data_type or "NOT_AVAILABLE"),
                "",
                clean_text(dax_text, preserve_newlines=True, limit=MAX_DAX_CHARS + len(OVERFLOW)),
                _joined(entry.pages),
                _source_name(entry),
                _source_type(entry),
                _joined(entry.dax_usages),
                _joined(entry.visuals),
                _joined(aggregations),
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
                    "Наименование в источнике",
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
    blocks.append(SectionBreakBlock("portrait"))
    return blocks


def _source_name(entry: FieldRegistryEntry) -> str:
    if entry.object_type == "MEASURE":
        return "Не применимо: мера вычисляется в модели и не имеет исходной колонки"
    if entry.source_column:
        return clean_text(entry.source_column)
    if entry.dax_expression:
        return "Не применимо: вычисляемый столбец создаётся в модели"
    return "NOT_AVAILABLE: имя исходной колонки отсутствует в локальной метадате PBIX"


def _source_type(entry: FieldRegistryEntry) -> str:
    if entry.object_type == "MEASURE":
        return "Не применимо: тип результата меры задаётся моделью"
    if entry.native_data_type:
        return clean_text(entry.native_data_type)
    if entry.dax_expression and not entry.source_column:
        return "Не применимо: вычисляемый столбец типизируется в модели"
    return "NOT_AVAILABLE: тип исходной колонки отсутствует в локальной метадате PBIX"


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
            title = clean_text(visual.title).strip()
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
                HeadingBlock(2, title),
                ParagraphBlock(f"Классификация: {classification}", "status"),
                TableBlock(
                    ("Расположение", "Визуал", "Объект модели", "Метод", "Контекст"),
                    [
                        (
                            f"{page_ids[page.id]} · {clean_text(page.name)}",
                            visual_ids[(page.id, visual.id)],
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


def _project_kpis(
    model: DocumentationModel,
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[KpiProjection]:
    measures = {(item.table.casefold(), item.name.casefold()): item for item in model.semantic_model.measures}
    candidates: dict[tuple[str, ...], dict[str, object]] = {}
    for measure in sorted(model.semantic_model.measures, key=lambda item: (item.table.casefold(), item.name.casefold())):
        key = ("MEASURE", measure.table.casefold(), measure.name.casefold())
        candidates[key] = {"measure": measure, "fields": []}

    for page in sorted(model.report.pages, key=lambda item: item.order):
        for visual in page.visuals:
            for item in visual.fields:
                if item.object_type == "MEASURE":
                    key = ("MEASURE", (item.table or "").casefold(), item.name.casefold())
                elif item.aggregation:
                    key = (
                        "AGGREGATION",
                        (item.table or "").casefold(),
                        item.name.casefold(),
                        item.aggregation.casefold(),
                    )
                else:
                    continue
                entry = candidates.setdefault(key, {"measure": None, "fields": []})
                if key[0] == "MEASURE" and entry["measure"] is None:
                    entry["measure"] = measures.get((key[1], key[2]))
                entry["fields"].append((page, visual, item))  # type: ignore[union-attr]

    local_ids = {key: f"K{index:03d}" for index, key in enumerate(candidates, 1)}
    measure_by_name: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for key in candidates:
        if key[0] == "MEASURE":
            measure_by_name[key[2]].append(key)

    dax_by_key: dict[tuple[str, ...], DaxProjection] = {}
    for key, entry in candidates.items():
        measure = entry["measure"]
        dax_by_key[key] = sanitize_dax(measure.expression) if isinstance(measure, MeasureInfo) else DaxProjection(
            None, "NOT_AVAILABLE", [], [], "Определение отсутствует в локальной модели"
        )

    result: list[KpiProjection] = []
    for key, entry in candidates.items():
        measure = entry["measure"]
        usage_items = list(entry["fields"])  # type: ignore[arg-type]
        if isinstance(measure, MeasureInfo):
            source = f"{measure.table}[{measure.name}]"
            method = "Мера DAX"
            description = clean_text(measure.description) if measure.description else None
        else:
            sample = usage_items[0][2] if usage_items else None
            source = sample.qualified_name if sample else "<UNKNOWN>"
            method = (
                _method_label(sample.aggregation, sample.object_type, sample.usage).capitalize()
                if sample and sample.aggregation
                else "Мера без локального определения"
            )
            description = None
        aliases = sorted(
            {clean_text(visual.title) for _, visual, _ in usage_items if clean_text(visual.title)},
            key=str.casefold,
        )
        usages = [
            (
                f"{page_ids[page.id]} · {clean_text(page.name)}",
                f"{visual_ids[(page.id, visual.id)]} · {clean_text(visual.title)}",
                clean_text(item.role or "—"),
                _method_label(item.aggregation, item.object_type, item.usage),
                _context_text(model, page, visual, bookmark_ids),
            )
            for page, visual, item in usage_items[:MAX_TABLE_ROWS]
        ]
        dax = dax_by_key[key]
        dependencies = _dependency_paths(
            key,
            dax_by_key,
            candidates,
            local_ids,
            measure_by_name,
        )
        status = "OK" if (dax.status == "OK" or key[0] == "AGGREGATION") else "PARTIAL"
        if any(item[2].status != "RESOLVED" for item in usage_items):
            status = "PARTIAL"
        result.append(
            KpiProjection(
                local_ids[key],
                key,
                clean_text(source),
                aliases,
                method,
                dependencies,
                description,
                dax,
                usages,
                status,
            )
        )
    return result


def _kpi_catalog(kpis: list[KpiProjection]) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Каталог KPI и вычислений"),
        ParagraphBlock(
            "Зависимости показаны символически по формулам и привязкам визуалов. "
            "Описание модели приводится как авторская метадата и не является выводом программы.",
            "lead",
        ),
    ]
    if not kpis:
        blocks.append(ParagraphBlock("Меры и агрегированные показатели не найдены."))
        return blocks
    for item in kpis:
        display = ", ".join(item.display_names) or item.semantic_source
        blocks.extend(
            [
                HeadingBlock(2, f"{item.local_id} · {display}"),
                TableBlock(
                    ("Параметр", "Значение"),
                    [
                        ("Объект модели", item.semantic_source),
                        ("Метод", item.method),
                        ("Видимые названия", "; ".join(item.display_names) or "—"),
                        ("Зависимости", "; ".join(item.dependencies) or "Не обнаружены"),
                        ("Описание модели", item.description or "NOT_AVAILABLE"),
                        ("Статус", item.status),
                    ],
                    (2_300, 7_060),
                ),
                HeadingBlock(3, "Определение DAX"),
            ]
        )
        if item.dax.text:
            blocks.append(CodeBlock(item.dax.text, item.dax.status))
        else:
            blocks.append(ParagraphBlock(item.dax.reason or "DAX: NOT_AVAILABLE", "warning"))
        blocks.append(HeadingBlock(3, "Где используется и какой контекст обнаружен"))
        if item.usages:
            blocks.append(
                TableBlock(
                    ("Лист", "Визуал", "Роль", "Агрегация", "Контекст"),
                    item.usages,
                    (1_600, 2_250, 1_150, 1_300, 3_060),
                )
            )
        else:
            blocks.append(ParagraphBlock("Использование в визуалах не найдено."))
    return blocks


def _page_guide(
    model: DocumentationModel,
    pages: list[PageInfo],
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = [HeadingBlock(1, "Путеводитель по листам и визуалам")]
    remaining = MAX_VISUALS
    for page in pages:
        blocks.extend(
            [
                HeadingBlock(2, f"{page_ids[page.id]} · {clean_text(page.name)}"),
                TableBlock(
                    ("Параметр", "Значение"),
                    [
                        ("Порядок", str(page.order + 1)),
                        ("Видимость", "VISIBLE" if page.visible else "HIDDEN"),
                        ("Тип", clean_text(page.page_type)),
                        ("Фильтры листа", _filters_text(page.filters)),
                        ("Визуалов", str(len(page.visuals))),
                    ],
                    (2_300, 7_060),
                ),
            ]
        )
        shown = page.visuals[: max(0, remaining)]
        remaining -= len(shown)
        if shown:
            blocks.append(
                TableBlock(
                    ("Визуал и тип", "Поля и метод", "Контекст", "Действия"),
                    [
                        (
                            f"{visual_ids[(page.id, visual.id)]} · {clean_text(visual.title)}\n"
                            f"{clean_text(visual.category)} / {clean_text(visual.technical_type)}",
                            _visual_method(visual, include_sources=True),
                            _context_text(model, page, visual, bookmark_ids),
                            _actions_text(model, visual, page_ids, bookmark_ids),
                        )
                        for visual in shown
                    ],
                    (2_150, 2_700, 2_850, 1_660),
                )
            )
        else:
            blocks.append(ParagraphBlock("Визуалы не найдены."))
        outgoing = [edge for edge in model.report.navigation if edge.source_page_id == page.id]
        incoming = [edge for edge in model.report.navigation if edge.target_page_id == page.id]
        blocks.append(
            ParagraphBlock(
                f"Переходы: исходящих — {len(outgoing)}, входящих — {len(incoming)}. "
                "Подробности приведены в разделе взаимодействий.",
                "note",
            )
        )
        if remaining <= 0:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
            break
    return blocks


def _interactions(
    model: DocumentationModel,
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    visual_by_raw: dict[str, list[str]],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    report = model.report
    page_by_id = {page.id: page for page in report.pages}
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Кнопки, закладки и переходы"),
        ParagraphBlock(
            "Формулировка «metadata указывает» означает статически найденную связь. "
            "Фактическое состояние после нажатия без выполнения отчёта не подтверждается.",
            "lead",
        ),
        HeadingBlock(2, "Закладки"),
    ]
    bookmarks = sorted(report.bookmarks, key=lambda item: item.order)
    if bookmarks:
        blocks.append(
            TableBlock(
                ("Закладка", "Группа / лист", "Scope", "Точки входа", "Затронутые визуалы"),
                [
                    (
                        f"{bookmark_ids[item.id]} · {clean_text(item.name)}",
                        f"{clean_text(item.group or '—')} / "
                        + (
                            f"{page_ids[item.associated_page_id]} · {clean_text(page_by_id[item.associated_page_id].name)}"
                            if item.associated_page_id in page_ids
                            else "<НЕ ОПРЕДЕЛЁН>"
                        ),
                        _scope_label(item.state_scope),
                        "; ".join(clean_text(value) for value in item.trigger_refs) or "<НЕ НАЙДЕНЫ>",
                        "; ".join(
                            local
                            for raw in item.affected_visual_ids
                            for local in visual_by_raw.get(raw, [f"<UNRESOLVED:{clean_text(raw)}>"])
                        )
                        or "<НЕ ОПРЕДЕЛЕНЫ>",
                    )
                    for item in bookmarks[:MAX_TABLE_ROWS]
                ],
                (1_850, 2_050, 1_300, 2_100, 2_060),
            )
        )
        blocks.append(ParagraphBlock("Сохранённые значения фильтров и срезов: <VALUES HIDDEN>.", "note"))
    else:
        blocks.append(ParagraphBlock("Закладки не найдены."))

    blocks.append(HeadingBlock(2, "Элементы управления и цели"))
    if report.navigation:
        blocks.append(
            TableBlock(
                ("Источник", "Что нажать", "Тип", "Цель", "Статус / эффект"),
                [
                    (
                        f"{page_ids.get(edge.source_page_id, '?')} · {clean_text(edge.source_page_name)}",
                        f"{visual_ids.get((edge.source_page_id, edge.source_visual_id), '?')} · "
                        f"{clean_text(edge.source_visual_title)}",
                        _action_label(edge.action_type),
                        _edge_target(edge, page_ids, bookmark_ids),
                        f"{clean_text(edge.status)}; metadata указывает переход: {_target_kind_label(edge.target_kind)}",
                    )
                    for edge in report.navigation[:MAX_TABLE_ROWS]
                ],
                (1_700, 2_200, 1_450, 2_200, 1_810),
            )
        )
    else:
        blocks.append(ParagraphBlock("Навигационные действия не найдены."))
    return blocks


def _journeys(
    model: DocumentationModel,
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Пользовательские маршруты"),
        ParagraphBlock("Каждая строка — прямой маршрут, обнаруженный в metadata отчёта.", "lead"),
    ]
    if not model.report.navigation:
        blocks.append(ParagraphBlock("Маршруты не найдены."))
        return blocks
    blocks.append(
        TableBlock(
            ("Откуда", "Через элемент", "Действие", "Куда", "Статус"),
            [
                (
                    f"{page_ids.get(edge.source_page_id, '?')} · {clean_text(edge.source_page_name)}",
                    f"{visual_ids.get((edge.source_page_id, edge.source_visual_id), '?')} · "
                    f"{clean_text(edge.source_visual_title)}",
                    _action_label(edge.action_type),
                    _edge_target(edge, page_ids, bookmark_ids),
                    clean_text(edge.status),
                )
                for edge in model.report.navigation[:MAX_TABLE_ROWS]
            ],
            (1_650, 2_250, 1_550, 2_250, 1_660),
        )
    )
    return blocks


def _semantic_model(model: DocumentationModel) -> list[DocumentBlock]:
    semantic = model.semantic_model
    blocks: list[DocumentBlock] = [
        HeadingBlock(1, "Семантическая модель и связи"),
        ParagraphBlock(f"Статус локальной модели: {clean_text(semantic.status)}", "status"),
    ]
    if not semantic.tables and not semantic.measures:
        blocks.append(ParagraphBlock("Локальная семантическая модель недоступна.", "warning"))
        return blocks
    if semantic.tables:
        blocks.extend(
            [
                HeadingBlock(2, "Таблицы"),
                TableBlock(
                    ("Таблица", "Скрыта", "Режим", "Колонок"),
                    [
                        (
                            clean_text(item.name),
                            yes_no(item.hidden),
                            clean_text(item.storage_mode or "—"),
                            str(len(item.columns)),
                        )
                        for item in semantic.tables[:MAX_TABLE_ROWS]
                    ],
                    (3_000, 1_400, 2_600, 2_360),
                ),
                HeadingBlock(2, "Колонки и безопасные примеры"),
            ]
        )
        columns = [column for table in semantic.tables for column in table.columns]
        blocks.append(
            TableBlock(
                ("Таблица / колонка", "Тип", "Скрыта", "Уникальных", "Примеры / статус"),
                [
                    (
                        f"{clean_text(item.table)}[{clean_text(item.name)}]",
                        clean_text(item.data_type or "—"),
                        yes_no(item.hidden),
                        str(item.cardinality) if item.cardinality is not None else "—",
                        "; ".join(clean_text(value) for value in item.examples) or f"<{clean_text(item.example_status)}>",
                    )
                    for item in columns[:MAX_TABLE_ROWS]
                ],
                (2_600, 1_350, 1_100, 1_350, 2_960),
            )
        )
        if len(columns) > MAX_TABLE_ROWS:
            blocks.append(ParagraphBlock(OVERFLOW, "warning"))
    if semantic.relationships:
        blocks.extend(
            [
                HeadingBlock(2, "Связи"),
                TableBlock(
                    ("Из", "В", "Кардинальность", "Фильтрация", "Статус"),
                    [
                        (
                            clean_text(item.from_object),
                            clean_text(item.to_object),
                            clean_text(item.cardinality or "—"),
                            clean_text(item.filter_direction or "—"),
                            f"Активна: {yes_no(item.active)}; security: {clean_text(item.security_filter or '—')}",
                        )
                        for item in semantic.relationships[:MAX_TABLE_ROWS]
                    ],
                    (2_250, 2_250, 1_500, 1_550, 1_810),
                ),
            ]
        )
    return blocks


def _security_and_sources(model: DocumentationModel) -> list[DocumentBlock]:
    semantic = model.semantic_model
    blocks: list[DocumentBlock] = [HeadingBlock(1, "Power Query, источники и безопасность")]
    blocks.append(HeadingBlock(2, "Power Query и подключения"))
    if semantic.queries:
        blocks.append(
            TableBlock(
                ("Запрос", "Тип", "Выражение"),
                [
                    (
                        clean_text(item.name),
                        clean_text(item.kind),
                        "DEFINED; CONTENT OMITTED IN SAFE MODE" if item.expression_available else "NOT_AVAILABLE",
                    )
                    for item in semantic.queries[:MAX_TABLE_ROWS]
                ],
                (3_100, 2_200, 4_060),
            )
        )
    else:
        blocks.append(ParagraphBlock("Power Query metadata: NOT_AVAILABLE."))
    blocks.append(
        ParagraphBlock(
            "Типы подключений: "
            + (", ".join(clean_text(value) for value in semantic.connection_types) or "не определены"),
            "note",
        )
    )
    blocks.extend(
        [
            HeadingBlock(2, "RLS и OLS — формальная заглушка"),
            ParagraphBlock(
                "Статус анализа: PROVISIONAL. Логины, UPN, пользователи и назначения ролей не выводятся.",
                "warning",
            ),
        ]
    )
    if semantic.rls:
        rows: list[tuple[str, ...]] = []
        for rule in semantic.rls[:MAX_TABLE_ROWS]:
            dax = sanitize_dax(rule.expression)
            rows.append(
                (
                    clean_text(rule.role),
                    clean_text(rule.table or "—"),
                    clean_text(dax.text or dax.reason or "PARTIAL"),
                    clean_text(rule.permission or "—"),
                )
            )
        blocks.append(TableBlock(("Роль", "Таблица", "DAX-фильтр", "Разрешение"), rows, (2_100, 1_900, 3_760, 1_600)))
    else:
        blocks.append(ParagraphBlock("RLS metadata: NOT_FOUND_OR_NOT_AVAILABLE."))
    if semantic.ols:
        blocks.append(
            TableBlock(
                ("Роль", "Таблица", "Колонка", "Разрешение"),
                [
                    (
                        clean_text(rule.role),
                        clean_text(rule.table or "—"),
                        clean_text(rule.column or "—"),
                        clean_text(rule.permission or "—"),
                    )
                    for rule in semantic.ols[:MAX_TABLE_ROWS]
                ],
                (2_400, 2_300, 2_660, 2_000),
            )
        )
    else:
        blocks.append(ParagraphBlock("OLS metadata: NOT_FOUND_OR_NOT_AVAILABLE."))
    return blocks


def _diagnostics(
    model: DocumentationModel,
    page_ids: dict[str, str],
    visual_ids: dict[tuple[str, str], str],
    bookmark_ids: dict[str, str],
) -> list[DocumentBlock]:
    unresolved = [
        (page, visual, item)
        for page in model.report.pages
        for visual in page.visuals
        for item in visual.fields
        if item.status != "RESOLVED"
    ]
    blocks: list[DocumentBlock] = [HeadingBlock(1, "Неполные сведения и диагностика")]
    blocks.append(HeadingBlock(2, "Неразрешённые ссылки"))
    if unresolved:
        blocks.append(
            TableBlock(
                ("Объект", "Расположение", "Причина", "Путь-источник"),
                [
                    (
                        clean_text(item.qualified_name),
                        f"{page_ids.get(page.id, '?')} / {visual_ids.get((page.id, visual.id), '?')}",
                        clean_text(item.reason or "Не указана"),
                        clean_text(item.source_path),
                    )
                    for page, visual, item in unresolved[:MAX_TABLE_ROWS]
                ],
                (2_200, 1_400, 2_700, 3_060),
            )
        )
    else:
        blocks.append(ParagraphBlock("Неразрешённых ссылок не найдено."))
    blocks.append(HeadingBlock(2, "Диагностика извлечения"))
    if model.issues:
        blocks.append(
            TableBlock(
                ("Уровень", "Компонент", "Код", "Сообщение", "Источник"),
                [
                    (
                        clean_text(item.severity),
                        clean_text(item.component),
                        clean_text(item.code),
                        clean_text(item.message),
                        clean_text(item.source_path or "—"),
                    )
                    for item in model.issues[:MAX_TABLE_ROWS]
                ],
                (1_050, 1_300, 1_800, 3_150, 2_060),
            )
        )
    else:
        blocks.append(ParagraphBlock("Предупреждения и ошибки отсутствуют."))
    blocks.extend(
        [
            HeadingBlock(2, "Технические идентификаторы документа"),
            ParagraphBlock(
                "P — лист, V — визуал, K — KPI/вычисление, B — закладка. "
                "Эти идентификаторы локальны для данной выгрузки и стабильны при одинаковом порядке metadata.",
                "note",
            ),
            ParagraphBlock(f"ИТОГОВЫЙ СТАТУС: {overall_status(model)}", "status"),
        ]
    )
    return blocks


def sanitize_dax(expression: str | None) -> DaxProjection:
    if not expression:
        return DaxProjection(None, "NOT_AVAILABLE", [], [], "DAX: NOT_AVAILABLE")
    text = clean_text(expression, preserve_newlines=True)
    output: list[str] = []
    index = 0
    malformed = False
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
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
    sanitized = "".join(output).strip()
    references = _dax_references(sanitized)
    functions = sorted(
        {match.group(1).upper() for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", sanitized)}
    )
    status = "OK"
    if len(sanitized) > MAX_DAX_CHARS:
        sanitized = sanitized[:MAX_DAX_CHARS].rstrip() + f"\n{OVERFLOW}"
        status = "PARTIAL"
    return DaxProjection(sanitized, status, references[:MAX_DEPENDENCIES], functions)


def _dax_references(expression: str) -> list[str]:
    qualified_pattern = re.compile(r"(?:(?:'((?:[^']|'')+)')|([A-Za-z_][\w .-]*))\s*\[([^\]]+)\]")
    qualified: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in qualified_pattern.finditer(expression):
        table = (match.group(1) or match.group(2) or "").replace("''", "'").strip()
        qualified.append(f"{table}[{match.group(3).strip()}]")
        spans.append(match.span())
    unqualified = []
    for match in re.finditer(r"\[([^\]]+)\]", expression):
        if not any(start <= match.start() < end for start, end in spans):
            unqualified.append(f"[{match.group(1).strip()}]")
    return list(dict.fromkeys(qualified + unqualified))


def _dependency_paths(
    root: tuple[str, ...],
    dax_by_key: dict[tuple[str, ...], DaxProjection],
    candidates: dict[tuple[str, ...], dict[str, object]],
    local_ids: dict[tuple[str, ...], str],
    measure_by_name: dict[str, list[tuple[str, ...]]],
) -> list[str]:
    paths: list[str] = []

    def resolve(reference: str) -> tuple[str, ...] | None:
        if reference.startswith("["):
            matches = measure_by_name.get(reference[1:-1].casefold(), [])
            return matches[0] if len(matches) == 1 else None
        match = re.fullmatch(r"(.+)\[([^\]]+)\]", reference)
        if not match:
            return None
        key = ("MEASURE", match.group(1).casefold(), match.group(2).casefold())
        return key if key in candidates else None

    def walk(key: tuple[str, ...], prefix: list[str], seen: set[tuple[str, ...]], depth: int) -> None:
        if depth >= 8 or len(paths) >= MAX_DEPENDENCIES:
            return
        for reference in dax_by_key.get(key, DaxProjection(None, "NOT_AVAILABLE", [], [])).references:
            target = resolve(reference)
            label = f"{local_ids[target]} {reference}" if target else reference
            path = prefix + [label]
            paths.append(" → ".join(path))
            if target and target not in seen:
                walk(target, path, seen | {target}, depth + 1)

    walk(root, [], {root}, 0)
    if len(paths) >= MAX_DEPENDENCIES:
        paths.append(OVERFLOW)
    return paths


def _context_items(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
    bookmark_ids: dict[str, str],
) -> list[str]:
    items = [f"REPORT: {item.field} ({item.filter_type})" for item in model.report.report_filters]
    items.extend(f"PAGE: {item.field} ({item.filter_type})" for item in page.filters)
    items.extend(f"VISUAL: {item.field} ({item.filter_type})" for item in visual.filters)
    for bookmark in model.report.bookmarks:
        if bookmark.associated_page_id == page.id and visual.id in bookmark.affected_visual_ids:
            items.append(f"{bookmark_ids.get(bookmark.id, '?')}: {_scope_label(bookmark.state_scope)}")
    return [clean_text(item) for item in items]


def _context_text(
    model: DocumentationModel,
    page: PageInfo,
    visual: VisualInfo,
    bookmark_ids: dict[str, str],
) -> str:
    items = _context_items(model, page, visual, bookmark_ids)
    return "; ".join(items) + "; значения: <VALUES HIDDEN>" if items else "Явные фильтры/закладки не обнаружены"


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
        )
    )


def _visual_sources(visual: VisualInfo) -> str:
    return "; ".join(dict.fromkeys(clean_text(item.qualified_name) for item in visual.fields)) or "—"


def _visual_method(visual: VisualInfo, *, include_sources: bool = False) -> str:
    values = []
    for item in visual.fields:
        method = _method_label(item.aggregation, item.object_type, item.usage)
        value = f"{item.qualified_name}: {method}" if include_sources else method
        values.append(clean_text(value))
    return "; ".join(dict.fromkeys(values)) or "—"


def _actions_text(
    model: DocumentationModel,
    visual: VisualInfo,
    page_ids: dict[str, str],
    bookmark_ids: dict[str, str],
) -> str:
    edges = [edge for edge in model.report.navigation if edge.source_visual_id == visual.id]
    return "; ".join(
        f"{_action_label(edge.action_type)} → {_edge_target(edge, page_ids, bookmark_ids)}" for edge in edges
    ) or "—"


def _edge_target(edge: object, page_ids: dict[str, str], bookmark_ids: dict[str, str]) -> str:
    target_kind = getattr(edge, "target_kind")
    target_id = getattr(edge, "target_id")
    target_name = clean_text(getattr(edge, "target_name") or "—")
    if target_kind == "PAGE" and getattr(edge, "target_page_id") in page_ids:
        return f"{page_ids[getattr(edge, 'target_page_id')]} · {target_name}"
    if target_kind == "BOOKMARK" and target_id in bookmark_ids:
        page = getattr(edge, "target_page_id")
        suffix = f" → {page_ids[page]}" if page in page_ids else ""
        return f"{bookmark_ids[target_id]} · {target_name}{suffix}"
    return f"{clean_text(target_kind)} · {target_name}"


def _filter_table(filters: Iterable[FilterInfo]) -> TableBlock:
    return TableBlock(
        ("Область", "Поле", "Тип", "Значения"),
        [
            (
                clean_text(item.scope),
                clean_text(item.field),
                clean_text(item.filter_type),
                f"<VALUES HIDDEN>; сохранено: {item.saved_value_count}",
            )
            for item in list(filters)[:MAX_TABLE_ROWS]
        ],
        (1_500, 2_700, 2_000, 3_160),
    )


def _filters_text(filters: Iterable[FilterInfo]) -> str:
    values = [f"{item.scope}: {item.field} ({item.filter_type}); <VALUES HIDDEN>" for item in filters]
    return "; ".join(clean_text(value) for value in values) or "Не найдены"


def _issue_summary(model: DocumentationModel) -> str:
    errors = sum(item.severity == "ERROR" for item in model.issues)
    warnings = sum(item.severity == "WARNING" for item in model.issues)
    return f"Ошибок: {errors}; предупреждений: {warnings}"


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


def _target_kind_label(value: str) -> str:
    return {"PAGE": "лист", "BOOKMARK": "закладка", "EXTERNAL": "внешняя цель"}.get(
        value, clean_text(value).lower()
    )


def _scope_label(value: str | None) -> str:
    if not value:
        return "scope неизвестен"
    labels = {
        "CAPTURED_STATE": "сохранённое состояние",
        "DATA": "данные",
        "DISPLAY": "отображение",
        "CURRENT_PAGE": "текущий лист",
    }
    return labels.get(value, clean_text(value).replace("_", " ").lower())


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
