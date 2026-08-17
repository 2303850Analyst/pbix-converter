from __future__ import annotations

import tempfile
import unittest
import zipfile
import json
from pathlib import Path

import yaml
from docx import Document

from data_compass.pipeline import generate_documentation
from document_core.ooxml import validate_docx


def write_bundle(path: Path, *, dashboards: int = 1) -> None:
    files = {
        "export/metadata.yaml": "version: 1.0.0\ntype: Dashboard\ntimestamp: '2026-01-01T00:00:00Z'\n",
        "export/databases/core.yaml": "database_name: Core\nuuid: db-1\n",
        "export/datasets/core/sales.yaml": """
table_name: sales
schema: mart
uuid: ds-1
database_uuid: db-1
columns:
  - column_name: region
    verbose_name: Регион
    type: VARCHAR
metrics:
  - metric_name: revenue
    verbose_name: Выручка
    expression: SUM(amount)
""",
        "export/charts/1.yaml": """
slice_name: Продажи по регионам
viz_type: bar
uuid: chart-1
dataset_uuid: ds-1
params:
  groupby: [region]
  metrics: [revenue]
""",
    }
    for index in range(dashboards):
        files[f"export/dashboards/dashboard-{index}.yaml"] = f"""
dashboard_title: Обзор продаж {index + 1}
uuid: dashboard-{index + 1}
position:
  ROOT_ID:
    id: ROOT_ID
    type: ROOT
    children: [CHART-1]
  CHART-1:
    id: CHART-1
    type: CHART
    meta:
      chartId: chart-1
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload.strip() + "\n")


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def write_representative_bundle(path: Path) -> None:
    layout = {
        "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["TAB-A", "TAB-B"]},
        "TAB-A": {"id": "TAB-A", "type": "TAB", "children": ["ROW-A"], "meta": {"text": "Обзор"}},
        "ROW-A": {"id": "ROW-A", "type": "ROW", "children": ["CHART-A", "CHART-B", "MD-A"]},
        "CHART-A": {"id": "CHART-A", "type": "CHART", "meta": {"chartId": "chart-a"}},
        "CHART-B": {"id": "CHART-B", "type": "CHART", "meta": {"chartId": "chart-b"}},
        "MD-A": {"id": "MD-A", "type": "MARKDOWN", "meta": {"text": "<b>Методика</b>"}},
        "TAB-B": {"id": "TAB-B", "type": "TAB", "children": ["CHART-C"], "meta": {"text": "Детали"}},
        "CHART-C": {"id": "CHART-C", "type": "CHART", "meta": {"chartId": "chart-c"}},
    }
    native_filter = {
        "id": "native-region",
        "name": "Регион",
        "filterType": "filter_select",
        "targets": [{"datasetUuid": "ds-sales", "column": "region"}],
        "scope": ["TAB-A"],
        "excluded": ["chart-b"],
        "cascadeParentIds": ["native-year"],
        "controlValues": {"operator": "IN", "defaultDataMask": ["Москва", "Урал", "Москва"]},
    }
    files = {
        "bundle/metadata.yaml": {"version": "1.0.0", "type": "Dashboard"},
        "bundle/databases/core.yaml": {"database_name": "Core warehouse", "uuid": "db-core", "backend": "postgresql"},
        "bundle/datasets/core/sales.yaml": {
            "table_name": "sales",
            "schema": "mart",
            "catalog": "dwh",
            "uuid": "ds-sales",
            "database_uuid": "db-core",
            "main_dttm_col": "event_date",
            "sql": "select * from mart.sales",
            "columns": [
                {"column_name": "region", "verbose_name": "Регион", "type": "VARCHAR", "source_column": "region_code", "source_type": "varchar(32)"},
                {"column_name": "margin_pct", "verbose_name": "Маржа", "type": "DECIMAL", "expression": "profit / revenue"},
            ],
            "metrics": [{"metric_name": "revenue", "verbose_name": "Выручка", "expression": "SUM(revenue)"}],
        },
        "bundle/charts/a.yaml": {
            "slice_name": "Выручка",
            "viz_type": "bar",
            "uuid": "chart-a",
            "dataset_uuid": "ds-sales",
            "params": json.dumps({"groupby": ["region"], "metrics": ["revenue"], "adhoc_filters": [{"subject": "region", "operator": "IN", "comparator": ["Москва"]}], "time_range": "Last month"}),
        },
        "bundle/charts/b.yaml": {
            "slice_name": "Выручка",
            "viz_type": "big_number",
            "uuid": "chart-b",
            "dataset_uuid": "ds-sales",
            "params": {"metric": "revenue", "time_range": "This year"},
        },
        "bundle/charts/c.yaml": {
            "slice_name": "Маржа",
            "viz_type": "table",
            "uuid": "chart-c",
            "dataset_uuid": "ds-sales",
            "params": {"all_columns": ["margin_pct"]},
        },
        "bundle/dashboards/main.yaml": {
            "dashboard_title": "Продажи",
            "uuid": "dashboard-main",
            "published": True,
            "position_json": json.dumps(layout, ensure_ascii=False),
            "json_metadata": json.dumps({
                "native_filter_configuration": [native_filter],
                "interactions": [{"source": "chart-a", "type": "cross_filter", "target": "chart-c"}],
            }, ensure_ascii=False),
        },
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


class DataCompassPipelineTests(unittest.TestCase):
    def test_interaction_section_is_conditional_and_drill_action_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "without-interactions.zip"
            write_bundle(source)
            without = root / "without.docx"
            generate_documentation(source, without)
            self.assertNotIn("Переходы и взаимодействия", docx_text(without))

            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            chart_name = next(name for name in files if name.endswith("charts/1.yaml"))
            chart = yaml.safe_load(files[chart_name])
            chart["params"]["drill_to_detail"] = True
            files[chart_name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)
            with_drill = root / "with-drill.docx"
            result = generate_documentation(source, with_drill)
            self.assertEqual(result.status, "OK")
            self.assertIn("drill_to_detail", docx_text(with_drill))

    def test_nested_query_post_processing_affects_same_name_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "post-processing.zip")
            output = Path(directory, "documentation.docx")
            write_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            chart_name = next(name for name in files if name.endswith("charts/1.yaml"))
            chart = yaml.safe_load(files[chart_name])
            chart["query_context"] = {"queries": [{"post_processing": [{"operation": "pivot"}]}]}
            files[chart_name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            second = dict(chart)
            second["uuid"] = "chart-2"
            second["query_context"] = {"queries": [{"post_processing": [{"operation": "rolling"}]}]}
            files["export/charts/2.yaml"] = yaml.safe_dump(second, allow_unicode=True).encode()
            dashboard_name = next(name for name in files if name.endswith("dashboards/dashboard-0.yaml"))
            dashboard = yaml.safe_load(files[dashboard_name])
            dashboard["position"]["ROOT_ID"]["children"].append("CHART-2")
            dashboard["position"]["CHART-2"] = {"type": "CHART", "meta": {"chartId": "chart-2"}}
            files[dashboard_name] = yaml.safe_dump(dashboard, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            text = docx_text(output)
            self.assertIn("РАЗЛИЧИЕ КОНТЕКСТА", text)
            self.assertIn("rolling", text)
            self.assertIn("pivot", text)

    def test_chart_filter_values_are_capped_and_native_exclusion_changes_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "filters.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            chart_name = next(name for name in files if name.endswith("charts/a.yaml"))
            chart = yaml.safe_load(files[chart_name])
            params = json.loads(chart["params"])
            params["adhoc_filters"][0]["comparator"] = [f"value-{index}" for index in range(30)]
            chart["params"] = params
            files[chart_name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            generate_documentation(source, output)

            document = Document(output)
            filter_row = next(
                row
                for table in document.tables
                for row in table.rows
                if row.cells and "Фильтр визуализации 1" in row.cells[0].text
            )
            self.assertEqual(len(filter_row.cells[6].text.split("; ")), 20)
            composition = {
                row.cells[0].text: row
                for table in document.tables
                for row in table.rows
                if row.cells and row.cells[0].text in {"V001", "V002"}
            }
            self.assertIn("фильтр дашборда «Регион» (F01)", composition["V001"].cells[5].text)
            self.assertIn("фильтр визуализации «Фильтр визуализации 1» (F02)", composition["V001"].cells[5].text)
            self.assertIn("временной фильтр «Временной диапазон» (F03)", composition["V001"].cells[5].text)
            self.assertNotIn("фильтр дашборда «Регион»", composition["V002"].cells[5].text)
            self.assertIn("временной фильтр «Временной диапазон» (F04)", composition["V002"].cells[5].text)

            filter_names = {
                row.cells[0].text
                for table in document.tables
                for row in table.rows
                if row.cells and "(F0" in row.cells[0].text
            }
            self.assertIn("фильтр дашборда «Регион» (F01)", filter_names)
            self.assertIn("фильтр визуализации «Фильтр визуализации 1» (F02)", filter_names)

    def test_chart_declared_interaction_uses_uuid_and_unnamed_titles_are_numbered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "unnamed.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            for index, suffix in enumerate(("charts/a.yaml", "charts/b.yaml"), 1):
                name = next(item for item in files if item.endswith(suffix))
                chart = yaml.safe_load(files[name])
                chart["slice_name"] = "0" * (20 + index)
                chart["id"] = 100 + index
                params = json.loads(chart["params"]) if isinstance(chart["params"], str) else chart["params"]
                if index == 1:
                    params["emit_filter"] = True
                chart["params"] = params
                files[name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            text = docx_text(output)
            self.assertIn("Без названия 1", text)
            self.assertIn("Без названия 2", text)
            self.assertIn("cross_filter", text)
            self.assertIn("визуализация «Без названия 1» (V001)", text)

    def test_unresolved_same_name_evidence_has_explicit_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "unresolved.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            for suffix in ("charts/a.yaml", "charts/b.yaml"):
                name = next(item for item in files if item.endswith(suffix))
                chart = yaml.safe_load(files[name])
                chart["dataset_uuid"] = "missing-dataset"
                files[name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            self.assertIn("НЕРАЗРЕШЁННЫЕ ДАННЫЕ", docx_text(output))

    def test_explicit_access_restrictions_render_without_user_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "access.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            name = next(item for item in files if item.endswith("dashboards/main.yaml"))
            dashboard = yaml.safe_load(files[name])
            dashboard["access_restrictions"] = [
                {"type": "role", "name": "Аналитики", "permission": "read", "scope": "dashboard", "users": ["ivanov"]},
                {"type": "user", "name": "private.user@corp.example", "permission": "read", "scope": "dashboard"},
                {"type": "role", "name": "private.user@corp.example", "permission": "read", "scope": "dashboard"},
            ]
            files[name] = yaml.safe_dump(dashboard, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member, payload in files.items():
                    archive.writestr(member, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            text = docx_text(output)
            self.assertIn("Доступы и ограничения", text)
            self.assertIn("Аналитики", text)
            self.assertNotIn("ivanov", text)
            self.assertNotIn("private.user", text)

    def test_dashboard_without_meaningful_content_fails_and_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "empty.zip")
            output = Path(directory, "documentation.docx")
            output.write_bytes(b"previous")
            files = {
                "metadata.yaml": "version: 1.0.0\n",
                "dashboards/main.yaml": "dashboard_title: Empty\nuuid: empty-dashboard\n",
            }
            with zipfile.ZipFile(source, "w") as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            with self.assertRaises(ValueError):
                generate_documentation(source, output)

            self.assertEqual(output.read_bytes(), b"previous")

    def test_equal_adhoc_metric_combines_all_visual_consumers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "adhoc.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            adhoc = {"label": "Динамическая выручка", "sqlExpression": "SUM(revenue)", "aggregate": "SUM"}
            for suffix in ("charts/a.yaml", "charts/b.yaml"):
                name = next(item for item in files if item.endswith(suffix))
                chart = yaml.safe_load(files[name])
                params = json.loads(chart["params"]) if isinstance(chart["params"], str) else chart["params"]
                params.pop("metric", None)
                params["metrics"] = [adhoc]
                chart["params"] = params
                files[name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            generate_documentation(source, output)

            document = Document(output)
            row = next(
                row
                for table in document.tables
                for row in table.rows
                if row.cells and "adhoc_" in row.cells[0].text
            )
            self.assertIn("V001", row.cells[9].text)
            self.assertIn("V002", row.cells[9].text)

    def test_duplicate_chart_identifiers_and_missing_layout_children_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "conflicts.zip")
            output = Path(directory, "documentation.docx")
            write_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            files["export/charts/2.yaml"] = files["export/charts/1.yaml"].replace(
                b"uuid: chart-1", b"uuid: chart-2"
            ) + b"id: 99\n"
            files["export/charts/1.yaml"] += b"id: 99\n"
            dashboard = yaml.safe_load(files["export/dashboards/dashboard-0.yaml"])
            dashboard["position"]["ROOT_ID"]["children"].append("MISSING-NODE")
            files["export/dashboards/dashboard-0.yaml"] = yaml.safe_dump(dashboard).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            self.assertTrue(any(issue.code == "DUPLICATE_CHART_REFERENCE" for issue in result.issues))
            self.assertTrue(any(issue.code == "UNRESOLVED_LAYOUT_CHILD" for issue in result.issues))

    def test_duplicate_native_filter_ids_are_partial_but_local_ids_remain_unique(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "duplicate-filters.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            dashboard_name = next(name for name in files if name.endswith("dashboards/main.yaml"))
            dashboard = yaml.safe_load(files[dashboard_name])
            metadata = json.loads(dashboard["json_metadata"])
            duplicate = dict(metadata["native_filter_configuration"][0])
            duplicate["name"] = "Другой регион"
            duplicate["cascadeParentIds"] = ["native-region"]
            metadata["native_filter_configuration"].append(duplicate)
            dashboard["json_metadata"] = json.dumps(metadata, ensure_ascii=False)
            files[dashboard_name] = yaml.safe_dump(dashboard, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            self.assertTrue(any(issue.code == "DUPLICATE_FILTER_REFERENCE" for issue in result.issues))
            text = docx_text(output)
            self.assertIn("фильтр дашборда «Регион» (F01)", text)
            self.assertIn("фильтр дашборда «Другой регион» (F02)", text)
            self.assertIn("неразрешённая зависимость фильтра «native-region»", text)

    def test_boolean_groupby_is_not_rendered_as_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "boolean.zip")
            output = Path(directory, "documentation.docx")
            write_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            dataset_name = next(name for name in files if name.endswith("datasets/core/sales.yaml"))
            dataset = yaml.safe_load(files[dataset_name])
            dataset["columns"][0]["groupby"] = True
            files[dataset_name] = yaml.safe_dump(dataset, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            generate_documentation(source, output)

            self.assertNotIn(">True<", docx_text(output))

    def test_unknown_semantic_chart_parameter_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "unknown-param.zip")
            output = Path(directory, "documentation.docx")
            write_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            chart_name = next(name for name in files if name.endswith("charts/1.yaml"))
            chart = yaml.safe_load(files[chart_name])
            chart["params"]["corporate_semantic_mode"] = "opaque"
            files[chart_name] = yaml.safe_dump(chart, allow_unicode=True).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            self.assertTrue(any(issue.code == "UNSUPPORTED_CHART_PARAMETER" for issue in result.issues))

    def test_same_name_metrics_and_calculated_columns_compare_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "same-names.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            files["bundle/datasets/core/returns.yaml"] = yaml.safe_dump(
                {
                    "table_name": "returns",
                    "uuid": "ds-returns",
                    "database_uuid": "db-core",
                    "columns": [
                        {"column_name": "margin", "verbose_name": "Маржа", "type": "DECIMAL", "expression": "profit / net"}
                    ],
                    "metrics": [
                        {"metric_name": "revenue_avg", "verbose_name": "Выручка", "expression": "AVG(revenue)"}
                    ],
                },
                allow_unicode=True,
            ).encode()
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            text = docx_text(output)
            self.assertIn("метрика «Выручка»", text)
            self.assertIn("вычисляемое поле «Маржа»", text)
            self.assertIn("РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ", text)

    def test_explicit_tab_and_dashboard_interactions_are_human_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "navigation.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)

            def inject(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                metadata = json.loads(data["json_metadata"])
                metadata["interactions"] = [
                    {"source": "chart-a", "type": "navigate_tab", "target": "TAB-B"},
                    {"source": "chart-b", "type": "navigate_dashboard", "target": "dashboard-main"},
                ]
                data["json_metadata"] = json.dumps(metadata, ensure_ascii=False)
                return yaml.safe_dump(data, allow_unicode=True).encode()

            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            dashboard_name = next(name for name in files if name.endswith("dashboards/main.yaml"))
            files[dashboard_name] = inject(files[dashboard_name])
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            text = docx_text(output)
            self.assertIn("вкладка «Детали»", text)
            self.assertIn("дашборд «Продажи»", text)

    def test_minimal_superset_v1_bundle_becomes_valid_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "documentation.docx")
            write_bundle(source)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            validate_docx(output)
            text = docx_text(output)
            self.assertIn("Об отчёте", text)
            self.assertIn("Обзор продаж 1", text)
            self.assertIn("Продажи по регионам", text)

    def test_multiple_dashboards_fail_without_replacing_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "documentation.docx")
            output.write_bytes(b"previous")
            write_bundle(source, dashboards=2)

            with self.assertRaises(ValueError):
                generate_documentation(source, output)

            self.assertEqual(output.read_bytes(), b"previous")

    def test_representative_bundle_documents_registry_layout_filters_interactions_and_context_differences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "representative.synthetic.zip")
            output = Path(directory, "documentation.docx")
            write_representative_bundle(source)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            text = docx_text(output)
            for expected in (
                "Реестр полей и метрик",
                "Набор данных[Поле/метрика]",
                "Наименование поля в источнике",
                "sales[region]",
                "region_code",
                "varchar(32)",
                "sales[revenue]",
                "Вкладка «Обзор» (T01)",
                "Вкладка «Детали» (T02)",
                "Фильтры уровня дашборда",
                "Все фильтры",
                "Переходы и взаимодействия",
                "cross_filter",
                "Одинаковые названия — возможные различия",
                "РАЗЛИЧИЕ КОНТЕКСТА",
                "Наборы данных и источники",
                "Core warehouse",
            ):
                self.assertIn(expected, text)
            self.assertNotIn("select * from mart.sales", text)
            self.assertNotIn("Закладки", text)
            self.assertNotIn("Power Query", text)


if __name__ == "__main__":
    unittest.main()
