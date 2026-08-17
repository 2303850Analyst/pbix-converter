from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from .container import CompassBundle, UnsafeBundleError, parse_json_object
from .domain import (
    AccessRestriction,
    Chart,
    Column,
    CompassModel,
    Dashboard,
    DashboardFilter,
    Database,
    Dataset,
    Interaction,
    Issue,
    LayoutItem,
    Metric,
)
from .policy import compact, remove_sensitive_metadata, safe_expression, safe_markdown, safe_url, visible_name


SUPPORTED_VERSION = "1.0.0"
SUPPORTED_VIZ = {
    "bar",
    "big_number",
    "big_number_total",
    "table",
    "pivot_table_v2",
    "line",
    "area",
    "pie",
    "dist_bar",
    "echarts_timeseries_bar",
    "echarts_timeseries_line",
    "mixed_timeseries",
    "filter_box",
}
SUPPORTED_PARAM_KEYS = {
    "adhoc_filters", "all_columns", "annotation_layers", "bottom_margin", "color_scheme", "columns",
    "comparison_type", "datasource", "drill_to_detail", "emit_filter", "enable_drill_to_detail", "filter_configs", "granularity_sqla", "groupby",
    "label_colors", "legend_format", "legend_orientation", "metric", "metrics", "order_by_cols", "order_desc",
    "percent_metrics", "post_processing", "row_limit", "show_legend", "show_value", "slice_id", "sort_by_metric",
    "stack", "time_grain_sqla", "time_range", "timeseries_limit_metric", "url_params", "viz_type", "x_axis",
    "x_axis_format", "x_axis_label", "y_axis_format", "y_axis_label",
}


def parse_bundle(path: Path) -> CompassModel:
    issues: list[Issue] = []
    with CompassBundle(path) as bundle:
        metadata_rows = bundle.yaml_objects("metadata")
        metadata = metadata_rows[0][1]
        version = _text(metadata.get("version"))
        if version != SUPPORTED_VERSION:
            issues.append(Issue("WARNING", "UNKNOWN_EXPORT_VERSION", f"Версия export не подтверждена: {version or '—'}"))
        if bundle.unknown_members:
            issues.append(Issue("WARNING", "UNKNOWN_EXPORT_EXTENSION", "Export содержит неподдерживаемое YAML-расширение"))
        databases = _deduplicate([_database(data) for _, data in bundle.yaml_objects("databases")], "DATABASE", issues)
        datasets = _deduplicate([_dataset(data, issues) for _, data in bundle.yaml_objects("datasets")], "DATASET", issues)
        charts = _deduplicate(
            [_chart(data, issues, index) for index, (_, data) in enumerate(bundle.yaml_objects("charts"), 1)],
            "CHART",
            issues,
        )
        dashboards = [_dashboard(data, issues) for _, data in bundle.yaml_objects("dashboards")]
    if len(dashboards) != 1:
        raise ValueError("Export bundle должен содержать ровно один dashboard")
    dashboard = dashboards[0]
    dashboard.interactions.extend(_declared_chart_interactions(charts))
    dashboard.interactions = _unique_interactions(dashboard.interactions)
    _resolve_chart_reference_conflicts(charts, issues)
    _validate_references(databases, datasets, charts, dashboard, issues)
    if not datasets and not charts and not any(
        item.kind in {"MARKDOWN", "HEADER"} and (item.title or item.text) for item in dashboard.layout.values()
    ):
        raise ValueError("Export bundle не содержит содержимого, пригодного для документации")
    return CompassModel(
        source_path=path.resolve(),
        export_version=version,
        generated_at=datetime.now(timezone.utc),
        databases=databases,
        datasets=datasets,
        charts=charts,
        dashboard=dashboard,
        issues=issues,
    )


def _database(raw: dict[str, Any]) -> Database:
    data = _safe(raw)
    uuid = _required(data, "uuid", "Database")
    return Database(uuid, visible_name(data.get("database_name"), "База данных без названия"), _text(data.get("backend")))


