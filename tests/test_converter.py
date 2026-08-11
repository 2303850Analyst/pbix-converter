from __future__ import annotations

import ast
import codecs
import contextlib
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.enum.section import WD_ORIENT

from pbix_doc.container import PbixContainer, UnsupportedPbixError
from pbix_doc.domain import (
    ColumnInfo,
    ConnectionInfo,
    DocumentationModel,
    FieldRef,
    FileInfo,
    FilterInfo,
    MeasureInfo,
    QueryInfo,
    RelationshipInfo,
    SecurityRule,
    SemanticModelInfo,
    TableInfo,
)
from pbix_doc.cli import build_parser, main
from pbix_doc.pipeline import Options, generate_documentation, resolve_fields
from pbix_doc.policy import METADATA_EXCLUSION_POLICY
from pbix_doc.ooxml import UnsafeDocxError, validate_docx
from pbix_doc.projector import (
    CodeBlock,
    HeadingBlock,
    SafeDocumentModel,
    TableBlock,
    project_document,
    sanitize_dax,
)
from pbix_doc.renderer import atomic_write, render_document
from pbix_doc.report import (
    aggregation_name,
    extract_saved_values,
    extract_title,
    filter_operator,
    parse_report,
    safe_external_target,
)
from pbix_doc.runtime import (
    WORKSPACE_MARKER,
    WORKSPACE_PREFIX,
    ExternalProcessViolation,
    NetworkEgressViolation,
    WorkspaceCleanupViolation,
    cleanup_stale_artifacts,
    install_runtime_guards,
)
from pbix_doc.semantic import (
    extract_power_query_connection_types,
    extract_power_query_source_metadata,
    extract_native_types,
    load_semantic_model,
    profile_examples,
    read_model_metadata,
    redact_connection_secrets,
    safe_error_message,
    normalize_summarize_by,
    normalize_model_data_type,
)


def read_docx_text(payload: bytes | Path) -> str:
    document = Document(BytesIO(payload) if isinstance(payload, bytes) else payload)
    values = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            values.extend(cell.text for cell in row.cells)
    return "\n".join(values)


def write_zip(path: Path, parts: dict[str, object]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            if isinstance(value, bytes):
                payload = value
            else:
                payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            archive.writestr(name, payload)


def pbir_parts() -> dict[str, object]:
    sales_field = {
        "Column": {
            "Expression": {"SourceRef": {"Entity": "Sales"}},
            "Property": "Region",
        }
    }
    revenue_measure = {
        "Measure": {
            "Expression": {"SourceRef": {"Entity": "Sales"}},
            "Property": "Selected region",
        }
    }
    amount_aggregation = {
        "Aggregation": {
            "Expression": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": "Sales"}},
                    "Property": "Amount",
                }
            },
            "Function": 0,
        }
    }
    return {
        "Report/definition/version.json": {"version": "4.0"},
        "Report/definition/report.json": {
            "displayName": "Продажи",
            "filterConfig": {
                "filters": [
                    {
                        "type": "Categorical",
                        "field": sales_field,
                        "condition": {"In": [{"Literal": {"Value": "Секретный регион"}}]},
                    }
                ]
            },
        },
        "Report/definition/pages/pages.json": {"pageOrder": ["overview", "details"]},
        "Report/definition/pages/overview/page.json": {
            "name": "overview",
            "displayName": "Обзор",
            "width": 1280,
            "height": 720,
        },
        "Report/definition/pages/details/page.json": {
            "name": "details",
            "displayName": "Детали",
            "width": 1280,
            "height": 720,
            "filterConfig": {
                "filters": [
                    {
                        "type": "Categorical",
                        "field": sales_field,
                        "condition": {"In": [{"Literal": {"Value": "Другой секрет"}}]},
                    }
                ]
            },
        },
        "Report/definition/pages/overview/visuals/revenue/visual.json": {
            "name": "revenue",
            "title": "Выручка",
            "position": {"x": 540, "y": 20, "z": 0, "width": 220, "height": 100},
            "visual": {
                "visualType": "card",
                "query": {
                    "queryState": {
                        "Values": {
                            "projections": [
                                {"queryRef": "Sales.Selected region", "field": revenue_measure}
                            ]
                        }
                    }
                },
            },
        },
        "Report/definition/pages/overview/visuals/chart/visual.json": {
            "name": "chart",
            "title": "Продажи по регионам",
            "position": {"x": 10, "y": 20, "z": 0, "width": 500, "height": 300},
            "visual": {
                "visualType": "barChart",
                "query": {
                    "queryState": {
                        "Category": {
                            "projections": [
                                {"queryRef": "Sales.Region", "field": sales_field}
                            ]
                        }
                    }
                },
            },
        },
        "Report/definition/pages/overview/visuals/page_button/visual.json": {
            "name": "page_button",
            "title": "К деталям",
            "position": {"x": 10, "y": 340, "z": 1, "width": 160, "height": 40},
            "visual": {"visualType": "actionButton"},
            "actions": [{"type": "PAGE_NAVIGATION", "target": "details"}],
        },
        "Report/definition/pages/details/visuals/bookmark_button/visual.json": {
            "name": "bookmark_button",
            "title": "Вернуть обзор",
            "position": {"x": 10, "y": 10, "z": 0, "width": 160, "height": 40},
            "visual": {"visualType": "actionButton"},
            "actions": [{"type": "BOOKMARK_NAVIGATION", "target": "bookmark_overview"}],
        },
        "Report/definition/pages/details/visuals/revenue/visual.json": {
            "name": "details_revenue",
            "title": "Выручка",
            "position": {"x": 200, "y": 10, "z": 1, "width": 220, "height": 100},
            "visual": {
                "visualType": "card",
                "query": {
                    "queryState": {
                        "Values": {
                            "projections": [
                                {"queryRef": "Sum(Sales.Amount)", "field": amount_aggregation}
                            ]
                        }
                    }
                },
            },
        },
        "Report/definition/bookmarks/bookmarks.json": {
            "items": [{"name": "bookmark_overview", "displayName": "Исходный вид"}]
        },
        "Report/definition/bookmarks/bookmark_overview.bookmark.json": {
            "name": "bookmark_overview",
            "displayName": "Исходный вид",
            "explorationState": {
                "activeSection": "overview",
                "visualContainers": {"chart": {"filters": ["Никогда не печатать"]}},
            },
        },
    }


