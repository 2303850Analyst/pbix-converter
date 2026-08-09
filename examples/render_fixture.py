from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
INPUT_PATH = HERE / "representative_report_fixture.json"
OUTPUT_PATH = HERE / "representative_report_documentation.txt"
LINE = "=" * 100
SUBLINE = "-" * 100


def display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def clipped(value: Any, limit: int = 52) -> str:
    text = display(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
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


def section(lines: list[str], number: str, title: str) -> None:
    lines.extend(["", f"{number}. {title}", SUBLINE])


def render(data: dict[str, Any]) -> str:
    report = data["report"]
    model = data["semantic_model"]
    pages = report["pages"]
    bookmarks = report.get("bookmarks", [])
    page_by_id = {page["id"]: page for page in pages}
    bookmark_by_id = {bookmark["id"]: bookmark for bookmark in bookmarks}
    visuals = [visual for page in pages for visual in page["visuals"]]
    fields = [field for visual in visuals for field in visual["fields"]]
    unresolved = [field for field in fields if field["status"] != "RESOLVED"]
    visible_pages = sum(page["visibility"] == "VISIBLE" for page in pages)

    navigation_edges: list[dict[str, str]] = []
    bookmark_sources: dict[str, list[str]] = {bookmark["id"]: [] for bookmark in bookmarks}
    for source_page in pages:
        for visual in source_page["visuals"]:
            for action in visual["actions"]:
                target_id = action["target"]
                target_type = action["type"]
                related_page_id = ""
                if target_id in page_by_id:
                    target_object = f"Страница: {page_by_id[target_id]['name']}"
                    related_page_id = target_id
                elif target_id in bookmark_by_id:
                    bookmark = bookmark_by_id[target_id]
                    target_object = f"Закладка: {bookmark['name']}"
                    related_page_id = bookmark["target_page"]
                    bookmark_sources[target_id].append(f"{source_page['name']} / {visual['title']}")
                else:
                    target_object = target_id

                related_page = page_by_id.get(related_page_id, {}).get("name", "<ВНЕШНЯЯ ИЛИ НЕРАЗРЕШЁННАЯ ЦЕЛЬ>")
                navigation_edges.append(
                    {
                        "source_page_id": source_page["id"],
                        "source_page": source_page["name"],
                        "visual": visual["title"],
                        "type": target_type,
                        "target": target_object,
                        "related_page_id": related_page_id,
                        "related_page": related_page,
                    }
                )

    lines = [
        LINE,
        "ДОКУМЕНТАЦИЯ POWER BI — РЕПРЕЗЕНТАТИВНЫЙ ПРИМЕР",
        LINE,
        "ВНИМАНИЕ: документ создан из синтетического нормализованного fixture, а не из реального PBIX.",
        "Он фиксирует ожидаемую структуру будущего выходного TXT.",
    ]

    section(lines, "1", "Сведения о файле и генерации")
    lines.extend(
        [
            f"Имя входного файла: {data['file']['name']}",
            f"Тип источника: {data['source_kind']}",
            "Размер файла: <НЕ ПРИМЕНИМО ДЛЯ СИНТЕТИЧЕСКОГО FIXTURE>",
            "SHA-256: <НЕ ПРИМЕНИМО ДЛЯ СИНТЕТИЧЕСКОГО FIXTURE>",
            f"Профиль значений: {data['extraction']['value_profile']}",
            "Максимум примеров: 5 категориальных значений",
        ]
    )

    section(lines, "2", "Статус извлечения")
    lines.extend(
        table(
            ["Компонент", "Статус", "Подробности"],
            [
                ["Общий результат", data["extraction"]["overall_status"], "Есть одна неразрешённая ссылка"],
                ["Контейнер", data["extraction"]["container_status"], "Реальный PBIX не использовался"],
                ["Структура отчёта", data["extraction"]["report_status"], f"Страниц: {len(pages)}, визуалов: {len(visuals)}"],
                ["Формат отчёта", "OK", f"{data['extraction']['report_format']} {data['extraction']['report_format_version']}"],
                ["Семантическая модель", data["extraction"]["model_status"], f"Таблиц: {len(model['tables'])}, мер: {len(model['measures'])}"],
                ["Ссылки на поля", "PARTIAL" if unresolved else "OK", f"Разрешено: {len(fields) - len(unresolved)} из {len(fields)}"],
            ],
        )
    )

    section(lines, "3", "Общие сведения об отчёте")
    lines.extend(
        [
            f"Название: {report['title']}",
            f"Страниц: {len(pages)} (видимых: {visible_pages}, скрытых: {len(pages) - visible_pages})",
            f"Визуальных элементов: {len(visuals)}",
            f"Закладок: {len(bookmarks)}",
            f"Навигационных связей: {len(navigation_edges)}",
            f"Фильтров уровня отчёта: {len(report['report_filters'])}",
        ]
    )

    section(lines, "3.1", "Фильтры уровня отчёта")
    lines.extend(
        table(
            ["Объект", "Тип", "Сохранено значений", "Значения"],
            [
                [flt["object"], flt["filter_type"], flt["saved_value_count"], flt["values"]]
                for flt in report["report_filters"]
            ],
        )
    )

    section(lines, "4", "Список страниц")
    lines.extend(
        table(
            ["№", "Страница", "Видимость", "Тип", "Размер", "Визуалов"],
            [
                [page["order"], page["name"], page["visibility"], page["page_type"], f"{page['width']}x{page['height']}", len(page["visuals"])]
                for page in pages
            ],
        )
    )

    section(lines, "4.1", "Закладки и связанные страницы")
    if bookmarks:
        lines.extend(
            table(
                ["№", "Закладка", "Группа", "Связанная страница", "Состояние", "Затронуто", "Точки входа"],
                [
                    [
                        bookmark["order"],
                        bookmark["name"],
                        bookmark["group"],
                        page_by_id.get(bookmark["target_page"], {}).get("name", bookmark["target_page"]),
                        bookmark["state_scope"],
                        len(bookmark["affected_visuals"]),
                        "; ".join(bookmark_sources[bookmark["id"]]) or "<НЕ НАЙДЕНЫ В МЕТАДАННЫХ>",
                    ]
                    for bookmark in bookmarks
                ],
            )
        )
        lines.append("Сохранённые значения фильтров и срезов внутри закладок: <VALUES HIDDEN>.")
    else:
        lines.append("Закладки не найдены.")

    section(lines, "4.2", "Навигационный граф: откуда и куда можно перейти")
    if navigation_edges:
        lines.extend(
            table(
                ["Откуда: страница", "Элемент", "Тип перехода", "Цель", "Связанная страница"],
                [
                    [edge["source_page"], edge["visual"], edge["type"], edge["target"], edge["related_page"]]
                    for edge in navigation_edges
                ],
            )
        )
    else:
        lines.append("Навигационные связи не найдены.")

    page_section_number = 5
    for page in pages:
        section(lines, str(page_section_number), f"Страница: {page['name']}")
        lines.extend(
            [
                f"Технический ID: {page['id']}",
                f"Порядок: {page['order']}",
                f"Видимость: {page['visibility']}",
                f"Тип страницы: {page['page_type']}",
                f"Холст: {page['width']} x {page['height']}",
            ]
        )

        section(lines, f"{page_section_number}.1", "Визуальные элементы")
        lines.extend(
            table(
                ["№", "Название", "Категория", "Power BI type", "X", "Y", "W", "H", "Z"],
                [
                    [
                        index,
                        visual["title"],
                        visual["category"],
                        visual["technical_type"],
                        visual["layout"]["x"],
                        visual["layout"]["y"],
                        visual["layout"]["width"],
                        visual["layout"]["height"],
                        visual["layout"]["z"],
                    ]
                    for index, visual in enumerate(page["visuals"], start=1)
                ],
            )
        )

        page_fields = [
            (visual["title"], field)
            for visual in page["visuals"]
            for field in visual["fields"]
        ]
        section(lines, f"{page_section_number}.2", "Поля визуалов")
        if page_fields:
            lines.extend(
                table(
                    ["Визуал", "Объект", "Тип", "Роль", "Использование", "Агрегация", "Статус"],
                    [
                        [title, field["object"], field["object_type"], field["role"], field["usage"], field["aggregation"], field["status"]]
                        for title, field in page_fields
                    ],
                )
            )
        else:
            lines.append("Поля отсутствуют.")

        page_filters = list(page["filters"])
        for visual in page["visuals"]:
            page_filters.extend(visual["filters"])
        section(lines, f"{page_section_number}.3", "Фильтры")
        if page_filters:
            lines.extend(
                table(
                    ["Область", "Объект", "Тип", "Сохранено", "Значения"],
                    [[flt["scope"], flt["object"], flt["filter_type"], flt["saved_value_count"], flt["values"]] for flt in page_filters],
                )
            )
        else:
            lines.append("Фильтры страницы и визуалов отсутствуют.")

        actions = [
            (visual["title"], action)
            for visual in page["visuals"]
            for action in visual["actions"]
        ]
        section(lines, f"{page_section_number}.4", "Навигация и действия")
        if actions:
            page_edges = [edge for edge in navigation_edges if edge["source_page_id"] == page["id"]]
            lines.extend(
                table(
                    ["Элемент", "Действие", "Цель", "Связанная страница"],
                    [[edge["visual"], edge["type"], edge["target"], edge["related_page"]] for edge in page_edges],
                )
            )
        else:
            lines.append("Действия отсутствуют.")

        section(lines, f"{page_section_number}.5", "Входящие переходы на страницу")
        incoming_edges = [edge for edge in navigation_edges if edge["related_page_id"] == page["id"]]
        if incoming_edges:
            lines.extend(
                table(
                    ["Исходная страница", "Элемент", "Тип", "Цель"],
                    [[edge["source_page"], edge["visual"], edge["type"], edge["target"]] for edge in incoming_edges],
                )
            )
        else:
            lines.append("Входящие переходы в метаданных не найдены.")

        page_section_number += 1

    model_section = page_section_number
    section(lines, str(model_section), "Семантическая модель")
    lines.extend(
        table(
            ["Таблица", "Режим", "Колонок"],
            [[item["name"], item["kind"], len(item["columns"])] for item in model["tables"]],
        )
    )

    section(lines, f"{model_section}.1", "Колонки и безопасные примеры значений")
    lines.extend(
        table(
            ["Таблица", "Колонка", "Тип", "Скрыта", "Уникальных", "Примеры"],
            [
                [table_item["name"], column["name"], column["data_type"], column["hidden"], column["cardinality"], column["examples"]]
                for table_item in model["tables"]
                for column in table_item["columns"]
            ],
        )
    )

    section(lines, f"{model_section}.2", "Меры и DAX")
    for measure in model["measures"]:
        lines.extend(
            [
                f"Мера: {measure['table']}[{measure['name']}]",
                f"Отображаемое название: {measure['display_name']}",
                f"Формат: {measure['format']}",
                "DAX:",
                measure["dax"],
                "",
            ]
        )

    section(lines, f"{model_section}.3", "Связи")
    lines.extend(
        table(
            ["Из", "В", "Кардинальность", "Активна", "Фильтрация", "Security filter"],
            [[rel["from"], rel["to"], rel["cardinality"], rel["active"], rel["filter_direction"], rel.get("security_filter", "—")] for rel in model["relationships"]],
        )
    )

    pq_section = model_section + 1
    section(lines, str(pq_section), "Power Query и источники")
    lines.extend(
        table(
            ["Запрос", "Тип", "Источник", "Подробности"],
            [[item["name"], item["kind"], item["source_type"], item["source_details"]] for item in model["power_query"]],
        )
    )

    security_section = pq_section + 1
    section(lines, str(security_section), "RLS и OLS")
    security = model["security_analysis"]
    lines.append("Обнаруженная схема безопасности:")
    lines.extend(
        table(
            ["Параметр", "Значение"],
            [
                ["Статус анализа", security["analysis_status"]],
                ["Тип RLS", security["rls_pattern"]],
                ["Таблица доступа", security["mapping_table"]],
                ["Колонки идентификации", "; ".join(security["identity_columns"])],
                ["Колонки области доступа", "; ".join(security["access_scope_columns"])],
                ["Функция текущего пользователя", security["identity_function"]],
                ["Значения логинов", security["identity_values"]],
            ],
        )
    )
    lines.append("Интерпретация RLS/OLS является формальной заглушкой до первого прогона на боевом PBIX.")
    lines.append("Распространение RLS по модели:")
    lines.extend(
        table(
            ["Путь", "Статус", "Направление security filter"],
            [[item["path"], item["status"], item["security_direction"]] for item in security["propagation_paths"]],
        )
    )
    lines.append("RLS — определения строковой безопасности, найденные в локальной модели:")
    lines.extend(
        table(
            ["Роль", "Таблица", "DAX-фильтр", "Состояние", "Metadata permission"],
            [[item["role"], item["table"], item["filter_expression"], item["state"], item["metadata_permission"]] for item in model["rls"]],
        )
    )
    lines.append("OLS — ограничения доступа к объектам модели:")
    lines.extend(
        table(
            ["Роль", "Область", "Таблица", "Колонка", "Разрешение"],
            [[item["role"], item["scope"], item["table"], item["column"], item["permission"]] for item in model["ols"]],
        )
    )
    lines.append("Логины в UserAccess не выводятся. Их наличие и кардинальность документируются без значений.")
    lines.append("Назначения пользователей или групп общей роли в Power BI Service локально не подтверждены.")

    unresolved_section = security_section + 1
    section(lines, str(unresolved_section), "Неразрешённые ссылки")
    if unresolved:
        lines.extend(
            table(
                ["Объект", "Причина", "Путь-источник"],
                [[field["object"], field.get("reason", "Не указана"), field["source_path"]] for field in unresolved],
            )
        )
        lines.append("Наличие записи в этом разделе не доказывает, что сам отчёт Power BI сломан.")
    else:
        lines.append("Неразрешённых ссылок не найдено.")

    diagnostics_section = unresolved_section + 1
    section(lines, str(diagnostics_section), "Диагностика извлечения")
    lines.extend(
        table(
            ["Уровень", "Компонент", "Код", "Сообщение"],
            [[issue["severity"], issue["component"], issue["code"], issue["message"]] for issue in data["issues"]],
        )
    )
    lines.extend(
        [
            "",
            "ИТОГОВЫЙ СТАТУС: PARTIAL",
            "Причина: один binding custom visual не удалось разрешить с достаточной уверенностью.",
            LINE,
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    data = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    output = render(data)
    OUTPUT_PATH.write_text(output, encoding="utf-8-sig", newline="\r\n")


if __name__ == "__main__":
    main()
