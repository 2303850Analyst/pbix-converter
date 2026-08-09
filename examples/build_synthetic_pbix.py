from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


ROOT = "Report/definition"
OUTPUT = Path(__file__).with_name("synthetic_representative.pbix")
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.9.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
BOOKMARK_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmark/1.4.0/schema.json"


def literal(value: str) -> dict[str, Any]:
    return {"expr": {"Literal": {"Value": f"'{value}'"}}}


def field(kind: str, table: str, name: str) -> dict[str, Any]:
    key = "Property" if kind != "HierarchyLevel" else "Level"
    return {
        kind: {
            "Expression": {"SourceRef": {"Entity": table}},
            key: name,
        }
    }


def aggregation(table: str, name: str, function: int = 0) -> dict[str, Any]:
    return {"Aggregation": {"Expression": field("Column", table, name), "Function": function}}


def projection(query_ref: str, expression: dict[str, Any]) -> dict[str, Any]:
    return {"field": expression, "queryRef": query_ref}


def title_properties(title: str) -> dict[str, Any]:
    return {"title": [{"properties": {"show": literal("true"), "text": literal(title)}}]}


def link_properties(action_type: str, target_property: str, target: str) -> dict[str, Any]:
    return {
        "visualLink": [
            {
                "properties": {
                    "show": literal("true"),
                    "type": literal(action_type),
                    target_property: literal(target),
                }
            }
        ]
    }