def _dataset(raw: dict[str, Any], issues: list[Issue]) -> Dataset:
    data = _safe(raw)
    uuid = _required(data, "uuid", "Dataset")
    columns: list[Column] = []
    for index, item in enumerate(_dict_list(data.get("columns")), 1):
        name = _text(item.get("column_name")) or f"Без названия {index}"
        expression, changed = safe_expression(item.get("expression"))
        if changed:
            issues.append(Issue("WARNING", "EXPRESSION_REDACTED", f"Выражение поля {name} скрыто или сокращено"))
        columns.append(
            Column(
                name=name,
                verbose_name=_visible_optional(item.get("verbose_name")),
                data_type=_text(item.get("type")),
                expression=None if expression == "—" else expression,
                source_name=_text(item.get("source_column")),
                source_type=_text(item.get("source_type")),
                aggregations=_aggregation_names(item.get("aggregations")),
            )
        )
    metrics: list[Metric] = []
    for index, item in enumerate(_dict_list(data.get("metrics")), 1):
        name = _text(item.get("metric_name")) or f"metric_{index}"
        expression, changed = safe_expression(item.get("expression"))
        if changed:
            issues.append(Issue("WARNING", "EXPRESSION_REDACTED", f"Выражение метрики {name} скрыто или сокращено"))
        metrics.append(
            Metric(
                name,
                visible_name(item.get("verbose_name") or name, f"Без названия {index}"),
                expression,
                aggregation=_text(item.get("aggregation")) or _sql_aggregation(expression),
            )
        )
    return Dataset(
        uuid=uuid,
        database_uuid=_text(data.get("database_uuid")),
        name=visible_name(data.get("table_name"), "Набор данных без названия"),
        schema=_text(data.get("schema")),
        catalog=_text(data.get("catalog")),
        sql=_text(data.get("sql")),
        main_dttm_col=_text(data.get("main_dttm_col")),
        columns=columns,
        metrics=metrics,
    )


def _chart(raw: dict[str, Any], issues: list[Issue], index: int) -> Chart:
    data = _safe(raw)
    uuid = _required(data, "uuid", "Chart")
    params = _jsonish(data.get("params"), f"chart {uuid} params", issues)
    form_data = _jsonish(data.get("form_data"), f"chart {uuid} form_data", issues)
    query_context = _jsonish(data.get("query_context"), f"chart {uuid} query_context", issues)
    merged = {**form_data, **params}
    merged = _sanitize_chart_metadata(merged, issues)
    query_context = _sanitize_chart_metadata(query_context, issues)
    viz_type = _text(data.get("viz_type") or merged.get("viz_type")) or "unknown"
    if viz_type not in SUPPORTED_VIZ:
        issues.append(Issue("WARNING", "UNKNOWN_VIZ_TYPE", f"Неподдержанный тип визуализации: {viz_type}"))
    unsupported = sorted(set(merged) - SUPPORTED_PARAM_KEYS)
    if unsupported:
        issues.append(Issue("WARNING", "UNSUPPORTED_CHART_PARAMETER", "Часть параметров визуализации не интерпретирована"))
    dataset_uuid = _text(data.get("dataset_uuid"))
    if not dataset_uuid:
        datasource = compact(data.get("datasource") or merged.get("datasource"), 200)
        if "__" in datasource:
            dataset_uuid = datasource.split("__", 1)[0]
    return Chart(
        uuid=uuid,
        exported_id=_text(data.get("id") or data.get("slice_id")),
        title=visible_name(data.get("slice_name"), f"Без названия {index}"),
        viz_type=viz_type,
        dataset_uuid=dataset_uuid,
        params=merged,
        query_context=query_context,
    )


