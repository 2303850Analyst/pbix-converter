from __future__ import annotations

import codecs
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .domain import DocumentationModel


LINE = "=" * 110
SUBLINE = "-" * 110


def render_document(model: DocumentationModel) -> str:
    report = model.report
    semantic = model.semantic_model
    visuals = [visual for page in report.pages for visual in page.visuals]
    fields = [field for visual in visuals for field in visual.fields]
    unresolved = [field for field in fields if field.status != "RESOLVED"]
    overall = overall_status(model)
    page_by_id = {page.id: page for page in report.pages}

    lines = [LINE, "ДОКУМЕНТАЦИЯ POWER BI", LINE]
    add_section(lines, "1", "Сведения о файле")
    lines.extend(
        [
            f"Имя: {model.file.path.name}",
            f"Путь: {model.file.path}",
            f"Размер: {model.file.size_bytes} bytes",
            f"SHA-256: {model.file.sha256}",
            f"Изменён: {model.file.modified_at.isoformat()}",
            f"Проанализирован: {model.generated_at.isoformat()}",
            f"Версия генератора: {model.application_version}",
            f"Версия PBIXRay: {semantic.library_version or 'NOT_AVAILABLE'}",
            "Профиль значений: SAFE_TOP_5_CATEGORICAL",
        ]
    )

    add_section(lines, "2", "Статус извлечения")
    lines.extend(
        table(
            ["Компонент", "Статус", "Подробности"],
            [
                ["Общий результат", overall, issue_summary(model)],
                ["Формат отчёта", report.report_format, report.format_version or "Версия не определена"],
                ["Листы", "OK" if report.pages else "FAILED", len(report.pages)],
                ["Визуалы", "OK", len(visuals)],
                ["Закладки", "OK", len(report.bookmarks)],
                ["Навигация", "OK", len(report.navigation)],
                ["Ссылки на поля", "PARTIAL" if unresolved else "OK", f"Разрешено {len(fields) - len(unresolved)} из {len(fields)}"],
                ["Семантическая модель", semantic.status, f"Таблиц: {len(semantic.tables)}, мер: {len(semantic.measures)}"],
                ["RLS/OLS", "PROVISIONAL", "Формальная заглушка до боевого прогона"],
            ],
        )
    )

    add_section(lines, "3", "Общие сведения об отчёте")
    lines.extend(
        [
            f"Название: {report.title}",
            f"Листов: {len(report.pages)}",
            f"Визуалов: {len(visuals)}",
            f"Закладок: {len(report.bookmarks)}",
            f"Навигационных связей: {len(report.navigation)}",
            f"Фильтров уровня отчёта: {len(report.report_filters)}",
        ]
    )
    if report.report_filters:
        add_section(lines, "3.1", "Фильтры уровня отчёта")
        lines.extend(render_filters(report.report_filters))

    add_section(lines, "4", "Список листов")
    lines.extend(
        table(
            ["№", "Лист", "ID", "Видимость", "Тип", "Размер", "Визуалов"],
            [
                [
                    page.order + 1,
                    page.name,
                    page.id,
                    "VISIBLE" if page.visible else "HIDDEN",
                    page.page_type,
                    f"{fmt_number(page.width)}x{fmt_number(page.height)}",
                    len(page.visuals),
                ]
                for page in report.pages
            ],
        )
    )

    add_section(lines, "4.1", "Закладки и связанные листы")
    if report.bookmarks:
        lines.extend(
            table(
                ["№", "Закладка", "ID", "Группа", "Связанный лист", "Состояние", "Визуалов", "Точки входа"],
                [
                    [
                        bookmark.order + 1,
                        bookmark.name,
                        bookmark.id,
                        bookmark.group or "—",
                        page_by_id.get(bookmark.associated_page_id).name
                        if bookmark.associated_page_id in page_by_id
                        else bookmark.associated_page_id or "<НЕ ОПРЕДЕЛЁН>",
                        bookmark.state_scope or "UNKNOWN",
                        len(bookmark.affected_visual_ids),
                        "; ".join(bookmark.trigger_refs) or "<НЕ НАЙДЕНЫ>",
                    ]
                    for bookmark in report.bookmarks
                ],
            )
        )
        lines.append("Сохранённые значения фильтров и срезов закладок: <VALUES HIDDEN>.")
    else:
        lines.append("Закладки не найдены.")

    add_section(lines, "4.2", "Навигационный граф: откуда и куда можно перейти")
    if report.navigation:
        lines.extend(
            table(
                ["Откуда", "Элемент", "Тип", "Цель", "Связанный лист", "Статус"],
                [
                    [
                        edge.source_page_name,
                        edge.source_visual_title,
                        edge.action_type,
                        edge.target_name or "—",
                        edge.target_page_name or "—",
                        edge.status,
                    ]
                    for edge in report.navigation
                ],
            )
        )
    else:
        lines.append("Навигационные связи не найдены.")

    section_number = 5
    for page in report.pages:
        add_section(lines, str(section_number), f"Лист: {page.name}")
        lines.extend(
            [
                f"ID: {page.id}",
                f"Порядок: {page.order + 1}",
                f"Видимость: {'VISIBLE' if page.visible else 'HIDDEN'}",
                f"Тип: {page.page_type}",
                f"Холст: {fmt_number(page.width)} x {fmt_number(page.height)}",
            ]
        )
        add_section(lines, f"{section_number}.1", "Визуальные элементы")
        lines.extend(
            table(
                ["№", "Название", "Категория", "Power BI type", "X", "Y", "W", "H", "Z"],
                [
                    [
                        index,
                        visual.title,
                        visual.category,
                        visual.technical_type,
                        fmt_number(visual.x),
                        fmt_number(visual.y),
                        fmt_number(visual.width),
                        fmt_number(visual.height),
                        fmt_number(visual.z),
                    ]
                    for index, visual in enumerate(page.visuals, 1)
                ],
            )
            if page.visuals
            else ["Визуалы не найдены."]
        )

        add_section(lines, f"{section_number}.2", "Поля визуалов")
        page_fields = [(visual, field) for visual in page.visuals for field in visual.fields]
        if page_fields:
            lines.extend(
                table(
                    ["Визуал", "Объект", "Тип", "Роль", "Использование", "Агрегация", "Статус"],
                    [
                        [
                            visual.title,
                            field.qualified_name,
                            field.object_type,
                            field.role or "—",
                            field.usage,
                            field.aggregation or "—",
                            field.status,
                        ]
                        for visual, field in page_fields
                    ],
                )
            )
        else:
            lines.append("Поля визуалов не найдены.")

        add_section(lines, f"{section_number}.3", "Фильтры")
        filters = list(page.filters)
        for visual in page.visuals:
            filters.extend(visual.filters)
        lines.extend(render_filters(filters) if filters else ["Фильтры не найдены."])

        add_section(lines, f"{section_number}.4", "Связанные закладки")
        associated = [bookmark for bookmark in report.bookmarks if bookmark.associated_page_id == page.id]
        if associated:
            lines.extend(
                table(
                    ["Закладка", "Группа", "Точки входа", "Затронутые визуалы"],
                    [
                        [
                            bookmark.name,
                            bookmark.group or "—",
                            "; ".join(bookmark.trigger_refs) or "<НЕ НАЙДЕНЫ>",
                            "; ".join(bookmark.affected_visual_ids) or "<НЕ ОПРЕДЕЛЕНЫ>",
                        ]
                        for bookmark in associated
                    ],
                )
            )
        else:
            lines.append("Закладки, связанные с листом, не найдены.")

        outgoing = [edge for edge in report.navigation if edge.source_page_id == page.id]
        incoming = [edge for edge in report.navigation if edge.target_page_id == page.id]
        add_section(lines, f"{section_number}.5", "Исходящие переходы")
        lines.extend(render_navigation(outgoing, incoming=False) if outgoing else ["Исходящие переходы не найдены."])
        add_section(lines, f"{section_number}.6", "Входящие переходы")
        lines.extend(render_navigation(incoming, incoming=True) if incoming else ["Входящие переходы не найдены."])
        section_number += 1

    add_section(lines, str(section_number), "Семантическая модель")
    lines.extend(render_semantic_model(model))
    section_number += 1

    add_section(lines, str(section_number), "Power Query и источники")
    if semantic.queries:
        lines.extend(
            table(
                ["Запрос", "Тип", "Выражение"],
                [[query.name, query.kind, "DEFINED; CONTENT OMITTED IN SAFE MODE" if query.expression_available else "NOT_AVAILABLE"] for query in semantic.queries],
            )
        )
    else:
        lines.append("Power Query metadata: NOT_AVAILABLE.")
    lines.append(
        "Типы подключений: " + (", ".join(semantic.connection_types) if semantic.connection_types else "не определены")
    )
    section_number += 1

    add_section(lines, str(section_number), "RLS и OLS — формальная заглушка")
    lines.append("Статус анализа: PROVISIONAL до первого прогона на боевом PBIX.")
    lines.append(f"RLS metadata: {'FOUND' if semantic.rls else 'NOT_FOUND_OR_NOT_AVAILABLE'}")
    if semantic.rls:
        lines.extend(
            table(
                ["Роль", "Таблица", "Выражение", "Разрешение"],
                [
                    [rule.role, rule.table or "—", sanitize_dax_literals(rule.expression), rule.permission or "—"]
                    for rule in semantic.rls
                ],
            )
        )
    lines.append(f"OLS metadata: {'FOUND' if semantic.ols else 'NOT_FOUND_OR_NOT_AVAILABLE'}")
    if semantic.ols:
        lines.extend(
            table(
                ["Роль", "Таблица", "Колонка", "Разрешение"],
                [[rule.role, rule.table or "—", rule.column or "—", rule.permission or "—"] for rule in semantic.ols],
            )
        )
    lines.append("Логины, UPN, пользователи и назначения ролей не выводятся.")
    section_number += 1

    add_section(lines, str(section_number), "Неразрешённые ссылки")
    if unresolved:
        lines.extend(
            table(
                ["Объект", "Роль", "Причина", "Путь-источник"],
                [[field.qualified_name, field.role or "—", field.reason or "Не указана", field.source_path] for field in unresolved],
            )
        )
    else:
        lines.append("Неразрешённых ссылок не найдено.")
    section_number += 1

    add_section(lines, str(section_number), "Диагностика извлечения")
    if model.issues:
        lines.extend(
            table(
                ["Уровень", "Компонент", "Код", "Сообщение", "Источник"],
                [[issue.severity, issue.component, issue.code, issue.message, issue.source_path or "—"] for issue in model.issues],
            )
        )
    else:
        lines.append("Предупреждения и ошибки отсутствуют.")
    lines.extend(["", f"ИТОГОВЫЙ СТАТУС: {overall}", LINE])
    return "\r\n".join(lines) + "\r\n"