def visual(
    name: str,
    title: str,
    visual_type: str,
    position: tuple[int, int, int, int, int],
    roles: dict[str, list[dict[str, Any]]] | None = None,
    link: tuple[str, str, str] | None = None,
    tooltip_page: str | None = None,
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    x, y, z, width, height = position
    objects = title_properties(title)
    if link:
        objects.update(link_properties(*link))
    if tooltip_page:
        objects["visualTooltip"] = [
            {"properties": {"show": literal("true"), "type": literal("ReportPage"), "section": literal(tooltip_page)}}
        ]
    core: dict[str, Any] = {"visualType": visual_type, "visualContainerObjects": objects}
    if roles:
        core["query"] = {
            "queryState": {
                role: {"projections": projections}
                for role, projections in roles.items()
            }
        }
    result: dict[str, Any] = {
        "$schema": VISUAL_SCHEMA,
        "name": name,
        "position": {"x": x, "y": y, "z": z, "width": width, "height": height, "tabOrder": z},
        "visual": core,
    }
    if filters:
        result["filterConfig"] = {"filters": filters}
    return result


def filter_definition(name: str, table: str, column: str, *values: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Categorical",
        "field": field("Column", table, column),
        "filter": {
            "In": {
                "Expressions": [field("Column", table, column)],
                "Values": [[{"Literal": {"Value": f"'{value}'"}}] for value in values],
            }
        },
    }


def page(name: str, display_name: str, *, page_type: str | None = None, hidden: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "$schema": PAGE_SCHEMA,
        "name": name,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "width": 1280,
        "height": 720 if not page_type else 360,
        "visibility": "HiddenInViewMode" if hidden else "AlwaysVisible",
    }
    if page_type:
        result["type"] = page_type
        result["pageBinding"] = {"name": f"binding_{name}", "type": page_type}
    return result


def bookmark(name: str, display_name: str, page_name: str, visual_names: list[str]) -> dict[str, Any]:
    return {
        "$schema": BOOKMARK_SCHEMA,
        "name": name,
        "displayName": display_name,
        "explorationState": {
            "version": "1.3",
            "activeSection": page_name,
            "sections": {
                page_name: {
                    "visualContainers": {visual_name: {} for visual_name in visual_names}
                }
            },
        },
    }


def parts() -> dict[str, Any]:
    year_filter = filter_definition("ReportFilter_Year", "Calendar", "Year", "2025", "2026")
    region_filter = filter_definition("VisualFilter_Region", "Sales", "Region", "Центр", "Север")
    overview_visuals = {
        "card_revenue": visual(
            "card_revenue",
            "Выручка",
            "card",
            (20, 20, 0, 250, 110),
            {"Values": [projection("Sales.Total Revenue", field("Measure", "Sales", "Total Revenue"))]},
        ),
        "chart_regions": visual(
            "chart_regions",
            "Продажи по регионам",
            "clusteredBarChart",
            (20, 150, 1, 610, 390),
            {
                "Category": [projection("Sales.Region", field("Column", "Sales", "Region"))],
                "Y": [projection("Sum(Sales.Amount)", aggregation("Sales", "Amount"))],
                "Tooltips": [projection("Sales.Margin", field("Measure", "Sales", "Margin"))],
            },
            tooltip_page="tooltip_sales",
            filters=[region_filter],
        ),
        "slicer_category": visual(
            "slicer_category",
            "Категория товара",
            "slicer",
            (660, 20, 2, 280, 220),
            {"Values": [projection("Product.Category", field("Column", "Product", "Category"))]},
        ),
        "button_details": visual(
            "button_details",
            "Перейти к деталям",
            "actionButton",
            (970, 20, 3, 260, 60),
            link=("PageNavigation", "navigationSection", "details"),
        ),
        "button_focus": visual(
            "button_focus",
            "Фокус: регионы",
            "actionButton",
            (970, 100, 4, 260, 60),
            link=("Bookmark", "bookmark", "bookmark_regions"),
        ),
        "intro_text": visual("intro_text", "Синтетический демонстрационный отчёт", "textbox", (660, 270, 5, 570, 80)),
    }
    details_visuals = {
        "matrix_sales": visual(
            "matrix_sales",
            "Матрица продаж",
            "pivotTable",
            (20, 20, 0, 850, 500),
            {
                "Rows": [projection("Product.Category", field("Column", "Product", "Category"))],
                "Columns": [projection("Calendar.Year", field("Column", "Calendar", "Year"))],
                "Values": [projection("Sales.Total Revenue", field("Measure", "Sales", "Total Revenue"))],
            },
        ),
        "custom_gauge": visual(
            "custom_gauge",
            "Выполнение плана",
            "syntheticGauge.customVisual",
            (900, 20, 1, 330, 260),
            {"Value": [projection("Sales.Plan Completion", field("Measure", "Sales", "Plan Completion"))]},
        ),
        "button_overview": visual(
            "button_overview",
            "Вернуться к обзору",
            "actionButton",
            (900, 320, 2, 330, 60),
            link=("PageNavigation", "navigationSection", "overview"),
        ),
        "button_reset": visual(
            "button_reset",
            "Сбросить представление",
            "actionButton",
            (900, 400, 3, 330, 60),
            link=("Bookmark", "bookmark", "bookmark_default"),
        ),
    }
    tooltip_visuals = {
        "tooltip_card": visual(
            "tooltip_card",
            "Маржа выбранного региона",
            "card",
            (20, 20, 0, 300, 120),
            {"Values": [projection("Sales.Margin", field("Measure", "Sales", "Margin"))]},
        )
    }

    result: dict[str, Any] = {
        "Report/definition.pbir": {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": "../Synthetic.Dataset"}},
        },
        f"{ROOT}/version.json": {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "4.0.0",
        },
        f"{ROOT}/report.json": {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.1.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU06",
                    "reportVersionAtImport": {"visual": "2.9.0", "page": "2.1.0", "report": "3.1.0"},
                    "type": "SharedResources",
                }
            },
            "filterConfig": {"filters": [year_filter]},
            "annotations": [{"name": "fixture", "value": "synthetic; no embedded semantic model"}],
        },
        f"{ROOT}/pages/pages.json": {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
            "pageOrder": ["overview", "details", "tooltip_sales"],
            "activePageName": "overview",
        },
        f"{ROOT}/pages/overview/page.json": page("overview", "Обзор"),
        f"{ROOT}/pages/details/page.json": page("details", "Детали"),
        f"{ROOT}/pages/tooltip_sales/page.json": page("tooltip_sales", "Tooltip: продажи", page_type="Tooltip", hidden=True),
        f"{ROOT}/bookmarks/bookmarks.json": {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/bookmarksMetadata/1.1.0/schema.json",
            "items": [
                {
                    "name": "view_group",
                    "displayName": "Представления",
                    "children": [
                        {"name": "bookmark_default", "displayName": "Исходный вид"},
                        {"name": "bookmark_regions", "displayName": "Фокус на регионах"},
                    ],
                }
            ],
        },
        f"{ROOT}/bookmarks/bookmark_default.bookmark.json": bookmark(
            "bookmark_default", "Исходный вид", "overview", list(overview_visuals)
        ),
        f"{ROOT}/bookmarks/bookmark_regions.bookmark.json": bookmark(
            "bookmark_regions", "Фокус на регионах", "overview", ["chart_regions", "slicer_category"]
        ),
        "SyntheticFixture/manifest.json": {
            "synthetic": True,
            "purpose": "PBIX documentation converter test fixture",
            "embeddedSemanticModel": False,
        },
    }
    for page_name, visuals in (
        ("overview", overview_visuals),
        ("details", details_visuals),
        ("tooltip_sales", tooltip_visuals),
    ):
        for visual_name, definition in visuals.items():
            result[f"{ROOT}/pages/{page_name}/visuals/{visual_name}/visual.json"] = definition
    return result


def build(output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(parts().items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            archive.writestr(info, payload)
    return output


if __name__ == "__main__":
    print(build())
