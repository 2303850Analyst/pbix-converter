from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "synthetic_data_compass_export.zip"


def build() -> Path:
    layout = {
        "ROOT_ID": {"type": "ROOT", "children": ["TAB-OVERVIEW", "TAB-DETAILS"]},
        "TAB-OVERVIEW": {"type": "TAB", "children": ["ROW-1"], "meta": {"text": "Обзор"}},
        "ROW-1": {"type": "ROW", "children": ["CHART-1", "CHART-2"]},
        "CHART-1": {"type": "CHART", "meta": {"chartId": "chart-regions"}},
        "CHART-2": {"type": "CHART", "meta": {"chartId": "chart-kpi"}},
        "TAB-DETAILS": {"type": "TAB", "children": ["CHART-3"], "meta": {"text": "Детали"}},
        "CHART-3": {"type": "CHART", "meta": {"chartId": "chart-table"}},
    }
    files = {
        "synthetic_data_compass_export/metadata.yaml": {"version": "1.0.0", "type": "Dashboard"},
        "synthetic_data_compass_export/databases/core.yaml": {
            "database_name": "Synthetic warehouse",
            "backend": "postgresql",
            "uuid": "database-core",
        },
        "synthetic_data_compass_export/datasets/core/sales.yaml": {
            "table_name": "sales",
            "schema": "mart",
            "uuid": "dataset-sales",
            "database_uuid": "database-core",
            "main_dttm_col": "event_date",
            "columns": [
                {"column_name": "region", "verbose_name": "Регион", "type": "VARCHAR"},
                {"column_name": "margin_pct", "verbose_name": "Маржа", "type": "DECIMAL", "expression": "profit / revenue"},
            ],
            "metrics": [
                {"metric_name": "revenue", "verbose_name": "Выручка", "expression": "SUM(revenue)"}
            ],
        },
        "synthetic_data_compass_export/charts/regions.yaml": {
            "slice_name": "Выручка",
            "viz_type": "bar",
            "uuid": "chart-regions",
            "dataset_uuid": "dataset-sales",
            "params": {"groupby": ["region"], "metrics": ["revenue"], "time_range": "Last month"},
        },
        "synthetic_data_compass_export/charts/kpi.yaml": {
            "slice_name": "Выручка",
            "viz_type": "big_number",
            "uuid": "chart-kpi",
            "dataset_uuid": "dataset-sales",
            "params": {"metric": "revenue", "time_range": "This year"},
        },
        "synthetic_data_compass_export/charts/table.yaml": {
            "slice_name": "Маржа по регионам",
            "viz_type": "table",
            "uuid": "chart-table",
            "dataset_uuid": "dataset-sales",
            "params": {"all_columns": ["region", "margin_pct"]},
        },
        "synthetic_data_compass_export/dashboards/sales.yaml": {
            "dashboard_title": "Synthetic: продажи",
            "uuid": "dashboard-sales",
            "published": True,
            "position": json.dumps(layout, ensure_ascii=False),
            "metadata": json.dumps(
                {
                    "native_filter_configuration": [
                        {
                            "id": "filter-region",
                            "name": "Регион",
                            "filterType": "filter_select",
                            "targets": [{"datasetUuid": "dataset-sales", "column": "region"}],
                            "scope": ["TAB-OVERVIEW"],
                            "controlValues": {"operator": "IN", "defaultDataMask": ["Центр", "Урал"]},
                        }
                    ],
                    "interactions": [
                        {"source": "chart-regions", "type": "cross_filter", "target": "chart-table"}
                    ],
                },
                ensure_ascii=False,
            ),
        },
    }
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files, key=str.casefold):
            payload = yaml.safe_dump(files[name], allow_unicode=True, sort_keys=False).encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return ARCHIVE


if __name__ == "__main__":
    print(build())