def _dashboard(raw: dict[str, Any], issues: list[Issue]) -> Dashboard:
    data = _safe(raw)
    uuid = _required(data, "uuid", "Dashboard")
    position = _jsonish(data.get("position_json") or data.get("position"), "dashboard layout", issues)
    layout: dict[str, LayoutItem] = {}
    for key, raw_item in sorted(position.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_item, dict):
            continue
        meta = raw_item.get("meta") if isinstance(raw_item.get("meta"), dict) else {}
        kind = compact(raw_item.get("type") or meta.get("type"), 100).upper()
        raw_text = _text(meta.get("text"))
        if kind == "MARKDOWN":
            clean_markdown, changed = safe_markdown(raw_text)
            raw_text = None if clean_markdown == "—" else clean_markdown
            if changed:
                issues.append(Issue("WARNING", "MARKDOWN_SANITIZED", f"Текстовый блок {key} очищен или сокращён"))
        layout[str(key)] = LayoutItem(
            key=str(key),
            kind=kind,
            title=_visible_optional(meta.get("text") or meta.get("name") or meta.get("tabText")),
            children=_strings(raw_item.get("children")),
            chart_ref=_text(meta.get("chartId") or meta.get("uuid") or meta.get("sliceId")),
            text=raw_text,
        )
    metadata = _jsonish(data.get("json_metadata") or data.get("metadata"), "dashboard metadata", issues)
    filters = _native_filters(metadata.get("native_filter_configuration"), issues)
    interactions = _interactions(metadata, issues)
    access_restrictions = _access_restrictions(data, metadata, issues)
    root_key = next((key for key, item in layout.items() if item.kind == "ROOT"), None)
    return Dashboard(
        uuid=uuid,
        title=visible_name(data.get("dashboard_title"), "Дашборд без названия"),
        published=_bool_or_none(data.get("published")),
        layout=layout,
        root_key=root_key,
        native_filters=filters,
        interactions=interactions,
        access_restrictions=access_restrictions,
    )


def _native_filters(value: object, issues: list[Issue]) -> list[DashboardFilter]:
    result: list[DashboardFilter] = []
    for index, item in enumerate(_dict_list(value), 1):
        targets = item.get("targets") if isinstance(item.get("targets"), list) else []
        target = targets[0] if targets and isinstance(targets[0], dict) else {}
        control = item.get("controlValues") if isinstance(item.get("controlValues"), dict) else {}
        data_mask = control.get("defaultDataMask") or item.get("defaultDataMask")
        values = _saved_filter_values(data_mask)[:20]
        scope_value = item.get("scope")
        if isinstance(scope_value, dict):
            scope = _strings(scope_value.get("rootPath"))
            excluded = _strings(scope_value.get("excluded"))
        else:
            scope = _strings(scope_value)
            excluded = _strings(item.get("excluded"))
        result.append(
            DashboardFilter(
                key=_text(item.get("id")) or f"filter-{index}",
                name=visible_name(item.get("name"), f"Фильтр без названия {index}"),
                filter_type=_text(item.get("filterType")) or "native",
                dataset_uuid=_text(target.get("datasetUuid") or target.get("datasetId")),
                column=_text(target.get("column") or target.get("column_name")),
                operator=_text(control.get("operator") or item.get("operator")),
                scope=scope,
                excluded=excluded,
                dependencies=_strings(item.get("cascadeParentIds")),
                values=values,
            )
        )
    counts: dict[str, int] = {}
    for item in result:
        counts[item.key] = counts.get(item.key, 0) + 1
    for key in sorted(name for name, count in counts.items() if count > 1):
        issues.append(
            Issue(
                "WARNING",
                "DUPLICATE_FILTER_REFERENCE",
                f"Повторяющийся ID фильтра {key} не разрешён",
            )
        )
    return result


def _interactions(metadata: dict[str, Any], issues: list[Issue]) -> list[Interaction]:
    result: list[Interaction] = []
    declared: list[dict[str, Any]] = []
    for key in ("interactions", "actions", "drill_actions", "navigation", "transitions"):
        declared.extend(_dict_list(metadata.get(key)))
    for item in declared:
        source = _text(item.get("source") or item.get("sourceId"))
        if not source:
            issues.append(Issue("WARNING", "INVALID_INTERACTION", "Взаимодействие без источника пропущено"))
            continue
        target_url, unsafe_url = safe_url(item.get("url"))
        if unsafe_url:
            issues.append(Issue("WARNING", "INTERACTION_URL_SANITIZED", "Параметры или схема URL действия скрыты"))
        result.append(
            Interaction(
                source_ref=source,
                action_type=_text(item.get("type") or item.get("action") or item.get("actionType")) or "interaction",
                target_ref=_text(item.get("target") or item.get("targetId") or item.get("tabId") or item.get("dashboardId")),
                target_url=target_url,
            )
        )
    configuration = metadata.get("chart_configuration")
    if isinstance(configuration, dict):
        for source, raw_config in configuration.items():
            if not isinstance(raw_config, dict):
                continue
            cross = raw_config.get("crossFilters") or raw_config.get("cross_filters")
            if isinstance(cross, dict) and cross.get("enabled", True) is not False:
                targets = _strings(cross.get("chartsInScope") or cross.get("targets"))
                for target in targets or [None]:
                    result.append(Interaction(str(source), "cross_filter", target))
            if raw_config.get("drillToDetail") is True or raw_config.get("drill_to_detail") is True:
                result.append(Interaction(str(source), "drill_to_detail", None))
    return result