def legacy_parts() -> dict[str, object]:
    config = {
        "name": "legacy_button",
        "singleVisual": {"visualType": "actionButton"},
        "actions": [{"type": "PAGE_NAVIGATION", "target": "page_b"}],
    }
    layout = {
        "displayName": "Legacy report",
        "version": 1,
        "sections": [
            {
                "name": "page_a",
                "displayName": "A",
                "ordinal": 0,
                "width": 1280,
                "height": 720,
                "visualContainers": [
                    {
                        "x": 1,
                        "y": 2,
                        "z": 0,
                        "width": 100,
                        "height": 40,
                        "config": json.dumps(config),
                    }
                ],
            },
            {"name": "page_b", "displayName": "B", "ordinal": 1},
        ],
    }
    encoded = json.dumps(layout, ensure_ascii=False).encode("utf-16-le")
    return {"Report/Layout": codecs.BOM_UTF16_LE + encoded}


class ReportParserTests(unittest.TestCase):
    def test_pbir_pages_fields_bookmark_and_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fixture.pbix")
            write_zip(path, pbir_parts())
            issues = []
            with PbixContainer(path) as container:
                report = parse_report(container, path.stem, issues)

        self.assertEqual(report.report_format, "PBIR")
        self.assertEqual([page.name for page in report.pages], ["Обзор", "Детали"])
        chart = next(item for item in report.pages[0].visuals if item.id == "chart")
        self.assertEqual(chart.fields[0].qualified_name, "Sales[Region]")
        self.assertEqual(report.bookmarks[0].associated_page_id, "overview")
        self.assertEqual(report.bookmarks[0].affected_visual_ids, ["chart"])
        self.assertEqual(len(report.navigation), 2)
        self.assertTrue(all(edge.status == "RESOLVED" for edge in report.navigation))
        self.assertEqual(report.bookmarks[0].trigger_refs, ["Детали / Вернуть обзор"])
        self.assertEqual(report.report_filters[0].saved_values, ["Секретный регион"])
        self.assertEqual(report.pages[1].filters[0].saved_values, ["Другой секрет"])
        self.assertFalse(issues)

    def test_title_parser_does_not_use_boolean_show_flag_as_title(self) -> None:
        show_only = {
            "title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]
        }
        with_text = {
            "title": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "text": {"expr": {"Literal": {"Value": "'Продажи'"}}},
                    }
                }
            ]
        }
        self.assertEqual(extract_title(show_only, "visual_1"), "visual_1")
        self.assertEqual(extract_title(with_text, "visual_1"), "Продажи")

    def test_saved_filter_values_are_deduplicated_and_bounded(self) -> None:
        literals = [
            {"Literal": {"Value": f"'{index}'"}}
            for index in list(range(25)) + [0, 1, 2]
        ]
        values, count = extract_saved_values(literals)
        self.assertEqual(values, [str(index) for index in range(20)])
        self.assertEqual(count, 25)

    def test_external_url_is_shown_without_credentials_or_parameters(self) -> None:
        self.assertEqual(
            safe_external_target("https://user:secret@example.test/report?id=42#page"),
            "https://example.test/report",
        )
        self.assertEqual(safe_external_target("dynamic-url-expression"), "Адрес определяется динамически")

    def test_filter_operator_respects_not_wrapper(self) -> None:
        self.assertEqual(
            filter_operator({"Not": {"Expression": {"In": [{"Literal": {"Value": "A"}}]}}}),
            "NOT_IN",
        )

    def test_aggregation_codes_are_rendered_as_names(self) -> None:
        expected = {
            0: "SUM",
            1: "AVERAGE",
            2: "COUNT",
            3: "MIN",
            4: "MAX",
            5: "COUNT_NON_NULL",
            6: "MEDIAN",
            7: "STANDARD_DEVIATION",
            8: "VARIANCE",
        }
        for code, name in expected.items():
            with self.subTest(code=code):
                self.assertEqual(aggregation_name(code), name)
                self.assertEqual(aggregation_name(str(code)), name)
        self.assertEqual(aggregation_name(2.0), "COUNT")
        self.assertEqual(aggregation_name("Average"), "AVERAGE")
        self.assertEqual(aggregation_name(None), "АГРЕГАЦИЯ НЕ УКАЗАНА")
        self.assertEqual(aggregation_name(math.nan), "АГРЕГАЦИЯ НЕ УКАЗАНА")
        self.assertEqual(aggregation_name(math.inf), "АГРЕГАЦИЯ НЕ УКАЗАНА")
        self.assertEqual(aggregation_name(99), "НЕИЗВЕСТНАЯ АГРЕГАЦИЯ (код: 99)")

    def test_legacy_utf16_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "legacy.pbix")
            write_zip(path, legacy_parts())
            issues = []
            with PbixContainer(path) as container:
                report = parse_report(container, path.stem, issues)

        self.assertEqual(report.report_format, "LEGACY")
        self.assertEqual([page.id for page in report.pages], ["page_a", "page_b"])
        self.assertEqual(report.navigation[0].target_page_id, "page_b")
        self.assertFalse(issues)

    def test_invalid_container_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "broken.pbix")
            path.write_bytes(b"not a zip")
            with self.assertRaises(UnsupportedPbixError):
                with PbixContainer(path):
                    pass


