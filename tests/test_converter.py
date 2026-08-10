from __future__ import annotations

import ast
import codecs
import contextlib
import io
import json
import os
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
    DocumentationModel,
    FileInfo,
    MeasureInfo,
    SemanticModelInfo,
    TableInfo,
)
from pbix_doc.cli import build_parser, main
from pbix_doc.pipeline import Options, generate_documentation, resolve_fields
from pbix_doc.ooxml import UnsafeDocxError, validate_docx
from pbix_doc.projector import HeadingBlock, SafeDocumentModel, TableBlock, project_document, sanitize_dax
from pbix_doc.renderer import atomic_write, render_document
from pbix_doc.report import parse_report
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
    extract_native_types,
    load_semantic_model,
    profile_examples,
    read_model_metadata,
    safe_error_message,
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
    def test_docx_is_user_facing_and_values_are_bounded(self) -> None:
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
                                format_string="Text",
                                source_column="region_code",
                                native_data_type="SQL: varchar(20); M: type text",
                                summarize_by="DoNotSummarize",
                            ),
                            ColumnInfo(
                                "Sales",
                                "Amount",
                                "Decimal",
                                False,
                                1000,
                                example_status="NUMERIC_VALUES_HIDDEN",
                            ),
                            ColumnInfo(
                                "Sales",
                                "UnusedFlag",
                                "Boolean",
                                False,
                                2,
                                example_status="NOT_PROFILED",
                            ),
                        ],
                    )
                ],
                measures=[
                    MeasureInfo(
                        "Sales",
                        "Selected region",
                        'CALCULATE([Revenue], Sales[Region] = "North")',
                        None,
                        None,
                        None,
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

        self.assertIn("Кнопки, закладки и переходы", text)
        self.assertIn("Пользовательские маршруты", text)
        self.assertIn("Одинаковое название — возможные различия", text)
        self.assertIn("РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ", text)
        self.assertIn("Каталог KPI и вычислений", text)
        self.assertIn("Реестр полей отчёта", text)
        self.assertIn("Варианты названий / обозначений в отчёте", text)
        self.assertIn("Наименование из NativeQuery при импорте", text)
        self.assertIn("Sales[Region]", text)
        self.assertIn("region_code", text)
        self.assertIn("SQL: varchar(20); M: type text", text)
        self.assertIn("Sales[Selected region]", text)
        self.assertIn("SUM —", text)
        self.assertIn("<VALUES HIDDEN>", text)
        self.assertNotIn("Секретный регион", text)
        self.assertNotIn("Другой секрет", text)
        self.assertNotIn("Никогда не печатать", text)
        self.assertNotIn(str(path.resolve()), text)
        self.assertIn("Источник", text)
        self.assertIn('Sales[Region] = "<VALUE>"', text)
        self.assertNotIn('"North"', text)
        self.assertEqual(sum(value in text for value in ["Центр", "Север", "Юг", "Запад", "Восток"]), 5)
        document = Document(BytesIO(payload))
        table_headers = [tuple(cell.text for cell in table.rows[0].cells) for table in document.tables]
        filter_index = next(index for index, row in enumerate(table_headers) if row[0] == "Область")
        registry_index = next(index for index, row in enumerate(table_headers) if row[0] == "Таблица[поле]")
        registry = document.tables[registry_index]
        registry_rows = {row.cells[0].text: row for row in registry.rows[1:]}
        self.assertEqual(registry_index, filter_index + 1)
        self.assertEqual(len(registry.columns), 11)
        self.assertTrue(all(row.cells[3].text == "" for row in registry.rows[1:]))
        self.assertIn("Sales[UnusedFlag]", registry_rows)
        self.assertEqual(registry_rows["Sales[UnusedFlag]"].cells[9].text, "—")
        self.assertIn("Sales[Selected region]", registry_rows["Sales[Region]"].cells[8].text)
        self.assertIn("SUM —", registry_rows["Sales[Amount]"].cells[10].text)
        self.assertTrue(any(section.orientation == WD_ORIENT.LANDSCAPE for section in document.sections))
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
        malformed = sanitize_dax('CALCULATE([Revenue], "unfinished)')
        self.assertEqual(safe.status, "OK")
        self.assertIn('"<VALUE>"', safe.text or "")
        self.assertNotIn("secret", safe.text or "")
        self.assertIn("[Revenue]", safe.references)
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
        self.assertIn("ИТОГОВЫЙ СТАТУС: PARTIAL", text)
        self.assertNotIn("Секретный регион", text)

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


class SemanticAdapterTests(unittest.TestCase):
    def test_native_query_names_and_types_are_normalized_without_retaining_query(self) -> None:
        class FakeModel:
            tmschema_tables = [{"Name": "Sales", "IsHidden": False, "StorageMode": "Import"}]
            tmschema_columns = [
                {
                    "TableName": "Sales",
                    "Name": "Amount",
                    "DataType": "Decimal",
                    "FormatString": "#,0.00",
                    "SourceColumn": "amount_net",
                    "SummarizeBy": "Sum",
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
        self.assertEqual(column.summarize_by, "Sum")
        self.assertNotIn("server", repr(semantic))
        self.assertNotIn("SELECT CAST", repr(semantic))
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


if __name__ == "__main__":
    unittest.main()