def render_semantic_model(model: DocumentationModel) -> list[str]:
    semantic = model.semantic_model
    lines = [f"Статус: {semantic.status}"]
    if not semantic.tables and not semantic.measures:
        lines.append("Локальная семантическая модель недоступна.")
        return lines
    if semantic.tables:
        lines.extend(
            table(
                ["Таблица", "Скрыта", "Режим", "Колонок"],
                [[item.name, yes_no(item.hidden), item.storage_mode or "—", len(item.columns)] for item in semantic.tables],
            )
        )
        columns = [column for table_info in semantic.tables for column in table_info.columns]
        lines.extend(["", "Колонки и безопасные примеры:"])
        lines.extend(
            table(
                ["Таблица", "Колонка", "Тип", "Скрыта", "Уникальных", "Примеры / статус"],
                [
                    [
                        column.table,
                        column.name,
                        column.data_type or "—",
                        yes_no(column.hidden),
                        column.cardinality if column.cardinality is not None else "—",
                        "; ".join(column.examples) if column.examples else f"<{column.example_status}>",
                    ]
                    for column in columns
                ],
            )
        )
    if semantic.measures:
        lines.extend(["", "Меры и DAX:"])
        for measure in semantic.measures:
            lines.extend(
                [
                    f"Мера: {measure.table}[{measure.name}]",
                    f"Формат: {measure.format_string or '—'}",
                    f"Папка: {measure.display_folder or '—'}",
                    f"Описание: {measure.description or '—'}",
                    "DAX:",
                    measure.expression,
                    "",
                ]
            )
    if semantic.relationships:
        lines.extend(["Связи:"])
        lines.extend(
            table(
                ["Из", "В", "Кардинальность", "Активна", "Фильтрация", "Security filter"],
                [
                    [
                        rel.from_object,
                        rel.to_object,
                        rel.cardinality or "—",
                        yes_no(rel.active),
                        rel.filter_direction or "—",
                        rel.security_filter or "—",
                    ]
                    for rel in semantic.relationships
                ],
            )
        )
    return lines