def _declared_chart_interactions(charts: list[Chart]) -> list[Interaction]:
    result: list[Interaction] = []
    for chart in charts:
        source = chart.uuid
        if chart.params.get("emit_filter") is True:
            result.append(Interaction(source, "cross_filter", None))
        if chart.params.get("drill_to_detail") is True or chart.params.get("enable_drill_to_detail") is True:
            result.append(Interaction(source, "drill_to_detail", None))
    return result


def _unique_interactions(items: list[Interaction]) -> list[Interaction]:
    result: list[Interaction] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for item in items:
        signature = (item.source_ref, item.action_type, item.target_ref, item.target_url)
        if signature not in seen:
            seen.add(signature)
            result.append(item)
    return result


def _validate_references(
    databases: list[Database], datasets: list[Dataset], charts: list[Chart], dashboard: Dashboard, issues: list[Issue]
) -> None:
    database_ids = {item.uuid for item in databases}
    dataset_ids = {item.uuid for item in datasets}
    chart_ids = {item.uuid for item in charts} | {item.exported_id for item in charts if item.exported_id}
    for dataset in datasets:
        if dataset.database_uuid and dataset.database_uuid not in database_ids:
            issues.append(Issue("WARNING", "UNRESOLVED_DATABASE", f"Не найдена база для набора {dataset.name}"))
    for chart in charts:
        if not chart.dataset_uuid or chart.dataset_uuid not in dataset_ids:
            issues.append(Issue("WARNING", "UNRESOLVED_DATASET", f"Не найден набор данных для визуализации {chart.title}"))
    for item in dashboard.layout.values():
        for child in item.children:
            if child not in dashboard.layout:
                issues.append(Issue("WARNING", "UNRESOLVED_LAYOUT_CHILD", "Layout содержит ссылку на отсутствующий элемент"))
        if item.kind == "CHART" and (not item.chart_ref or item.chart_ref not in chart_ids):
            issues.append(Issue("WARNING", "UNRESOLVED_CHART", f"Не найдена визуализация layout: {item.key}"))
    if dashboard.root_key is None and dashboard.layout:
        issues.append(Issue("WARNING", "UNRESOLVED_LAYOUT_ROOT", "Корневой элемент layout не найден"))
    layout_ids = set(dashboard.layout)
    for interaction in dashboard.interactions:
        valid_sources = chart_ids | layout_ids
        valid_targets = valid_sources | {dashboard.uuid}
        if interaction.source_ref not in valid_sources or (
            interaction.target_ref and interaction.target_ref not in valid_targets
        ):
            issues.append(Issue("WARNING", "UNRESOLVED_INTERACTION", "Не разрешена ссылка взаимодействия"))


def _deduplicate(items: list[Any], label: str, issues: list[Issue]) -> list[Any]:
    groups: dict[str, list[Any]] = {}
    for item in items:
        groups.setdefault(item.uuid, []).append(item)
    result: list[Any] = []
    for group in groups.values():
        if len(group) > 1:
            issues.append(Issue("WARNING", f"DUPLICATE_{label}_UUID", f"Повторяющийся UUID {label.lower()} не разрешён"))
            continue
        result.append(group[0])
    return result