class RendererTests(unittest.TestCase):
    def test_docx_is_user_facing_and_values_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fixture.pbix")
            write_zip(path, pbir_parts())
            issues = []
            with PbixContainer(path) as container:
                report = parse_report(container, path.stem, issues)
            unnamed_chart = next(item for item in report.pages[0].visuals if item.id == "chart")
            unnamed_chart.title = "356810a1d29047eea77442279e6f909a"
            details_revenue = next(
                item for item in report.pages[1].visuals if item.id == "details_revenue"
            )
            details_revenue.fields.append(
                FieldRef(
                    "Sales",
                    "Amount",
                    "COLUMN",
                    "Y",
                    "VISUAL_DATA",
                    "COUNT",
                    "synthetic-regression",
                )
            )
            details_revenue.fields.append(
                FieldRef(
                    "H$Storage",
                    "Index",
                    "COLUMN",
                    "Y",
                    "VISUAL_DATA",
                    "SUM",
                    "synthetic-regression",
                )
            )
            details_revenue.filters.append(
                FilterInfo(
                    "VISUAL",
                    "R$Relationship[Index]",
                    "Categorical",
                    1,
                    "synthetic-regression",
                    ["internal"],
                )
            )
            report.pages[0].filters.append(
                FilterInfo(
                    "PAGE",
                    "Sales[Region]",
                    "Categorical",
                    2,
                    "synthetic-regression",
                    ["null", "true"],
                )
            )
            semantic = SemanticModelInfo(
                "EMBEDDED",
                tables=[
                    TableInfo(
                        "Sales",
                        False,
                        "Import",
                        [
                            ColumnInfo(
                                "Sales",
                                "Region",
                                "String",
                                False,
                                5,
                                examples=["Центр", "Север", "Юг", "Запад", "Восток"],
                                example_status="PROFILED",
                                format_string="Text",
                                source_column="region_code",
                                native_data_type="SQL: varchar(20); M: type text",
                                summarize_by="Без агрегации",
                            ),
                            ColumnInfo(
                                "Sales",
                                "Amount",
                                "Decimal",
                                False,
                                1000,
                                example_status="NUMERIC_VALUES_HIDDEN",
                                summarize_by="По умолчанию",
                            ),
                            ColumnInfo(
                                "Sales",
                                "UnusedFlag",
                                "Boolean",
                                False,
                                2,
                                example_status="NOT_PROFILED",
                            ),
                            ColumnInfo(
                                "Sales",
                                "CalculatedAmount",
                                "Decimal",
                                False,
                                None,
                                expression="Sales[Amount] * 2",
                            ),
                        ],
                    ),
                    TableInfo(
                        "LocalDateTable_123",
                        True,
                        "Import",
                        [ColumnInfo("LocalDateTable_123", "Date", "DateTime", True, 365)],
                    ),
                    TableInfo(
                        "DateTableTemplate_456",
                        True,
                        "Import",
                        [ColumnInfo("DateTableTemplate_456", "Month", "String", True, 12)],
                    ),
                    TableInfo(
                        "H$Storage",
                        True,
                        "Import",
                        [ColumnInfo("H$Storage", "Index", "Int64", True, 1)],
                    ),
                    TableInfo(
                        "R$Relationship",
                        True,
                        "Import",
                        [ColumnInfo("R$Relationship", "Index", "Int64", True, 1)],
                    ),
                    TableInfo(
                        "U$Hierarchy",
                        True,
                        "Import",
                        [ColumnInfo("U$Hierarchy", "Index", "Int64", True, 1)],
                    ),
                ],
                measures=[
                    MeasureInfo(
                        "Sales",
                        "Selected region",
                        'CALCULATE([Revenue], Sales[Region] = "North")',
                        None,
                        None,
                        None,
                    ),
                    MeasureInfo(
                        "Sales",
                        "Uses internal storage",
                        "SUM('H$Storage'[Index]) + [Selected region]",
                        None,
                        None,
                        None,
                    ),
                    MeasureInfo(
                        "U$Hierarchy",
                        "Internal measure",
                        "COUNTROWS('U$Hierarchy')",
                        None,
                        None,
                        None,
                    ),
                ],
                relationships=[
                    RelationshipInfo(
                        "Sales[Region]",
                        "R$Relationship[Index]",
                        "ManyToOne",
                        True,
                        "Single",
                        None,
                    )
                ],
                queries=[
                    QueryInfo("H$Storage", "POWER_QUERY", True),
                    QueryInfo(
                        "Sales Import",
                        "POWER_QUERY",
                        True,
                        ["SQL Server"],
                        ["Сервер: srv01; База: DWH"],
                        ["Server=srv01;Database=DWH;Password=<СКРЫТО>"],
                        [
                            'let Source = Sql.Database("srv01", "DWH"), '
                            'Data = Value.NativeQuery(Source, "SELECT amount FROM dbo.sales") '
                            'in Data'
                        ],
                    ),
                ],
                rls=[SecurityRule("RLS", "Internal", "R$Relationship", None, "TRUE()", "Read")],
                ols=[SecurityRule("OLS", "Internal", "U$Hierarchy", "Index", None, "None")],
                connections=[
                    ConnectionInfo(
                        "SQL Server",
                        ["Сервер: srv01", "База: DWH"],
                        "Server=srv01;Database=DWH;Password=<СКРЫТО>",
                    )
                ],
            )
            resolve_fields(report.pages, semantic, issues)
            model = DocumentationModel(
                FileInfo(path, path.stat().st_size, "0" * 64, datetime.now(timezone.utc)),
                report,
                semantic,
                issues,
                datetime.now(timezone.utc),
                "test",
            )
            payload = render_document(project_document(model))
            text = read_docx_text(payload)

        self.assertIn("Переходы и действия (Все закладки)", text)
        self.assertNotIn("Кнопки и переходы", text)
        self.assertIn("Все фильтры", text)
        self.assertNotIn("Пользовательские маршруты", text)
        self.assertNotIn("Путеводитель по листам и визуалам", text)
        self.assertNotIn("POWER BI · ТЕХНИЧЕСКОЕ РУКОВОДСТВО", text)
        self.assertIn("Одинаковое название — возможные различия", text)
        self.assertIn("РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ", text)
        self.assertNotIn("Каталог KPI и вычислений", text)
        self.assertNotIn("Определение DAX", text)
        self.assertNotIn("Где используется и какой контекст обнаружен", text)
        self.assertIn("Реестр полей отчёта", text)
        self.assertIn("Состав листа", text)
        self.assertIn("Варианты названий", text)
        self.assertIn("Наименование поля таблицы в источнике", text)
        self.assertIn("Sales[Region]", text)
        self.assertIn("region_code", text)
        self.assertIn("SQL: varchar(20); M: type text", text)
        self.assertIn("Sales[Selected region]", text)
        self.assertIn("Секретный регион", text)
        self.assertIn("Другой секрет", text)
        self.assertNotIn("Никогда не печатать", text)
        self.assertNotIn("<VALUES HIDDEN>", text)
        self.assertNotIn("NOT_AVAILABLE", text)
        self.assertNotIn("<IDENTIFIER_OR_HIDDEN>", text)
        self.assertNotIn("LocalDateTable_", text)
        self.assertNotIn("DateTableTemplate_", text)
        self.assertNotIn("H$Storage", text)
        self.assertNotIn("R$Relationship", text)
        self.assertNotIn("U$Hierarchy", text)
        self.assertIn("Uses internal storage", text)
        self.assertIn("<СЛУЖЕБНАЯ ССЫЛКА>", text)
        self.assertIn("Сервер / база / путь", text)
        self.assertIn("Строка подключения", text)
        self.assertIn("M-код: запрос «Sales Import»", text)
        self.assertIn('Sql.Database("srv01", "DWH")', text)
        self.assertIn("SELECT amount FROM dbo.sales", text)
        self.assertIn("Password=<СКРЫТО>", text)
        self.assertNotIn("RLS и OLS — формальная заглушка", text)
        self.assertNotIn("Статус анализа: PROVISIONAL", text)
        self.assertNotIn("Диагностика извлечения", text)
        self.assertNotIn("PBIXRAY_NOT_INSTALLED", text)
        self.assertNotIn("Технические идентификаторы документа", text)
        self.assertNotIn("ИТОГОВЫЙ СТАТУС", text)
        self.assertNotIn("Неразрешённые ссылки", text)
        self.assertNotIn("Неразрешённых ссылок не найдено", text)
        self.assertNotIn("Зависимости показаны символически", text)
        self.assertNotIn("P01 ·", text)
        self.assertNotIn("V001 ·", text)
        self.assertNotIn("B01 ·", text)
        self.assertNotIn("K001 ·", text)
        self.assertNotIn("SHA-256", text)
        self.assertNotIn("Версия генератора", text)
        self.assertNotIn("Версия PBIXRay", text)
        self.assertNotIn("Профиль значений", text)
        self.assertNotIn("**", text)
        self.assertIn("лист «Обзор» (P01)", text)
        self.assertIn("закладка «Исходный вид» (B01)", text)
        self.assertIn("таблица «Sales»", text)
        self.assertNotIn(str(path.resolve()), text)
        self.assertIn("Источник", text)
        self.assertIn('Sales[Region] = "<VALUE>"', text)
        self.assertNotIn('"North"', text)
        self.assertEqual(sum(value in text for value in ["Центр", "Север", "Юг", "Запад", "Восток"]), 5)
        document = Document(BytesIO(payload))
        self.assertEqual(next(paragraph.text for paragraph in document.paragraphs if paragraph.text), "Об отчёте")
        headings = {
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.style.name.startswith("Heading")
        }
        self.assertNotIn("Статус извлечения", headings)
        self.assertNotIn("Как читать документ", headings)
        self.assertNotIn("Карта листов", headings)
        self.assertNotIn("Фильтры уровня отчёта", headings)
        table_headers = [tuple(cell.text for cell in table.rows[0].cells) for table in document.tables]
        overview_index = next(
            index for index, row in enumerate(table_headers) if row == ("Параметр", "Значение")
        )
        registry_index = next(index for index, row in enumerate(table_headers) if row[0] == "Таблица[Поле]")
        composition_indexes = [
            index
            for index, row in enumerate(table_headers)
            if row[:2] == ("Элемент", "Название")
        ]
        composition_index = composition_indexes[0]
        navigation_index = next(
            index
            for index, row in enumerate(table_headers)
            if row[:2] == ("Элемент / закладка", "Тип действия")
        )
        filters_index = next(
            index
            for index, row in enumerate(table_headers)
            if row[:2] == ("Область", "Источник фильтра")
        )
        registry = document.tables[registry_index]
        compositions = [document.tables[index] for index in composition_indexes]
        registry_rows = {row.cells[0].text: row for row in registry.rows[1:]}
        self.assertEqual(registry_index, overview_index + 1)
        self.assertEqual(composition_index, registry_index + 1)
        self.assertEqual(len(registry.columns), 11)
        self.assertEqual(
            table_headers[registry_index],
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
        )
        self.assertEqual(
            table_headers[composition_index],
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
        )
        self.assertEqual(navigation_index, composition_indexes[-1] + 1)
        self.assertEqual(filters_index, navigation_index + 1)
        composition_rows = {
            row.cells[0].text: row
            for table in compositions
            for row in table.rows[1:]
        }
        self.assertTrue(all(re.fullmatch(r"[VB]\d{2,3}", key) for key in composition_rows))
        self.assertEqual(composition_rows["B01"].cells[1].text, "«Исходный вид»")
        self.assertIn("закладка «Исходный вид» (B01)", composition_rows["V001"].cells[6].text)
        self.assertIn("лист «Детали» (P02)", composition_rows["V003"].cells[7].text)
        self.assertIn("Sales[Selected region]", composition_rows["V002"].cells[4].text)
        self.assertIn("CALCULATE", composition_rows["V002"].cells[4].text)
        self.assertIn('"<VALUE>"', composition_rows["V002"].cells[4].text)
        self.assertIn("кнопка «Вернуть обзор» (V004)", composition_rows["B01"].cells[7].text)
        self.assertEqual(composition_rows["V001"].cells[1].text, "«Без названия 1»")
        self.assertIn("«Без названия 1»", registry_rows["Sales[Region]"].cells[1].text)
        self.assertEqual(
            composition_rows["V001"].cells[5].text,
            "Фильтр отчёта по полю Sales[Region]; Фильтр листа по полю Sales[Region]",
        )
        self.assertNotIn("Пусто", composition_rows["V001"].cells[5].text)
        self.assertNotIn("Да", composition_rows["V001"].cells[5].text)
        self.assertNotIn("Категориальный", composition_rows["V001"].cells[5].text)
        navigation_rows = {
            row.cells[0].text: row for row in document.tables[navigation_index].rows[1:]
        }
        self.assertIn("«Исходный вид» (B01)", navigation_rows)
        self.assertIn("лист «Обзор» (P01)", navigation_rows["«Исходный вид» (B01)"].cells[4].text)
        navigation_types = {row.cells[1].text for row in document.tables[navigation_index].rows[1:]}
        self.assertTrue(any(value.startswith("Закладка") for value in navigation_types))
        self.assertIn("Переход на лист", navigation_types)
        filter_rows = document.tables[filters_index].rows[1:]
        self.assertTrue(any(row.cells[2].text == "Sales[Region]" for row in filter_rows))
        self.assertTrue(any("Секретный регион" in row.cells[4].text for row in filter_rows))
        self.assertTrue(all(row.cells[3].text == "Выбраны значения" for row in filter_rows))
        self.assertTrue(all(row.cells[3].text == "" for row in registry.rows[1:]))
        self.assertFalse(any(name.casefold().startswith("local") for name in registry_rows))
        self.assertEqual(registry_rows["Sales[Region]"].cells[2].text, "String")
        self.assertEqual(registry_rows["Sales[UnusedFlag]"].cells[4].text, "—")
        self.assertIn("Sales[UnusedFlag]", registry_rows)
        self.assertEqual(registry_rows["Sales[UnusedFlag]"].cells[9].text, "—")
        self.assertEqual(registry_rows["Sales[UnusedFlag]"].cells[6].text, "—")
        self.assertEqual(registry_rows["Sales[UnusedFlag]"].cells[7].text, "—")
        self.assertEqual(registry_rows["Sales[Selected region]"].cells[6].text, "—")
        self.assertEqual(registry_rows["Sales[Selected region]"].cells[7].text, "—")
        self.assertEqual(registry_rows["Sales[CalculatedAmount]"].cells[6].text, "—")
        self.assertEqual(registry_rows["Sales[CalculatedAmount]"].cells[7].text, "—")
        self.assertNotEqual(registry_rows["Sales[CalculatedAmount]"].cells[4].text, "—")
        self.assertIn("Sales[Selected region]", registry_rows["Sales[Region]"].cells[8].text)
        self.assertEqual(
            registry_rows["Sales[Amount]"].cells[10].text,
            "По умолчанию, SUM, COUNT",
        )
        bold_reference_names = {
            run.text
            for paragraph in document.paragraphs
            for run in paragraph.runs
            if run.bold
        }
        for table in document.tables:
            bold_reference_names.update(
                run.text
                for row in table.rows
                for cell in row.cells
                for paragraph in cell.paragraphs
                for run in paragraph.runs
                if run.bold
            )
        self.assertIn("«Обзор»", bold_reference_names)
        self.assertIn("«Sales»", bold_reference_names)
        self.assertTrue(all(section.orientation == WD_ORIENT.LANDSCAPE for section in document.sections))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            relations = b"\n".join(
                archive.read(name) for name in names if name.casefold().endswith(".rels")
            )
            document_xml = archive.read("word/document.xml")
        self.assertNotIn(b'TargetMode="External"', relations)
        self.assertNotIn(b"vbaProject", b"\n".join(name.encode() for name in names))
        self.assertIn(b"w:tblGrid", document_xml)
        self.assertIn(b"w:tblHeader", document_xml)
        for excluded in (b"LocalDateTable", b"DateTableTemplate", b"H$", b"R$", b"U$"):
            self.assertNotIn(excluded, document_xml)

    def test_atomic_writer_publishes_valid_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fixture.pbix")
            write_zip(path, pbir_parts())
            issues = []
            with PbixContainer(path) as container:
                report = parse_report(container, path.stem, issues)
            model = DocumentationModel(
                FileInfo(path, path.stat().st_size, "0" * 64, datetime.now(timezone.utc)),
                report,
                SemanticModelInfo("NO_MODEL"),
                issues,
                datetime.now(timezone.utc),
                "test",
            )
            output = Path(directory, "output.docx")
            atomic_write(output, render_document(project_document(model)))
            validate_docx(output)
            content = output.read_bytes()
        self.assertTrue(content.startswith(b"PK"))

    def test_dax_sanitizer_is_token_aware_and_fails_closed(self) -> None:
        safe = sanitize_dax('CALCULATE([Revenue], Sales[Region] = "N""orth") // secret')
        compact = sanitize_dax(
            """
            VAR/* note */Result =
                CALCULATE (
                    SUM ( Sales[Amount] ),
                    'Sales  Facts'[Region Name] = "North"
                )
            RETURN
                Result
            """
        )
        redacted = sanitize_dax("SUM('H$Storage'[Index]) + [Revenue]")
        malformed = sanitize_dax('CALCULATE([Revenue], "unfinished)')
        self.assertEqual(safe.status, "OK")
        self.assertIn('"<VALUE>"', safe.text or "")
        self.assertNotIn("secret", safe.text or "")
        self.assertIn("[Revenue]", safe.references)
        self.assertEqual(
            compact.text,
            "VAR Result = CALCULATE(SUM(Sales[Amount]), 'Sales  Facts'[Region Name] = \"<VALUE>\") RETURN Result",
        )
        self.assertEqual(redacted.text, "SUM(<СЛУЖЕБНАЯ ССЫЛКА>) + [Revenue]")
        self.assertEqual(redacted.status, "PARTIAL")
        self.assertEqual(redacted.references, ["[Revenue]"])
        self.assertEqual(malformed.status, "PARTIAL")
        self.assertIsNone(malformed.text)

    def test_render_is_deterministic_and_txt_is_rejected(self) -> None:
        generated = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        model = SafeDocumentModel("Отчёт", "Описание", "fixture.pbix", generated, "OK", [HeadingBlock(1, "Раздел")])
        first = render_document(model)
        second = render_document(model)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            atomic_write(Path(directory, "legacy.txt"), first)

    def test_table_renderer_does_not_silently_truncate_projected_text(self) -> None:
        generated = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        long_text = "DAX:" + "X" * 6_000
        model = SafeDocumentModel(
            "Отчёт",
            "Описание",
            "fixture.pbix",
            generated,
            "OK",
            [TableBlock(("Выражение DAX",), [(long_text,)], (9_360,))],
        )

        self.assertIn(long_text, read_docx_text(render_document(model)))

    def test_code_renderer_does_not_truncate_power_query(self) -> None:
        generated = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        long_text = "let Source = \"" + "X" * 13_000 + "\" in Source_END"
        model = SafeDocumentModel(
            "Отчёт",
            "Описание",
            "fixture.pbix",
            generated,
            "OK",
            [CodeBlock(long_text)],
        )

        self.assertIn(long_text, read_docx_text(render_document(model)))

    def test_ooxml_validator_rejects_external_relationship(self) -> None:
        generated = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        payload = render_document(
            SafeDocumentModel("Отчёт", "Описание", "fixture.pbix", generated, "OK", [])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "unsafe.docx")
            with zipfile.ZipFile(BytesIO(payload)) as source, zipfile.ZipFile(path, "w") as target:
                for name in source.namelist():
                    data = source.read(name)
                    if name == "_rels/.rels":
                        data = data.replace(
                            b"</Relationships>",
                            b'<Relationship Id="rExternal" Type="x" Target="https://example.org" '
                            b'TargetMode="External"/></Relationships>',
                        )
                    target.writestr(name, data)
            with self.assertRaises(UnsafeDocxError):
                validate_docx(path)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_without_embedded_model_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory, "fixture.pbix")
            output_path = Path(directory, "result.docx")
            write_zip(input_path, pbir_parts())
            model = generate_documentation(input_path, output_path, Options(example_limit=5))
            text = read_docx_text(output_path)
            output_exists = output_path.exists()

        self.assertTrue(output_exists)
        self.assertNotEqual(model.semantic_model.status, "EMBEDDED")
        self.assertNotIn("ИТОГОВЫЙ СТАТУС", text)
        self.assertIn("Секретный регион", text)

    def test_workspace_is_created_beside_output_and_removed(self) -> None:
        observed: list[Path] = []

        def fake_model(_: Path, workspace: Path, example_limit: int, __: list) -> SemanticModelInfo:
            observed.append(workspace)
            self.assertEqual(workspace.parent, output_path.parent)
            self.assertTrue(workspace.joinpath(WORKSPACE_MARKER).is_file())
            self.assertEqual(example_limit, 5)
            return SemanticModelInfo("NO_MODEL")

        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory, "fixture.pbix")
            output_path = Path(directory, "result.docx")
            write_zip(input_path, pbir_parts())
            with patch("pbix_doc.pipeline.load_semantic_model", side_effect=fake_model):
                generate_documentation(input_path, output_path, Options(example_limit=100))
            remaining = list(Path(directory).glob(f"{WORKSPACE_PREFIX}*"))

        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())
        self.assertFalse(remaining)

    def test_workspace_cleanup_failure_is_fatal_and_output_is_not_replaced(self) -> None:
        def fake_model(_: Path, workspace: Path, __: int, ___: list) -> SemanticModelInfo:
            workspace.joinpath("extracted.bin").write_bytes(b"temporary")
            return SemanticModelInfo("NO_MODEL")

        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory, "fixture.pbix")
            output_path = Path(directory, "result.docx")
            write_zip(input_path, pbir_parts())
            output_path.write_bytes(b"existing output")
            with (
                patch("pbix_doc.pipeline.load_semantic_model", side_effect=fake_model),
                patch("pbix_doc.runtime.shutil.rmtree", side_effect=OSError("locked")),
                self.assertRaises(WorkspaceCleanupViolation),
            ):
                generate_documentation(input_path, output_path)
            remaining = list(Path(directory).glob(f"{WORKSPACE_PREFIX}*"))

            self.assertEqual(output_path.read_bytes(), b"existing output")
            self.assertEqual(len(remaining), 1)