def render_filters(filters: Iterable[Any]) -> list[str]:
    return table(
        ["Область", "Поле", "Тип", "Сохранено значений", "Значения"],
        [[item.scope, item.field, item.filter_type, item.saved_value_count, "<VALUES HIDDEN>"] for item in filters],
    )


def render_navigation(edges: Iterable[Any], incoming: bool) -> list[str]:
    if incoming:
        return table(
            ["Исходный лист", "Элемент", "Тип", "Цель", "Статус"],
            [[edge.source_page_name, edge.source_visual_title, edge.action_type, edge.target_name or "—", edge.status] for edge in edges],
        )
    return table(
        ["Элемент", "Тип", "Цель", "Связанный лист", "Статус"],
        [[edge.source_visual_title, edge.action_type, edge.target_name or "—", edge.target_page_name or "—", edge.status] for edge in edges],
    )


def overall_status(model: DocumentationModel) -> str:
    if not model.report.pages:
        return "FAILED"
    if any(issue.severity in {"ERROR", "WARNING"} for issue in model.issues):
        return "PARTIAL"
    return "OK"


def issue_summary(model: DocumentationModel) -> str:
    errors = sum(issue.severity == "ERROR" for issue in model.issues)
    warnings = sum(issue.severity == "WARNING" for issue in model.issues)
    return f"Ошибок: {errors}, предупреждений: {warnings}"


def sanitize_dax_literals(expression: str | None) -> str:
    if not expression:
        return "—"
    return re.sub(r'"(?:[^"]|"")*"', '"<VALUE>"', expression)


def yes_no(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Да" if value else "Нет"


def fmt_number(value: float | None) -> str:
    if value is None:
        return "—"
    return str(int(value)) if value.is_integer() else f"{value:g}"


def add_section(lines: list[str], number: str, title: str) -> None:
    lines.extend(["", f"{number}. {title}", SUBLINE])


def display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def clipped(value: Any, limit: int = 72) -> str:
    text = " ".join(display(value).replace("\t", " ").splitlines()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    rendered = [[clipped(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in rendered:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def row_line(row: list[str]) -> str:
        return "|" + "|".join(f" {cell:<{width}} " for cell, width in zip(row, widths)) + "|"

    result = [border, row_line(headers), border]
    result.extend(row_line(row) for row in rendered)
    result.append(border)
    return result


def atomic_write(output_path: Path, document: str) -> None:
    output_path = output_path.resolve()
    if output_path.suffix.casefold() != ".txt":
        raise ValueError("Выходной файл должен иметь расширение .txt")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(output_path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output_path.stem}_",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(codecs.BOM_UTF8)
            stream.write(document.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