def _access_restrictions(
    data: dict[str, Any], metadata: dict[str, Any], issues: list[Issue]
) -> list[AccessRestriction]:
    rows: list[dict[str, Any]] = []
    for source in (data.get("access_restrictions"), data.get("security_metadata"), metadata.get("access_restrictions"), metadata.get("security_metadata")):
        rows.extend(_dict_list(source))
    result: list[AccessRestriction] = []
    for index, item in enumerate(rows, 1):
        kind = compact(item.get("subject_type") or item.get("entity_type") or item.get("kind") or item.get("type"), 100).casefold()
        if kind not in {"role", "group", "policy", "restriction"}:
            issues.append(Issue("WARNING", "UNSUPPORTED_ACCESS_RESTRICTION", "Индивидуальное или неизвестное назначение доступа не выводится"))
            continue
        name = visible_name(item.get("name") or kind, f"Ограничение {index}")
        permission = _text(item.get("permission") or item.get("access"))
        scope = _text(item.get("scope") or item.get("target") or item.get("resource"))
        if _looks_like_identity(name) or (scope and _looks_like_identity(scope)):
            issues.append(Issue("WARNING", "ACCESS_IDENTITY_OMITTED", "Индивидуальное назначение доступа не выводится"))
            continue
        if permission or scope:
            result.append(AccessRestriction(name, permission, scope))
    return result


def _looks_like_identity(value: str) -> bool:
    return bool(
        re.search(r"(?i)(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b[a-z0-9._-]+\\[a-z0-9._-]+\b)", value)
    )


def _resolve_chart_reference_conflicts(charts: list[Chart], issues: list[Issue]) -> None:
    owners: dict[str, list[Chart]] = {}
    for chart in charts:
        if chart.exported_id:
            owners.setdefault(chart.exported_id, []).append(chart)
    for group in owners.values():
        if len(group) < 2:
            continue
        for chart in group:
            chart.exported_id = None
        issues.append(Issue("WARNING", "DUPLICATE_CHART_REFERENCE", "Повторяющийся ID визуализации не разрешён"))


def _jsonish(value: object, source: str, issues: list[Issue]) -> dict[str, Any]:
    try:
        return _safe(parse_json_object(value, source))
    except UnsafeBundleError:
        issues.append(Issue("WARNING", "MALFORMED_OPTIONAL_METADATA", f"Не удалось разобрать {source}"))
        return {}


def _safe(value: object) -> Any:
    return remove_sensitive_metadata(value)


def _required(data: dict[str, Any], key: str, label: str) -> str:
    value = _text(data.get(key))
    if not value:
        raise ValueError(f"{label} не содержит обязательный {key}")
    return value


def _dict_list(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [compact(item, 500) for item in value if compact(item, 500)]
    if isinstance(value, dict):
        return [compact(item, 500) for item in value.values() if compact(item, 500)]
    text = compact(value, 500)
    return [text] if text else []


def _aggregation_names(value: object) -> list[str]:
    if isinstance(value, bool) or value is None:
        return []
    return [item for item in _strings(value) if item.casefold() not in {"true", "false"}]


def _sanitize_chart_metadata(value: object, issues: list[Issue]) -> Any:
    changed = False

    def visit(item: object, key: str = "") -> object:
        nonlocal changed
        if isinstance(item, dict):
            return {str(child_key): visit(child, str(child_key)) for child_key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, key) for child in item]
        if key.casefold() in {"sqlexpression", "expression", "sql", "jinja"}:
            safe, redacted = safe_expression(item)
            changed = changed or redacted
            return None if safe == "—" else safe
        return item

    result = visit(value)
    if changed:
        issues.append(Issue("WARNING", "CHART_EXPRESSION_REDACTED", "Выражение визуализации скрыто или сокращено"))
    return result


def _sql_aggregation(expression: str) -> str | None:
    match = re.match(r"(?i)^\s*(sum|count|avg|min|max|count_distinct)\s*\(", expression)
    return match.group(1).upper() if match else None


def _saved_filter_values(value: object) -> list[str]:
    candidates: list[object] = []
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        filter_state = value.get("filterState")
        if isinstance(filter_state, dict):
            selected = filter_state.get("value")
            candidates.extend(selected if isinstance(selected, list) else [selected])
        extra = value.get("extraFormData")
        if isinstance(extra, dict):
            for item in extra.get("filters", []) if isinstance(extra.get("filters"), list) else []:
                if isinstance(item, dict):
                    selected = item.get("val")
                    candidates.extend(selected if isinstance(selected, list) else [selected])
    else:
        candidates = [value]
    result: list[str] = []
    for candidate in candidates:
        text = compact(candidate, 500)
        if text and text not in result:
            result.append(text)
    return result


def _text(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = compact(value, 4_000)
    return text or None


def _visible_optional(value: object) -> str | None:
    text = _text(value)
    return visible_name(text, "") or None if text else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
