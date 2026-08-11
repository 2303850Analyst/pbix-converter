from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath
from typing import Any

from .container import PbixContainer
from .domain import (
    ActionInfo,
    BookmarkInfo,
    FieldRef,
    FilterInfo,
    Issue,
    NavigationEdge,
    PageInfo,
    ReportInfo,
    VisualInfo,
)


VISUAL_CATEGORIES = {
    "card": "KPI",
    "cardvisual": "KPI",
    "kpi": "KPI",
    "slicer": "FILTER_CONTROL",
    "textbox": "STATIC",
    "image": "STATIC",
    "shape": "STATIC",
    "actionbutton": "NAVIGATION",
    "bookmarknavigator": "NAVIGATION",
    "pagenavigator": "NAVIGATION",
}

AGGREGATIONS = {
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

MAX_SAVED_FILTER_VALUES = 20

AGGREGATION_NAMES = {
    "sum": "SUM",
    "average": "AVERAGE",
    "avg": "AVERAGE",
    "count": "COUNT",
    "min": "MIN",
    "max": "MAX",
    "countnonnull": "COUNT_NON_NULL",
    "count_non_null": "COUNT_NON_NULL",
    "median": "MEDIAN",
    "standarddeviation": "STANDARD_DEVIATION",
    "standard_deviation": "STANDARD_DEVIATION",
    "variance": "VARIANCE",
}


def aggregation_name(value: Any) -> str:
    normalized: Any = value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+", text):
            normalized = int(text)
        else:
            named = AGGREGATION_NAMES.get(text.casefold())
            if named:
                return named
            return text or "АГРЕГАЦИЯ НЕ УКАЗАНА"
    elif isinstance(value, float):
        if not math.isfinite(value):
            return "АГРЕГАЦИЯ НЕ УКАЗАНА"
        if value.is_integer():
            normalized = int(value)
    if isinstance(normalized, int) and not isinstance(normalized, bool):
        return AGGREGATIONS.get(normalized, f"НЕИЗВЕСТНАЯ АГРЕГАЦИЯ (код: {normalized})")
    return "АГРЕГАЦИЯ НЕ УКАЗАНА" if normalized is None else str(normalized)


def decode_nested_json(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return decode_nested_json(json.loads(stripped), depth + 1)
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, list):
        return [decode_nested_json(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: decode_nested_json(item, depth + 1) for key, item in value.items()}
    return value


def walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def clean_literal(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        text = text[1:-1].replace("''", "'")
    return text.strip() or None


def named_scalar(node: Any, names: Iterable[str]) -> str | None:
    wanted = {name.casefold() for name in names}
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key.casefold() not in wanted:
                continue
            literal = literal_value(value)
            if literal is not None:
                return clean_literal(literal)
    return None


def literal_value(node: Any) -> Any:
    if isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, dict):
        literal = node.get("Literal") or node.get("literal")
        if isinstance(literal, dict) and "Value" in literal:
            return literal["Value"]
        if "Value" in node and isinstance(node["Value"], (str, int, float, bool)):
            return node["Value"]
        if "value" in node and isinstance(node["value"], (str, int, float, bool)):
            return node["value"]
        for value in node.values():
            result = literal_value(value)
            if result is not None:
                return result
    if isinstance(node, list):
        for value in node:
            result = literal_value(value)
            if result is not None:
                return result
    return None


def get_path(node: Any, *keys: str, default: Any = None) -> Any:
    current = node
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def collect_aliases(node: Any) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        sources = item.get("From") or item.get("from")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            alias = source.get("Name") or source.get("name")
            entity = source.get("Entity") or source.get("entity") or source.get("Table")
            if alias and entity:
                aliases[str(alias)] = str(entity)
    return aliases


def source_name(node: Any) -> str | None:
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        ref = item.get("SourceRef") or item.get("sourceRef")
        if isinstance(ref, dict):
            value = ref.get("Entity") or ref.get("Source") or ref.get("entity") or ref.get("source")
            if value:
                return str(value)
    return None


def field_from_expression(
    expression: Any,
    aliases: dict[str, str],
    role: str | None,
    usage: str,
    source_path: str,
    query_ref: str | None = None,
) -> FieldRef | None:
    if not isinstance(expression, dict):
        return field_from_query_ref(query_ref, role, usage, source_path)

    for key, object_type in (
        ("Column", "COLUMN"),
        ("Measure", "MEASURE"),
        ("Hierarchy", "HIERARCHY"),
        ("HierarchyLevel", "HIERARCHY_LEVEL"),
    ):
        body = expression.get(key)
        if not isinstance(body, dict):
            continue
        name = body.get("Property") or body.get("Level") or body.get("Hierarchy") or body.get("Name")
        table_alias = source_name(body)
        table = aliases.get(str(table_alias), str(table_alias)) if table_alias else None
        if name:
            return FieldRef(table, str(name), object_type, role, usage, None, source_path)

    aggregation = expression.get("Aggregation")
    if isinstance(aggregation, dict):
        nested = aggregation.get("Expression") or aggregation.get("expression") or aggregation
        result = field_from_expression(nested, aliases, role, usage, source_path, query_ref)
        if result:
            function = aggregation.get("Function")
            if function is None:
                function = aggregation.get("function")
            result.aggregation = aggregation_name(function)
            return result

    for value in expression.values():
        result = field_from_expression(value, aliases, role, usage, source_path, query_ref)
        if result:
            return result
    return field_from_query_ref(query_ref, role, usage, source_path)


def field_from_query_ref(
    query_ref: str | None, role: str | None, usage: str, source_path: str
) -> FieldRef | None:
    value = clean_literal(query_ref)
    if not value:
        return None
    match = re.search(r"(?:^|\()([^.[\]()]+)\.([^.[\]()]+)(?:\)|$)", value)
    if match:
        return FieldRef(match.group(1), match.group(2), "UNKNOWN", role, usage, None, source_path)
    return FieldRef(
        None,
        value,
        "UNKNOWN",
        role,
        usage,
        None,
        source_path,
        reason="Ссылка найдена, но её структура не распознана",
    )


def semantic_expressions(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if any(key in node for key in ("Column", "Measure", "Aggregation", "Hierarchy", "HierarchyLevel")):
            yield node
            return
        for value in node.values():
            yield from semantic_expressions(value)
    elif isinstance(node, list):
        for value in node:
            yield from semantic_expressions(value)


def deduplicate_fields(fields: Iterable[FieldRef]) -> list[FieldRef]:
    result: list[FieldRef] = []
    seen: set[tuple[Any, ...]] = set()
    for item in fields:
        key = (
            item.table,
            item.name,
            item.object_type,
            item.role,
            item.usage,
            item.aggregation,
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def extract_projection_fields(node: Any, visual_type: str, source_path: str) -> list[FieldRef]:
    aliases = collect_aliases(node)
    usage_default = "SLICER" if visual_type.casefold() == "slicer" else "VISUAL_DATA"
    fields: list[FieldRef] = []
    query_states: list[dict[str, Any]] = []
    for item in walk(node):
        if isinstance(item, dict) and isinstance(item.get("queryState"), dict):
            query_states.append(item["queryState"])
    for query_state in query_states:
        for role, role_state in query_state.items():
            projections = role_state.get("projections", []) if isinstance(role_state, dict) else []
            for projection in projections:
                if not isinstance(projection, dict):
                    continue
                query_ref = projection.get("queryRef") or projection.get("nativeQueryRef")
                expression = projection.get("field") or projection.get("Field")
                usage = "TOOLTIP" if "tooltip" in role.casefold() else usage_default
                field = field_from_expression(expression, aliases, role, usage, source_path, query_ref)
                if field:
                    fields.append(field)

    select_map: dict[str, Any] = {}
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        selected = item.get("Select") or item.get("select")
        if not isinstance(selected, list):
            continue
        for selection in selected:
            if not isinstance(selection, dict):
                continue
            name = selection.get("Name") or selection.get("name")
            expression = next(
                (selection[key] for key in ("Column", "Measure", "Aggregation", "Hierarchy", "HierarchyLevel") if key in selection),
                selection,
            )
            if name:
                select_map[str(name)] = expression

    for item in walk(node):
        if not isinstance(item, dict) or "projections" not in item:
            continue
        projections = item["projections"]
        if not isinstance(projections, dict):
            continue
        for role, role_items in projections.items():
            if not isinstance(role_items, list):
                continue
            for projection in role_items:
                if not isinstance(projection, dict):
                    continue
                query_ref = projection.get("queryRef") or projection.get("nativeQueryRef")
                expression = select_map.get(str(query_ref)) if query_ref else None
                usage = "TOOLTIP" if "tooltip" in role.casefold() else usage_default
                field = field_from_expression(expression, aliases, role, usage, source_path, query_ref)
                if field:
                    fields.append(field)

    if not fields:
        for expression in semantic_expressions(node):
            field = field_from_expression(expression, aliases, None, usage_default, source_path)
            if field:
                fields.append(field)
    return deduplicate_fields(fields)


def extract_filters(node: Any, scope: str, source_path: str) -> list[FilterInfo]:
    candidates: list[Any] = []
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        config = item.get("filterConfig")
        if isinstance(config, dict) and isinstance(config.get("filters"), list):
            candidates.extend(config["filters"])
        if isinstance(item.get("filters"), list):
            candidates.extend(item["filters"])

    result: list[FilterInfo] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        aliases = collect_aliases(candidate)
        expression = next(semantic_expressions(candidate), None)
        field = field_from_expression(expression, aliases, None, f"{scope}_FILTER", source_path)
        field_name = field.qualified_name if field else "<НЕРАЗРЕШЁННОЕ ПОЛЕ>"
        condition = filter_condition(candidate)
        filter_type = filter_operator(condition) or named_scalar(
            candidate, ("type", "filterType", "howCreated")
        ) or "UNKNOWN"
        key = (scope, field_name, filter_type)
        if key in seen:
            continue
        seen.add(key)
        saved_values, saved_value_count = extract_saved_values(condition)
        result.append(
            FilterInfo(
                scope,
                field_name,
                filter_type,
                saved_value_count,
                source_path,
                saved_values,
            )
        )
    return result


def filter_condition(candidate: Any) -> Any:
    if not isinstance(candidate, dict):
        return candidate
    for key in ("condition", "Condition", "where", "Where", "filterExpression"):
        value = candidate.get(key)
        if isinstance(value, (dict, list)):
            return value
    nested_filter = candidate.get("filter")
    if isinstance(nested_filter, (dict, list)):
        for item in walk(nested_filter):
            if not isinstance(item, dict):
                continue
            for key in ("condition", "Condition", "where", "Where"):
                value = item.get(key)
                if isinstance(value, (dict, list)):
                    return value
        return nested_filter
    return candidate


def filter_operator(condition: Any) -> str | None:
    operators = {
        "in": "IN",
        "notin": "NOT_IN",
        "between": "BETWEEN",
        "contains": "CONTAINS",
        "startswith": "STARTS_WITH",
        "endswith": "ENDS_WITH",
        "greaterthan": "GREATER_THAN",
        "greaterthanorequal": "GREATER_THAN_OR_EQUAL",
        "lessthan": "LESS_THAN",
        "lessthanorequal": "LESS_THAN_OR_EQUAL",
        "equals": "EQUALS",
        "notequals": "NOT_EQUALS",
        "topn": "TOP_N",
    }
    inverse = {
        "IN": "NOT_IN",
        "EQUALS": "NOT_EQUALS",
        "CONTAINS": "NOT_CONTAINS",
    }

    def detect(node: Any) -> str | None:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized = re.sub(r"[^a-z]", "", str(key).casefold())
                if normalized == "not":
                    nested = detect(value)
                    return inverse.get(nested or "", nested)
                if normalized in operators:
                    return operators[normalized]
            for value in node.values():
                nested = detect(value)
                if nested:
                    return nested
        elif isinstance(node, list):
            for value in node:
                nested = detect(value)
                if nested:
                    return nested
        return None

    return detect(condition)


def extract_saved_values(node: Any) -> tuple[list[str], int]:
    values: list[str] = []
    seen: set[str] = set()
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        literal = item.get("Literal") or item.get("literal")
        if not isinstance(literal, dict):
            continue
        raw = literal.get("Value") if "Value" in literal else literal.get("value")
        value = clean_literal(raw)
        if value is None or value in seen:
            continue
        seen.add(value)
        if len(values) < MAX_SAVED_FILTER_VALUES:
            values.append(value)
    return values, len(seen)


def extract_title(node: Any, fallback: str) -> str:
    direct = node.get("title") or node.get("displayName") if isinstance(node, dict) else None
    if (
        isinstance(direct, str)
        and direct.strip()
        and direct.strip().casefold() not in {"true", "false"}
    ):
        return direct.strip()
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key.casefold() != "title":
                continue
            text = named_scalar(value, ("text", "title", "displayName"))
            if text and text.casefold() not in {"true", "false"}:
                return text
    return fallback


def extract_actions(node: Any, source_path: str) -> list[ActionInfo]:
    result: list[ActionInfo] = []
    for item in walk(node):
        explicit = item.get("actions") if isinstance(item, dict) else None
        if not isinstance(explicit, list):
            continue
        for action in explicit:
            if not isinstance(action, dict):
                continue
            result.append(
                ActionInfo(
                    str(action.get("type") or "UNKNOWN").upper(),
                    clean_literal(action.get("target")),
                    source_path,
                )
            )

    action_keys = {"action", "visualLink", "buttonAction", "link"}
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        for key, subtree in item.items():
            if key not in action_keys or not isinstance(subtree, (dict, list)):
                continue
            action_type = named_scalar(subtree, ("type", "actionType"))
            target = named_scalar(
                subtree,
                (
                    "bookmark",
                    "bookmarkName",
                    "page",
                    "pageName",
                    "section",
                    "sectionName",
                    "navigationSection",
                    "drillthroughSection",
                    "destination",
                    "url",
                    "webUrl",
                ),
            )
            if action_type:
                normalized = re.sub(r"[^A-Za-z]", "_", action_type).strip("_").upper()
                aliases = {
                    "BOOKMARK": "BOOKMARK_NAVIGATION",
                    "PAGENAVIGATION": "PAGE_NAVIGATION",
                    "PAGE_NAVIGATION": "PAGE_NAVIGATION",
                    "DRILLTHROUGH": "DRILLTHROUGH",
                    "WEBURL": "WEB_URL",
                }
                result.append(ActionInfo(aliases.get(normalized, normalized), target, source_path))

    for item in walk(node):
        if not isinstance(item, dict):
            continue
        for key, subtree in item.items():
            if "tooltip" not in key.casefold() or not isinstance(subtree, (dict, list)):
                continue
            page = named_scalar(subtree, ("page", "pageName", "reportPage", "section", "sectionName"))
            if page:
                result.append(ActionInfo("REPORT_TOOLTIP", page, source_path))

    unique: list[ActionInfo] = []
    seen: set[tuple[str, str | None]] = set()
    for action in result:
        key = (action.action_type, action.target)
        if key not in seen:
            seen.add(key)
            unique.append(action)
    return unique


def visual_category(technical_type: str) -> str:
    folded = technical_type.casefold()
    if folded in VISUAL_CATEGORIES:
        return VISUAL_CATEGORIES[folded]
    if any(token in folded for token in ("button", "navigator")):
        return "NAVIGATION"
    if any(token in folded for token in ("chart", "table", "matrix", "map", "gauge", "funnel")):
        return "DATA_VISUAL"
    if "." in technical_type:
        return "CUSTOM_VISUAL"
    return "DATA_VISUAL"


def parse_pbir(container: PbixContainer, fallback_title: str, issues: list[Issue]) -> ReportInfo:
    page_files = [
        part
        for part in container.parts
        if re.search(r"(?:^|/)definition/pages/[^/]+/page\.json$", part)
    ]
    pages_metadata_file = next(
        (part for part in container.parts if part.endswith("definition/pages/pages.json")), None
    )
    pages_metadata = container.read_json(pages_metadata_file) if pages_metadata_file else {}
    page_order = pages_metadata.get("pageOrder", []) if isinstance(pages_metadata, dict) else []
    order_index = {str(page_id): index for index, page_id in enumerate(page_order)}

    pages: list[PageInfo] = []
    for fallback_order, page_path in enumerate(sorted(page_files)):
        try:
            page_data = decode_nested_json(container.read_json(page_path))
            folder = PurePosixPath(page_path).parent.name
            page_id = str(page_data.get("name") or folder)
            page_name = str(page_data.get("displayName") or page_id)
            visibility = str(page_data.get("visibility") or "Visible")
            visible = not bool(page_data.get("isHidden")) and visibility.casefold() not in {
                "hidden",
                "hiddeninviewmode",
                "false",
                "1",
            }
            binding = page_data.get("pageBinding") if isinstance(page_data.get("pageBinding"), dict) else {}
            page_type = str(binding.get("type") or page_data.get("type") or "STANDARD").upper()
            page = PageInfo(
                page_id,
                page_name,
                order_index.get(page_id, fallback_order),
                visible,
                page_type,
                number(page_data.get("width")),
                number(page_data.get("height")),
                extract_filters(page_data, "PAGE", page_path),
                source_path=page_path,
            )
            prefix = f"{PurePosixPath(page_path).parent.as_posix()}/visuals/"
            visual_files = [
                part
                for part in container.parts
                if part.startswith(prefix) and part.endswith("/visual.json")
            ]
            for visual_path in visual_files:
                try:
                    visual_data = decode_nested_json(container.read_json(visual_path))
                    visual_core = visual_data.get("visual") if isinstance(visual_data.get("visual"), dict) else {}
                    visual_id = str(visual_data.get("name") or PurePosixPath(visual_path).parent.name)
                    technical_type = str(visual_core.get("visualType") or visual_data.get("visualType") or "unknown")
                    position = visual_data.get("position") if isinstance(visual_data.get("position"), dict) else {}
                    fields = extract_projection_fields(visual_data, technical_type, visual_path)
                    page.visuals.append(
                        VisualInfo(
                            visual_id,
                            extract_title(visual_data, visual_id),
                            technical_type,
                            visual_category(technical_type),
                            number(position.get("x")),
                            number(position.get("y")),
                            number(position.get("z")),
                            number(position.get("width")),
                            number(position.get("height")),
                            not bool(visual_data.get("isHidden")),
                            fields,
                            extract_filters(visual_data, "VISUAL", visual_path),
                            extract_actions(visual_data, visual_path),
                            visual_path,
                        )
                    )
                except Exception as error:
                    issues.append(Issue("WARNING", "REPORT", "VISUAL_PARSE_FAILED", str(error), visual_path, page_id))
            page.visuals.sort(key=lambda item: (item.z is None, item.z or 0, item.id))
            pages.append(page)
        except Exception as error:
            issues.append(Issue("ERROR", "REPORT", "PAGE_PARSE_FAILED", str(error), page_path))
    pages.sort(key=lambda item: (item.order, item.id))

    report_file = next(
        (part for part in container.parts if part.endswith("definition/report.json")), None
    )
    report_data = decode_nested_json(container.read_json(report_file)) if report_file else {}
    version_file = next(
        (part for part in container.parts if part.endswith("definition/version.json")), None
    )
    version_data = container.read_json(version_file) if version_file else {}
    title = str(report_data.get("displayName") or report_data.get("name") or fallback_title)
    bookmarks = parse_pbir_bookmarks(container, pages, issues)
    report = ReportInfo(
        title,
        "PBIR",
        str(version_data.get("version")) if isinstance(version_data, dict) and version_data.get("version") else None,
        pages,
        bookmarks,
        extract_filters(report_data, "REPORT", report_file or "definition/report.json"),
    )
    build_navigation(report, issues)
    return report


def parse_pbir_bookmarks(
    container: PbixContainer, pages: list[PageInfo], issues: list[Issue]
) -> list[BookmarkInfo]:
    page_ids = {page.id.casefold(): page.id for page in pages}
    page_names = {page.name.casefold(): page.id for page in pages}
    bookmark_files = [
        part for part in container.parts if part.endswith(".bookmark.json") and "/definition/bookmarks/" in f"/{part}"
    ]
    metadata_file = next(
        (part for part in container.parts if part.endswith("definition/bookmarks/bookmarks.json")), None
    )
    metadata = decode_nested_json(container.read_json(metadata_file)) if metadata_file else {}
    order_group = bookmark_order_and_groups(metadata)
    result: list[BookmarkInfo] = []
    for fallback_order, path in enumerate(sorted(bookmark_files)):
        try:
            data = decode_nested_json(container.read_json(path))
            bookmark_id = str(data.get("name") or PurePosixPath(path).name.removesuffix(".bookmark.json"))
            display_name = str(data.get("displayName") or bookmark_id)
            exploration = data.get("explorationState") or data.get("state") or data
            page_value = named_scalar(
                exploration,
                ("activeSection", "page", "pageName", "section", "sectionName", "targetPage"),
            )
            page_id = None
            if page_value:
                page_id = page_ids.get(page_value.casefold()) or page_names.get(page_value.casefold()) or page_value
            affected = affected_visuals(exploration)
            order, group = order_group.get(bookmark_id, (fallback_order, None))
            scope = named_scalar(data, ("stateScope", "options", "applyTo")) or "CAPTURED_STATE"
            saved_values, saved_value_count = extract_saved_values(exploration)
            result.append(
                BookmarkInfo(
                    bookmark_id,
                    display_name,
                    group,
                    order,
                    page_id,
                    scope,
                    affected,
                    source_path=path,
                    saved_values=saved_values,
                    saved_value_count=saved_value_count,
                )
            )
        except Exception as error:
            issues.append(Issue("WARNING", "BOOKMARK", "BOOKMARK_PARSE_FAILED", str(error), path))
    result.sort(key=lambda item: (item.order, item.id))
    return result


def bookmark_order_and_groups(node: Any) -> dict[str, tuple[int, str | None]]:
    result: dict[str, tuple[int, str | None]] = {}
    counter = 0

    def visit(items: Any, group: str | None = None) -> None:
        nonlocal counter
        if isinstance(items, dict):
            children = items.get("children") or items.get("items")
            object_name = items.get("name") or items.get("id")
            display_name = items.get("displayName") or object_name
            if isinstance(children, list):
                visit(children, str(display_name) if display_name else group)
            elif object_name:
                result[str(object_name)] = (counter, group)
                counter += 1
        elif isinstance(items, list):
            for item in items:
                visit(item, group)

    if isinstance(node, dict):
        visit(node.get("items") or node.get("bookmarks") or node)
    return result


def affected_visuals(node: Any) -> list[str]:
    result: set[str] = set()
    for item in walk(node):
        if not isinstance(item, dict):
            continue
        containers = item.get("visualContainers")
        if isinstance(containers, dict):
            result.update(str(key) for key in containers)
        elif isinstance(containers, list):
            for container in containers:
                if isinstance(container, dict):
                    name = container.get("name") or container.get("id")
                    if name:
                        result.add(str(name))
    return sorted(result)


def parse_legacy(container: PbixContainer, fallback_title: str, issues: list[Issue]) -> ReportInfo:
    layout_path = container.find_ending("report/layout")[0]
    root = decode_nested_json(container.read_json(layout_path))
    sections = root.get("sections", []) if isinstance(root, dict) else []
    pages: list[PageInfo] = []
    for fallback_order, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        page_id = str(section.get("name") or f"page_{fallback_order + 1}")
        page = PageInfo(
            page_id,
            str(section.get("displayName") or page_id),
            int(section.get("ordinal", fallback_order)),
            str(section.get("visibility", "0")) not in {"1", "Hidden", "hidden"},
            str(section.get("type") or "STANDARD").upper(),
            number(section.get("width")),
            number(section.get("height")),
            extract_filters(section, "PAGE", f"{layout_path}#/sections/{fallback_order}"),
            source_path=f"{layout_path}#/sections/{fallback_order}",
        )
        for index, raw_visual in enumerate(section.get("visualContainers", [])):
            if not isinstance(raw_visual, dict):
                continue
            source_path = f"{layout_path}#/sections/{fallback_order}/visualContainers/{index}"
            try:
                config = decode_nested_json(raw_visual.get("config", {}))
                query = decode_nested_json(raw_visual.get("query", {}))
                filters = decode_nested_json(raw_visual.get("filters", []))
                combined = {"config": config, "query": query, "filters": filters}
                single = config.get("singleVisual", {}) if isinstance(config, dict) else {}
                visual_id = str(config.get("name") or raw_visual.get("name") or f"visual_{index + 1}")
                technical_type = str(single.get("visualType") or config.get("visualType") or "unknown")
                page.visuals.append(
                    VisualInfo(
                        visual_id,
                        extract_title(combined, visual_id),
                        technical_type,
                        visual_category(technical_type),
                        number(raw_visual.get("x")),
                        number(raw_visual.get("y")),
                        number(raw_visual.get("z")),
                        number(raw_visual.get("width")),
                        number(raw_visual.get("height")),
                        not bool(raw_visual.get("isHidden")),
                        extract_projection_fields(combined, technical_type, source_path),
                        extract_filters(combined, "VISUAL", source_path),
                        extract_actions(combined, source_path),
                        source_path,
                    )
                )
            except Exception as error:
                issues.append(Issue("WARNING", "REPORT", "VISUAL_PARSE_FAILED", str(error), source_path, page_id))
        page.visuals.sort(key=lambda item: (item.z is None, item.z or 0, item.id))
        pages.append(page)

    report_config = decode_nested_json(root.get("config", {})) if isinstance(root, dict) else {}
    report = ReportInfo(
        str(root.get("displayName") or root.get("name") or fallback_title),
        "LEGACY",
        str(root.get("version")) if root.get("version") is not None else None,
        sorted(pages, key=lambda item: (item.order, item.id)),
        parse_legacy_bookmarks(root, pages, layout_path, issues),
        extract_filters({"root": root.get("filters", []), "config": report_config}, "REPORT", layout_path),
    )
    build_navigation(report, issues)
    return report


def parse_legacy_bookmarks(
    root: Any, pages: list[PageInfo], source_path: str, issues: list[Issue]
) -> list[BookmarkInfo]:
    page_ids = {page.id.casefold(): page.id for page in pages}
    result: list[BookmarkInfo] = []
    bookmark_lists: list[list[Any]] = []
    for item in walk(root):
        if isinstance(item, dict) and isinstance(item.get("bookmarks"), list):
            bookmark_lists.append(item["bookmarks"])
    seen: set[str] = set()
    for bookmarks in bookmark_lists:
        for data in bookmarks:
            if not isinstance(data, dict):
                continue
            bookmark_id = str(data.get("name") or data.get("id") or "")
            if not bookmark_id or bookmark_id in seen:
                continue
            seen.add(bookmark_id)
            exploration = data.get("explorationState") or data
            page_value = named_scalar(exploration, ("activeSection", "section", "page"))
            page_id = page_ids.get(page_value.casefold(), page_value) if page_value else None
            saved_values, saved_value_count = extract_saved_values(exploration)
            result.append(
                BookmarkInfo(
                    bookmark_id,
                    str(data.get("displayName") or bookmark_id),
                    clean_literal(data.get("group")),
                    len(result),
                    page_id,
                    "CAPTURED_STATE",
                    affected_visuals(exploration),
                    source_path=source_path,
                    saved_values=saved_values,
                    saved_value_count=saved_value_count,
                )
            )
    return result


def build_navigation(report: ReportInfo, issues: list[Issue]) -> None:
    page_by_key: dict[str, PageInfo] = {}
    for page in report.pages:
        page_by_key[page.id.casefold()] = page
        page_by_key[page.name.casefold()] = page
    bookmark_by_key: dict[str, BookmarkInfo] = {}
    for bookmark in report.bookmarks:
        bookmark_by_key[bookmark.id.casefold()] = bookmark
        bookmark_by_key[bookmark.name.casefold()] = bookmark
        if bookmark.associated_page_id:
            page = page_by_key.get(bookmark.associated_page_id.casefold())
            if page:
                bookmark.associated_page_id = page.id

    edges: list[NavigationEdge] = []
    for page in report.pages:
        for visual in page.visuals:
            for action in visual.actions:
                target = clean_literal(action.target)
                target_page: PageInfo | None = None
                target_name: str | None = target
                target_id: str | None = target
                target_kind = "UNKNOWN"
                status = "UNRESOLVED"
                folded_type = action.action_type.casefold()
                if "bookmark" in folded_type and target:
                    target_kind = "BOOKMARK"
                    bookmark = bookmark_by_key.get(target.casefold())
                    if bookmark:
                        target_id = bookmark.id
                        target_name = bookmark.name
                        target_page = page_by_key.get((bookmark.associated_page_id or "").casefold())
                        bookmark.trigger_refs.append(f"{page.name} / {visual.title}")
                        status = "RESOLVED" if target_page else "PARTIAL"
                elif "page" in folded_type or "tooltip" in folded_type or "drill" in folded_type:
                    target_kind = "PAGE"
                    target_page = page_by_key.get(target.casefold()) if target else None
                    if target_page:
                        target_id = target_page.id
                        target_name = target_page.name
                        status = "RESOLVED"
                elif "url" in folded_type:
                    target_kind = "EXTERNAL"
                    target_name = safe_external_target(target)
                    target_id = None
                    status = "RESOLVED"
                elif "back" in folded_type:
                    target_kind = "DYNAMIC_BACK"
                    target_name = "Предыдущая страница"
                    target_id = None
                    status = "DYNAMIC"

                if status == "UNRESOLVED":
                    issues.append(
                        Issue(
                            "WARNING",
                            "NAVIGATION",
                            "NAVIGATION_TARGET_UNRESOLVED",
                            f"Не удалось разрешить цель действия {action.action_type}",
                            action.source_path,
                            page.id,
                            visual.id,
                        )
                    )
                edges.append(
                    NavigationEdge(
                        page.id,
                        page.name,
                        visual.id,
                        visual.title,
                        action.action_type,
                        target_kind,
                        target_id,
                        target_name,
                        target_page.id if target_page else None,
                        target_page.name if target_page else None,
                        status,
                    )
                )
    report.navigation = edges


def safe_external_target(target: str | None) -> str:
    value = clean_literal(target)
    if not value:
        return "Адрес не определён"
    match = re.match(r"^(https?)://([^/?#]+)([^?#]*)", value, re.IGNORECASE)
    if not match:
        return "Адрес определяется динамически"
    authority = match.group(2).rsplit("@", 1)[-1]
    return f"{match.group(1).casefold()}://{authority}{match.group(3)}"


def number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_report(
    container: PbixContainer, fallback_title: str, issues: list[Issue]
) -> ReportInfo:
    pbir_pages = [
        part for part in container.parts if re.search(r"(?:^|/)definition/pages/[^/]+/page\.json$", part)
    ]
    if pbir_pages:
        return parse_pbir(container, fallback_title, issues)
    if container.find_ending("report/layout"):
        return parse_legacy(container, fallback_title, issues)
    issues.append(
        Issue(
            "ERROR",
            "REPORT",
            "UNKNOWN_REPORT_FORMAT",
            "В контейнере не найдены PBIR definition или Legacy Report/Layout",
        )
    )
    return ReportInfo(fallback_title, "UNKNOWN", None)