class RuntimePolicyTests(unittest.TestCase):
    def test_metadata_exclusion_policy_handles_tables_and_qualified_references(self) -> None:
        for value in (
            "LocalDateTable_123[Date]",
            "DateTableTemplate_456[Month]",
            "H$Storage[Index]",
            "'R$Relationship'[Index]",
            "u$Hierarchy",
        ):
            with self.subTest(value=value):
                self.assertTrue(METADATA_EXCLUSION_POLICY.excludes(value))
        self.assertFalse(METADATA_EXCLUSION_POLICY.excludes("BusinessLocality[Name]"))

    def test_stale_owned_workspace_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            stale = parent.joinpath(f"{WORKSPACE_PREFIX}stale")
            stale.mkdir()
            stale.joinpath(WORKSPACE_MARKER).write_text(
                json.dumps({"application": "pbix-doc", "pid": 2_000_000_000}),
                encoding="ascii",
            )
            stale.joinpath("data.pbixray").write_bytes(b"temporary")
            cleanup_stale_artifacts(parent)
            self.assertFalse(stale.exists())

    def test_runtime_guards_separate_network_and_process_violations(self) -> None:
        install_runtime_guards()
        for event in ("socket.__new__", "socket.connect", "socket.sendto"):
            with self.subTest(event=event), self.assertRaises(NetworkEgressViolation):
                sys.audit(event)
        for event in ("subprocess.Popen", "os.system"):
            with self.subTest(event=event), self.assertRaises(ExternalProcessViolation):
                sys.audit(event)

        with self.assertRaises(NetworkEgressViolation):
            socket.socket()
        with self.assertRaises(ExternalProcessViolation):
            subprocess.run([sys.executable, "-c", "pass"], check=True)

    def test_network_violation_cannot_be_downgraded_by_semantic_adapter(self) -> None:
        fake_pbixray = types.ModuleType("pbixray")
        fake_pbixray.LiveConnectionError = type("LiveConnectionError", (Exception,), {})
        fake_pbixray.NoEmbeddedModelError = type("NoEmbeddedModelError", (Exception,), {})

        def blocked(*_: object, **__: object) -> None:
            raise NetworkEgressViolation("blocked")

        fake_pbixray.PBIXRay = blocked
        with patch.dict(sys.modules, {"pbixray": fake_pbixray}), self.assertRaises(NetworkEgressViolation):
            load_semantic_model(Path("fixture.pbix"), None, 0, [])

    def test_application_source_has_no_network_or_process_imports(self) -> None:
        forbidden = {"git", "http", "requests", "socket", "subprocess", "urllib", "webbrowser"}
        source_root = Path(__file__).parents[1].joinpath("src", "pbix_doc")
        imports: set[str] = set()
        for path in source_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.partition(".")[0])
        self.assertFalse(imports & forbidden)

    def test_cli_has_no_temp_directory_override(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["input.pbix", "output.docx", "--temp-dir", os.curdir])

    def test_cli_reports_compact_error_without_traceback(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            exit_code = main(["missing.pbix", "output.docx"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Ошибка:", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_cli_returns_distinct_code_for_runtime_policy_violation(self) -> None:
        errors = io.StringIO()
        with (
            patch("pbix_doc.cli.generate_documentation", side_effect=NetworkEgressViolation("blocked")),
            contextlib.redirect_stderr(errors),
        ):
            exit_code = main(["input.pbix", "output.docx"])
        self.assertEqual(exit_code, 4)
        self.assertIn("Критическая ошибка политики VDI", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_bootstrap_installs_guards_before_cli_import(self) -> None:
        source = Path(__file__).parents[1].joinpath("src", "pbix_doc", "bootstrap.py").read_text(encoding="utf-8")
        self.assertLess(source.index("install_runtime_guards()"), source.index("from .cli import main"))

    def test_dependency_error_redacts_local_paths(self) -> None:
        sensitive_path = Path(r"C:\Corporate\Reports\secret.pbix")
        message = safe_error_message(RuntimeError(f"Cannot open {sensitive_path}"), sensitive_path)
        self.assertEqual(message, "Cannot open <LOCAL_PATH>")


class ProfilerTests(unittest.TestCase):
    def test_profiler_keeps_only_top_text_values_and_suppresses_sensitive_fields(self) -> None:
        import pandas as pd

        region = ColumnInfo("Access", "Region", "string", False, 8)
        login = ColumnInfo("Access", "UserLogin", "string", False, 8)
        amount = ColumnInfo("Access", "AmountBand", "int64", False, 3)
        internal = ColumnInfo("H$Storage", "Index", "string", False, 1)
        semantic = SemanticModelInfo(
            "EMBEDDED",
            tables=[
                TableInfo("Access", False, "Import", [region, login, amount]),
                TableInfo("H$Storage", True, "Import", [internal]),
            ],
        )

        class FakeModel:
            calls: list[str] = []

            def iter_table(self, table_name: str, *_: object, **__: object):
                self.calls.append(table_name)
                yield pd.DataFrame(
                    {
                        "Region": ["A", "A", "B", "C", "D", "E", "F", "person@example.org"],
                    }
                )

        issues = []
        profile_examples(FakeModel(), semantic, 5, issues)

        self.assertEqual(region.examples, ["A", "B", "C", "D", "E"])
        self.assertEqual(login.example_status, "IDENTIFIER_OR_HIDDEN")
        self.assertEqual(amount.example_status, "NUMERIC_VALUES_HIDDEN")
        self.assertEqual(internal.example_status, "NOT_PROFILED")
        self.assertEqual(FakeModel.calls, ["Access"])
        self.assertFalse(issues)


class SemanticAdapterTests(unittest.TestCase):
    def test_summarize_by_codes_are_normalized_without_bare_numbers(self) -> None:
        expected = {
            1: "По умолчанию",
            2: "Без агрегации",
            3: "SUM",
            4: "MIN",
            5: "MAX",
            6: "COUNT",
            7: "AVERAGE",
            8: "DISTINCT_COUNT",
        }
        for code, name in expected.items():
            with self.subTest(code=code):
                self.assertEqual(normalize_summarize_by(code), name)
                self.assertEqual(normalize_summarize_by(str(code)), name)
        self.assertEqual(normalize_summarize_by(3.0), "SUM")
        self.assertEqual(normalize_summarize_by("DoNotSummarize"), "Без агрегации")
        self.assertEqual(normalize_summarize_by(99), "НЕИЗВЕСТНЫЙ КОД (99)")
        self.assertIsNone(normalize_summarize_by(""))
        self.assertIsNone(normalize_summarize_by(math.nan))
        self.assertIsNone(normalize_summarize_by(math.inf))

    def test_model_data_type_codes_are_rendered_as_names(self) -> None:
        expected = {
            2: "String",
            6: "Int64",
            8: "Double",
            9: "DateTime",
            10: "Decimal",
            11: "Boolean",
            17: "Binary",
            20: "Variant",
        }
        for code, name in expected.items():
            with self.subTest(code=code):
                self.assertEqual(normalize_model_data_type(code), name)
                self.assertEqual(normalize_model_data_type(str(code)), name)
        self.assertIsNone(normalize_model_data_type(99))

    def test_native_query_names_types_and_full_code_are_retained(self) -> None:
        class FakeModel:
            tmschema_tables = [{"Name": "Sales", "IsHidden": False, "StorageMode": "Import"}]
            tmschema_columns = [
                {
                    "TableName": "Sales",
                    "Name": "Amount",
                    "DataType": "Decimal",
                    "FormatString": "#,0.00",
                    "SourceColumn": "amount_net",
                    "SummarizeBy": 3,
                }
            ]
            schema = [{"TableName": "Sales", "ColumnName": "Amount", "SemanticType": "Decimal"}]
            statistics = [{"TableName": "Sales", "ColumnName": "Amount", "Cardinality": 100}]
            dax_columns: list[dict[str, object]] = []
            tables: list[dict[str, object]] = []
            dax_measures: list[dict[str, object]] = []
            relationships: list[dict[str, object]] = []
            power_query = [
                {
                    "TableName": "Sales",
                    "Expression": (
                        'let Source = Value.NativeQuery(Sql.Database("server", "database"), '
                        '"SELECT CAST(amount AS decimal(18,2)) AS amount_net FROM dbo.sales"), '
                        'Typed = Table.TransformColumnTypes(Source,{{"amount_net", Currency.Type}}) in Typed'
                    ),
                }
            ]
            rls: list[dict[str, object]] = []
            ols: list[dict[str, object]] = []
            connections: list[dict[str, object]] = []

        issues = []
        semantic = read_model_metadata(FakeModel(), issues)
        column = semantic.tables[0].columns[0]

        self.assertEqual(column.data_type, "Decimal")
        self.assertEqual(column.format_string, "#,0.00")
        self.assertEqual(column.source_column, "amount_net")
        self.assertEqual(column.native_data_type, "SQL: decimal(18,2); M: Currency.Type")
        self.assertEqual(column.summarize_by, "SUM")
        self.assertEqual(semantic.queries[0].connection_types, ["SQL Server"])
        self.assertEqual(semantic.connection_types, ["SQL Server"])
        self.assertEqual(
            semantic.queries[0].source_details,
            ["Сервер: server; База: database"],
        )
        self.assertIn("server", semantic.queries[0].expressions[0])
        self.assertIn("SELECT CAST", semantic.queries[0].expressions[0])
        self.assertFalse(issues)

    def test_native_types_are_read_only_from_native_query_and_type_steps(self) -> None:
        expression = (
            'let Note = "SELECT CAST(fake AS int) AS fake_alias", '
            '// Value.NativeQuery(Db, "SELECT CAST(commented AS int) AS commented_alias")\n'
            'Source = Value.NativeQuery(Sql.Database("server", "database"), '
            '"SELECT CAST(amount AS decimal(18,2)) AS amount_net FROM dbo.sales"), '
            'Unrelated = {{"fake_m", type text}}, '
            'Typed = Table.TransformColumnTypes(Source,{{"amount_net", Currency.Type}}) in Typed'
        )

        self.assertEqual(
            extract_native_types(expression),
            {"amount_net": ["SQL: decimal(18,2)", "M: Currency.Type"]},
        )

    def test_power_query_connectors_are_detected_without_source_arguments(self) -> None:
        expression = (
            'let Db = Sql.Database("server", "database"), '
            'File = Excel.Workbook(File.Contents("private.xlsx")) in Db'
        )
        self.assertEqual(
            extract_power_query_connection_types(expression),
            ["Excel", "SQL Server", "Файл"],
        )

    def test_power_query_sources_are_extracted_and_secrets_are_redacted(self) -> None:
        expression = (
            'let Db = Odbc.DataSource("Driver=X;Server=srv;Pwd=plain password;Token=abc"), '
            'ApiKey = "key-123", '
            'Web = Web.Contents("https://user:pass@host/path?sig=signature&x=1") in Db'
        )
        sanitized = redact_connection_secrets(expression)
        details, connection_strings = extract_power_query_source_metadata(expression)

        for secret in ("plain password", "abc", "key-123", "pass", "signature"):
            self.assertNotIn(secret, sanitized)
            self.assertNotIn(secret, repr((details, connection_strings)))
        self.assertGreaterEqual(sanitized.count("<СКРЫТО>"), 5)
        self.assertEqual(
            connection_strings,
            ["Driver=X;Server=srv;Pwd=<СКРЫТО>;Token=<СКРЫТО>"],
        )

        json_like = redact_connection_secrets(
            '\"api_key\": \"json-secret\"; PrivateKey=\"private-secret\"; AccessKey=plain-secret'
        )
        self.assertNotIn("json-secret", json_like)
        self.assertNotIn("private-secret", json_like)
        self.assertNotIn("plain-secret", json_like)
        self.assertEqual(json_like.count("<СКРЫТО>"), 3)
        self.assertEqual(
            details,
            ["URL: https://user:<СКРЫТО>@host/path?sig=<СКРЫТО>&x=1"],
        )

    def test_mashup_queries_are_included_and_parameters_are_skipped(self) -> None:
        class FakeModel:
            tmschema_tables: list[dict[str, object]] = []
            tmschema_columns: list[dict[str, object]] = []
            schema: list[dict[str, object]] = []
            statistics: list[dict[str, object]] = []
            dax_columns: list[dict[str, object]] = []
            tables: list[dict[str, object]] = []
            dax_measures: list[dict[str, object]] = []
            relationships: list[dict[str, object]] = []
            power_query: list[dict[str, object]] = []
            mashup_queries = [
                {
                    "Name": "Sales",
                    "Kind": "Query",
                    "IsParameter": False,
                    "Expression": 'let Source = Odbc.DataSource("dsn=private") in Source',
                },
                {
                    "Name": "Environment",
                    "Kind": "Parameter",
                    "IsParameter": True,
                    "Expression": '"production"',
                },
            ]
            rls: list[dict[str, object]] = []
            ols: list[dict[str, object]] = []
            connections: list[dict[str, object]] = []

        semantic = read_model_metadata(FakeModel(), [])

        self.assertEqual([query.name for query in semantic.queries], ["Sales"])
        self.assertEqual(semantic.queries[0].connection_types, ["ODBC"])
        self.assertEqual(semantic.queries[0].connection_strings, ["dsn=private"])
        self.assertIn("dsn=private", semantic.queries[0].expressions[0])

    def test_model_connection_metadata_is_retained_with_secret_redaction(self) -> None:
        class FakeModel:
            tmschema_tables: list[dict[str, object]] = []
            tmschema_columns: list[dict[str, object]] = []
            schema: list[dict[str, object]] = []
            statistics: list[dict[str, object]] = []
            dax_columns: list[dict[str, object]] = []
            tables: list[dict[str, object]] = []
            dax_measures: list[dict[str, object]] = []
            relationships: list[dict[str, object]] = []
            power_query: list[dict[str, object]] = []
            rls: list[dict[str, object]] = []
            ols: list[dict[str, object]] = []
            connections = [
                {
                    "connectionType": "SQL Server",
                    "server": "srv01",
                    "database": "DWH",
                    "connectionString": "Server=srv01;Database=DWH;Pwd=plain-secret",
                }
            ]

        semantic = read_model_metadata(FakeModel(), [])

        self.assertEqual(semantic.connections[0].source_details, ["Сервер: srv01", "База: DWH"])
        self.assertEqual(
            semantic.connections[0].connection_string,
            "Server=srv01;Database=DWH;Pwd=<СКРЫТО>",
        )
        self.assertNotIn("plain-secret", repr(semantic))


if __name__ == "__main__":
    unittest.main()
