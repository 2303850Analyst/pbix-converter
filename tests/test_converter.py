from __future__ import annotations

import codecs
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pbix_doc.container import PbixContainer, UnsupportedPbixError
from pbix_doc.domain import (
    ColumnInfo,
    DocumentationModel,
    FileInfo,
    SemanticModelInfo,
    TableInfo,
)
from pbix_doc.pipeline import Options, generate_documentation, resolve_fields
from pbix_doc.renderer import atomic_write, render_document
from pbix_doc.report import parse_report
from pbix_doc.semantic import profile_examples


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
        self.assertEqual(report.pages[0].visuals[0].fields[0].qualified_name, "Sales[Region]")
        self.assertEqual(report.bookmarks[0].associated_page_id, "overview")
        self.assertEqual(report.bookmarks[0].affected_visual_ids, ["chart"])
        self.assertEqual(len(report.navigation), 2)
        self.assertTrue(all(edge.status == "RESOLVED" for edge in report.navigation))
        self.assertEqual(report.bookmarks[0].trigger_refs, ["Детали / Вернуть обзор"])
        self.assertFalse(issues)

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
    def test_values_are_hidden_and_top_examples_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "fixture.pbix")
            write_zip(path, pbir_parts())
            issues = []
            with PbixContainer(path) as container:
                report = parse_report(container, path.stem, issues)
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
                            )
                        ],
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
            text = render_document(model)

        self.assertIn("Закладки и связанные листы", text)
        self.assertIn("Исходящие переходы", text)
        self.assertIn("Входящие переходы", text)
        self.assertIn("<VALUES HIDDEN>", text)
        self.assertNotIn("Секретный регион", text)
        self.assertNotIn("Никогда не печатать", text)
        self.assertEqual(sum(value in text for value in ["Центр", "Север", "Юг", "Запад", "Восток"]), 5)

    def test_atomic_writer_uses_utf8_bom_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "output.txt")
            atomic_write(output, "one\r\ntwo\r\n")
            content = output.read_bytes()
        self.assertTrue(content.startswith(codecs.BOM_UTF8))
        self.assertIn(b"one\r\ntwo\r\n", content)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_without_embedded_model_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory, "fixture.pbix")
            output_path = Path(directory, "result.txt")
            write_zip(input_path, pbir_parts())
            model = generate_documentation(input_path, output_path, Options(example_limit=5))
            text = output_path.read_text(encoding="utf-8-sig")
            output_exists = output_path.exists()

        self.assertTrue(output_exists)
        self.assertNotEqual(model.semantic_model.status, "EMBEDDED")
        self.assertIn("ИТОГОВЫЙ СТАТУС: PARTIAL", text)
        self.assertNotIn("Секретный регион", text)


class ProfilerTests(unittest.TestCase):
    def test_profiler_keeps_only_top_text_values_and_suppresses_sensitive_fields(self) -> None:
        import pandas as pd

        region = ColumnInfo("Access", "Region", "string", False, 8)
        login = ColumnInfo("Access", "UserLogin", "string", False, 8)
        amount = ColumnInfo("Access", "AmountBand", "int64", False, 3)
        semantic = SemanticModelInfo(
            "EMBEDDED",
            tables=[TableInfo("Access", False, "Import", [region, login, amount])],
        )

        class FakeModel:
            def iter_table(self, *_: object, **__: object):
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
        self.assertFalse(issues)


if __name__ == "__main__":
    unittest.main()
