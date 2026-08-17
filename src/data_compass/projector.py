from __future__ import annotations

from collections import defaultdict
import json
import re

from document_core.model import HeadingBlock, ParagraphBlock, SafeDocumentModel, TableBlock

from .domain import Chart, CompassModel, DashboardFilter, Dataset, LayoutItem, Metric
from .policy import compact, safe_expression, safe_markdown, safe_url, validate_safe_texts, visible_name


LANDSCAPE = 14_400
DASH = "—"


def project_document(model: CompassModel) -> SafeDocumentModel:
    blocks = [
        HeadingBlock(1, "Об отчёте"),
        ParagraphBlock(
            "Документ описывает локально экспортированную конфигурацию Data Compass. "
            "SQL, Jinja и значения показателей не выполняются и не вычисляются.",
            "lead",
        ),
        TableBlock(
            ("Параметр", "Значение"),
            [
                ("Файл", model.source_path.name),
                ("Дашборд", model.dashboard.title),
                ("Формат", f"Data Compass / Superset {model.export_version or DASH}"),
                ("Полнота извлечения", _status_label(model.status)),
                ("Наборов данных", str(len(model.datasets))),
                ("Визуализаций", str(len(model.charts))),
            ],
            (4_000, 10_400),
        ),
    ]
    blocks.extend(_dashboard_filters(model))
    blocks.extend(_registry(model))
    blocks.extend(_composition(model))
    blocks.extend(_all_filters(model))
    blocks.extend(_interactions(model))
    blocks.extend(_same_names(model))
    blocks.extend(_sources(model))
    blocks.extend(_access_restrictions(model))
    safe = SafeDocumentModel(
        title=model.dashboard.title,
        subtitle="Документация дашборда Data Compass",
        source_name=model.source_path.name,
        generated_at=model.generated_at,
        overall_status=model.status,
        blocks=blocks,
        product_name="Data Compass Converter",
        product_subject="Локальная документация Data Compass для Confluence",
    )
    validate_safe_texts(_all_text(safe))
    return safe


def _composition(model: CompassModel) -> list[object]:
    dashboard = model.dashboard
    chart_index: dict[str, Chart] = {}
    for chart in model.charts:
        chart_index[chart.uuid] = chart
        if chart.exported_id:
            chart_index[chart.exported_id] = chart
    groups = _layout_groups(dashboard.layout, dashboard.root_key)
    if not groups:
        groups = [("Основной экран", list(dashboard.layout))]
    blocks: list[object] = [HeadingBlock(1, "Состав дашборда")]
    visual_number = 1
    unnamed: defaultdict[str, int] = defaultdict(int)
    interaction_by_source = defaultdict(list)
    for interaction in dashboard.interactions:
        source_chart = chart_index.get(interaction.source_ref)
        interaction_by_source[source_chart.uuid if source_chart else interaction.source_ref].append(interaction)
    filter_by_chart = _filters_by_chart(model)
    visual_ids = _visual_ids(model)
    layout_ids = _layout_ids(model)
    for group_index, (group_name, keys) in enumerate(groups, 1):
        heading = f"Вкладка «{group_name}» (T{group_index:02d})" if any(
            item.kind == "TAB" for item in dashboard.layout.values()
        ) else group_name
        blocks.append(HeadingBlock(2, heading))
        rows: list[tuple[str, ...]] = []
        for key in keys:
            item = dashboard.layout.get(key)
            if not item or item.kind in {"ROOT", "ROW", "COLUMN", "TAB"}:
                continue
            chart = chart_index.get(item.chart_ref or "") if item.kind == "CHART" else None
            if chart:
                name = chart.title
                kind = _viz_label(chart.viz_type)
                fields = _chart_fields(chart)
                calculation = _chart_calculation(chart)
                context = "; ".join(filter_by_chart.get(chart.uuid, [])) or DASH
                interactions = interaction_by_source.get(chart.uuid, [])
                interaction_text = "; ".join(item.action_type for item in interactions) or DASH
                target_text = "; ".join(
                    _interaction_target(item, chart_index, dashboard, visual_ids, layout_ids)
                    for item in interactions
                ) or DASH
            else:
                unnamed[item.kind] += 1
                name = item.title or visible_name(item.text, f"Без названия {unnamed[item.kind]}")
                kind = _layout_label(item.kind)
                if item.kind == "MARKDOWN":
                    name, _ = safe_markdown(item.text)
                fields = calculation = context = interaction_text = target_text = DASH
            rows.append(
                (f"V{visual_number:03d}", name, kind, fields, calculation, context, interaction_text, target_text)
            )
            visual_number += 1
        if rows:
            blocks.append(
                TableBlock(
                    (
                        "Элемент",
                        "Название",
                        "Тип и назначение",
                        "Поля и метрики",
                        "Расчёт/SQL",
                        "Контекст и фильтры",
                        "Взаимодействия",
                        "Действие/цель",
                    ),
                    rows,
                    (850, 2_150, 1_550, 2_250, 2_150, 2_250, 1_600, 1_600),
                    font_size=7.2,
                )
            )
    return blocks


def _dashboard_filters(model: CompassModel) -> list[object]:
    if not model.dashboard.native_filters:
        return []
    filter_ids = _filter_ids(model)
    rows = [
        (
            _filter_reference("фильтр дашборда", item.name, filter_ids[("native", item.key, index)]),
            _filter_field(item, model),
            item.filter_type,
            item.operator or DASH,
            _filter_scope(item, model),
            _named_dependencies(item, model),
        )
        for index, item in enumerate(model.dashboard.native_filters, 1)
    ]
    return [
        HeadingBlock(1, "Фильтры уровня дашборда"),
        TableBlock(
            ("Фильтр", "Поле", "Тип", "Оператор", "Область действия", "Зависимости"),
            rows,
            (2_200, 2_500, 1_800, 1_500, 3_400, 3_000),
        ),
    ]


def _registry(model: CompassModel) -> list[object]:
    chart_tabs = _chart_tab_references(model)
    visual_ids = _visual_ids(model)
    chart_fields = {chart.uuid: _field_tokens(chart) for chart in model.charts}
    rows: list[tuple[str, ...]] = []
    seen_adhoc: set[tuple[str, str]] = set()
    for dataset in sorted(model.datasets, key=lambda item: item.name.casefold()):
        for column in sorted(dataset.columns, key=lambda item: item.name.casefold()):
            consumers = [chart for chart in model.charts if column.name in chart_fields[chart.uuid]]
            metric_users = [metric.label for metric in dataset.metrics if _expression_mentions(metric.expression, column.name)]
            rows.append(
                (
                    f"{dataset.name}[{column.name}]",
                    column.verbose_name or DASH,
                    column.data_type or DASH,
                    "",
                    column.expression or DASH,
                    _tabs_for(consumers, chart_tabs),
                    column.source_name or DASH,
                    column.source_type or DASH,
                    "; ".join(metric_users) or DASH,
                    _charts_text(consumers, visual_ids),
                    ", ".join(column.aggregations) or _aggregations_for(column.name, consumers),
                )
            )
        for metric in sorted(dataset.metrics, key=lambda item: item.label.casefold()):
            consumers = [chart for chart in model.charts if metric.name in chart_fields[chart.uuid] or metric.label in chart_fields[chart.uuid]]
            rows.append(_metric_row(dataset, metric, consumers, chart_tabs, visual_ids))
        adhoc: dict[tuple[str, str, str], tuple[Metric, list[Chart]]] = {}
        for chart in model.charts:
            if chart.dataset_uuid != dataset.uuid:
                continue
            for metric in _adhoc_metrics(chart):
                signature = (
                    dataset.uuid,
                    (metric.expression or metric.label).casefold(),
                    (metric.aggregation or "").casefold(),
                )
                if signature in seen_adhoc:
                    adhoc[signature][1].append(chart)
                    continue
                seen_adhoc.add(signature)
                adhoc[signature] = (metric, [chart])
        for metric, consumers in adhoc.values():
            rows.append(_metric_row(dataset, metric, consumers, chart_tabs, visual_ids))
    return [
        HeadingBlock(1, "Реестр полей и метрик"),
        TableBlock(
            (
                "Набор данных[Поле/метрика]",
                "Варианты названий",
                "Тип данных в Data Compass",
                "Объяснение",
                "SQL/выражение",
                "Упоминается на вкладке",
                "Наименование поля в источнике",
                "Тип данных в источнике",
                "Используется в метриках",
                "Используется в визуализациях",
                "Варианты агрегаций",
            ),
            rows,
            (1_700, 1_300, 1_000, 700, 2_000, 1_200, 1_400, 1_000, 1_300, 1_600, 1_200),
            font_size=6.6,
        ),
    ]


def _metric_row(
    dataset: Dataset,
    metric: Metric,
    consumers: list[Chart],
    chart_tabs: dict[str, str],
    visual_ids: dict[str, str],
) -> tuple[str, ...]:
    return (
        f"{dataset.name}[{metric.name}]",
        metric.label if metric.label != metric.name else DASH,
        "Метрика" if metric.metric_type == "saved" else "Ad-hoc метрика",
        "",
        metric.expression or DASH,
        _tabs_for(consumers, chart_tabs),
        DASH,
        DASH,
        DASH,
        _charts_text(consumers, visual_ids),
        metric.aggregation or _aggregations_for(metric.name, consumers),
    )


def _all_filters(model: CompassModel) -> list[object]:
    rows: list[tuple[str, ...]] = []
    filter_ids = _filter_ids(model)
    for index, item in enumerate(model.dashboard.native_filters, 1):
        rows.append(
            (
                _filter_reference("фильтр дашборда", item.name, filter_ids[("native", item.key, index)]),
                "Фильтр дашборда",
                _filter_field(item, model),
                item.operator or item.filter_type,
                _filter_scope(item, model),
                _named_dependencies(item, model),
                "; ".join(dict.fromkeys(item.values[:20])) or DASH,
            )
        )
    for chart in model.charts:
        for row in _chart_filters(chart, model, filter_ids):
            rows.append(row)
    if not rows:
        return []
    return [
        HeadingBlock(1, "Все фильтры"),
        TableBlock(
            ("Фильтр", "Уровень", "Поле", "Оператор/тип", "Область действия", "Зависимости", "Сохранённые значения"),
            rows,
            (2_000, 1_700, 2_000, 1_700, 2_700, 2_100, 2_200),
            font_size=7.2,
        ),
    ]


def _interactions(model: CompassModel) -> list[object]:
    if not model.dashboard.interactions:
        return []
    chart_index: dict[str, Chart] = {}
    for chart in model.charts:
        chart_index[chart.uuid] = chart
        if chart.exported_id:
            chart_index[chart.exported_id] = chart
    visual_ids = _visual_ids(model)
    layout_ids = _layout_ids(model)
    rows: list[tuple[str, ...]] = []
    for item in model.dashboard.interactions:
        source = chart_index.get(item.source_ref)
        source_layout = model.dashboard.layout.get(item.source_ref)
        source_text = (
            _chart_reference(source, visual_ids)
            if source
            else _layout_reference(source_layout, layout_ids.get(item.source_ref)) if source_layout else f"неразрешённый источник {item.source_ref}"
        )
        url, unsafe = safe_url(item.target_url)
        target = _interaction_target(item, chart_index, model.dashboard, visual_ids, layout_ids)
        if url:
            target = url
        if unsafe:
            target = f"{target}; параметры URL скрыты" if target != DASH else "URL скрыт"
        rows.append((source_text, item.action_type, target))
    return [
        HeadingBlock(1, "Переходы и взаимодействия"),
        TableBlock(("Источник", "Действие", "Цель"), rows, (4_600, 3_000, 6_800)),
    ]


def _same_names(model: CompassModel) -> list[object]:
    groups: defaultdict[tuple[str, str], list[tuple[str, str, str, str, bool]]] = defaultdict(list)
    chart_tab_references = _chart_tab_references(model)
    visual_ids = _visual_ids(model)
    filters = _filters_by_chart(model)
    for chart in model.charts:
        location = (
            f"{chart_tab_references.get(chart.uuid, 'Основной экран')}; "
            f"визуализация «{chart.title}» ({visual_ids.get(chart.uuid, '—')})"
        )
        definition, resolved = _chart_definition_signature(chart, model)
        groups[("visual", chart.title.casefold())].append(
            (
                f"визуализация «{chart.title}»",
                location,
                definition,
                _chart_context_signature(chart, filters.get(chart.uuid, [])),
                resolved,
            )
        )
    for dataset in model.datasets:
        for metric in dataset.metrics:
            consumers = [chart for chart in model.charts if chart.dataset_uuid == dataset.uuid and (
                metric.name in _field_tokens(chart) or metric.label in _field_tokens(chart)
            )]
            groups[("metric", metric.label.casefold())].append(
                (
                    f"метрика «{metric.label}»",
                    f"набор данных «{dataset.name}»; {_charts_text(consumers, visual_ids)}",
                    f"dataset={dataset.name}; expression={metric.expression or DASH}; aggregation={metric.aggregation or DASH}",
                    _consumer_context(consumers, filters),
                    bool(metric.expression),
                )
            )
        for column in dataset.columns:
            if not column.expression:
                continue
            label = column.verbose_name or column.name
            consumers = [chart for chart in model.charts if chart.dataset_uuid == dataset.uuid and column.name in _field_tokens(chart)]
            groups[("column", label.casefold())].append(
                (
                    f"вычисляемое поле «{label}»",
                    f"набор данных «{dataset.name}»; {_charts_text(consumers, visual_ids)}",
                    f"dataset={dataset.name}; expression={column.expression}",
                    _consumer_context(consumers, filters),
                    True,
                )
            )
    rows: list[tuple[str, ...]] = []
    for occurrences in groups.values():
        if len(occurrences) < 2:
            continue
        definitions = {item[2] for item in occurrences}
        contexts = {item[3] for item in occurrences}
        if not all(item[4] for item in occurrences):
            classification = "НЕРАЗРЕШЁННЫЕ ДАННЫЕ"
        elif len(definitions) > 1:
            classification = "РАЗЛИЧИЕ ОПРЕДЕЛЕНИЙ"
        elif len(contexts) > 1:
            classification = "РАЗЛИЧИЕ КОНТЕКСТА"
        else:
            classification = "ОДИНАКОВОЕ ОПРЕДЕЛЕНИЕ"
        for label, location, definition, context, _ in occurrences:
            rows.append(
                (
                    label,
                    classification,
                    location,
                    definition,
                    context,
                )
            )
    if not rows:
        return []
    return [
        HeadingBlock(1, "Одинаковые названия — возможные различия"),
        ParagraphBlock(
            "Сравнивается экспортированная методология и контекст. Раздел не утверждает различие фактических значений.",
            "note",
        ),
        TableBlock(
            ("Название", "Классификация", "Расположение", "Определение", "Контекст"),
            rows,
            (2_300, 2_600, 3_300, 3_100, 3_100),
            font_size=7.4,
        ),
    ]


def _sources(model: CompassModel) -> list[object]:
    databases = {item.uuid: item for item in model.databases}
    rows = []
    for dataset in sorted(model.datasets, key=lambda item: item.name.casefold()):
        database = databases.get(dataset.database_uuid or "")
        consumers = [chart for chart in model.charts if chart.dataset_uuid == dataset.uuid]
        rows.append(
            (
                database.name if database else DASH,
                dataset.name,
                "Виртуальный" if dataset.sql else "Физический",
                dataset.catalog or DASH,
                dataset.schema or DASH,
                dataset.name,
                dataset.main_dttm_col or DASH,
                str(len(dataset.columns)),
                str(len(dataset.metrics)),
                _charts_text(consumers, _visual_ids(model)),
                "Да" if dataset.sql else "Нет",
            )
        )
    if not rows:
        return []
    return [
        HeadingBlock(1, "Наборы данных и источники"),
        TableBlock(
            ("База данных", "Набор данных", "Тип", "Каталог", "Схема", "Таблица", "Поле времени", "Полей", "Метрик", "Визуализации", "Virtual SQL"),
            rows,
            (1_500, 1_500, 900, 900, 900, 1_200, 1_200, 650, 650, 3_000, 2_000),
            font_size=7,
        ),
    ]


def _layout_groups(layout: dict[str, LayoutItem], root_key: str | None) -> list[tuple[str, list[str]]]:
    root = layout.get(root_key) if root_key else None
    ordered_keys = root.children if root else []
    tab_keys = [key for key in ordered_keys if key in layout and layout[key].kind == "TAB"]
    tab_keys.extend(
        key
        for key, item in sorted(layout.items(), key=lambda pair: pair[0])
        if item.kind == "TAB" and key not in tab_keys
    )
    tabs = [layout[key] for key in tab_keys]
    if tabs:
        result: list[tuple[str, list[str]]] = []
        for index, tab in enumerate(tabs, 1):
            result.append((tab.title or f"Вкладка без названия {index}", _descendants(layout, tab.children)))
        return result
    roots = layout.get(root_key) if root_key else None
    return [("Основной экран", _descendants(layout, roots.children if roots else list(layout)))]


def _chart_tabs(model: CompassModel) -> dict[str, str]:
    result: dict[str, str] = {}
    chart_index: dict[str, Chart] = {}
    for chart in model.charts:
        chart_index[chart.uuid] = chart
        if chart.exported_id:
            chart_index[chart.exported_id] = chart
    for group_name, keys in _layout_groups(model.dashboard.layout, model.dashboard.root_key):
        for key in keys:
            item = model.dashboard.layout.get(key)
            chart = chart_index.get(item.chart_ref or "") if item else None
            if chart:
                result[chart.uuid] = group_name
    return result


def _chart_tab_references(model: CompassModel) -> dict[str, str]:
    layout_ids = _layout_ids(model)
    chart_lookup = _chart_lookup(model)
    result: dict[str, str] = {}
    tabs = [item for item in model.dashboard.layout.values() if item.kind == "TAB"]
    for tab in tabs:
        reference = _layout_reference(tab, layout_ids.get(tab.key))
        for key in _descendants(model.dashboard.layout, tab.children):
            item = model.dashboard.layout.get(key)
            chart = chart_lookup.get(item.chart_ref or "") if item and item.kind == "CHART" else None
            if chart:
                result[chart.uuid] = reference
    for chart in model.charts:
        result.setdefault(chart.uuid, "Основной экран")
    return result


def _field_tokens(chart: Chart) -> set[str]:
    result: set[str] = set()
    for key in ("groupby", "columns", "all_columns", "metrics", "metric"):
        value = chart.params.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str):
                result.add(item)
            elif isinstance(item, dict):
                for candidate in ("label", "column_name", "sqlExpression", "expression"):
                    text = compact(item.get(candidate), 4_000)
                    if text:
                        result.add(text)
    return result


def _adhoc_metrics(chart: Chart) -> list[Metric]:
    result: list[Metric] = []
    values = chart.params.get("metrics") or chart.params.get("metric")
    candidates = values if isinstance(values, list) else [values]
    for index, item in enumerate(candidates, 1):
        if not isinstance(item, dict):
            continue
        expression, _ = safe_expression(item.get("sqlExpression") or item.get("expression"))
        label = visible_name(item.get("label"), f"Ad-hoc метрика {index}")
        aggregation = compact(item.get("aggregate") or item.get("aggregation"), 100) or None
        result.append(
            Metric(
                name=f"adhoc_{chart.uuid}_{index}",
                label=label,
                expression=None if expression == DASH else expression,
                metric_type="adhoc",
                aggregation=aggregation,
            )
        )
    return result


def _tabs_for(charts: list[Chart], chart_tabs: dict[str, str]) -> str:
    return "; ".join(dict.fromkeys(chart_tabs.get(chart.uuid, "Основной экран") for chart in charts)) or DASH


def _charts_text(charts: list[Chart], visual_ids: dict[str, str] | None = None) -> str:
    visual_ids = visual_ids or {}
    result: list[str] = []
    seen: set[str] = set()
    for chart in charts:
        if chart.uuid in seen:
            continue
        seen.add(chart.uuid)
        result.append(
            f"визуализация «{chart.title}»"
            + (f" ({visual_ids[chart.uuid]})" if chart.uuid in visual_ids else "")
        )
    return "; ".join(result) or DASH


def _aggregations_for(field: str, charts: list[Chart]) -> str:
    values: list[str] = []
    for chart in charts:
        for metric in _adhoc_metrics(chart):
            if field.casefold() in (metric.expression or "").casefold() and metric.aggregation:
                values.append(metric.aggregation)
    return ", ".join(dict.fromkeys(values)) or "по умолчанию"


def _filter_field(item: DashboardFilter, model: CompassModel) -> str:
    dataset = next((dataset for dataset in model.datasets if dataset.uuid == item.dataset_uuid), None)
    if dataset and item.column:
        return f"{dataset.name}[{item.column}]"
    return item.column or DASH


def _filter_scope(item: DashboardFilter, model: CompassModel) -> str:
    layout_ids = _layout_ids(model)
    values = []
    if not item.scope:
        values.append("Весь дашборд")
    for key in item.scope:
        layout = model.dashboard.layout.get(key)
        if layout:
            if layout.kind == "ROOT":
                values.append(f"дашборд «{model.dashboard.title}» (D01)")
            elif layout.kind in {"ROW", "COLUMN"}:
                chart_lookup = _chart_lookup(model)
                visual_ids = _visual_ids(model)
                charts = []
                for child_key in _descendants(model.dashboard.layout, layout.children):
                    child = model.dashboard.layout.get(child_key)
                    chart = chart_lookup.get(child.chart_ref or "") if child and child.kind == "CHART" else None
                    if chart:
                        charts.append(chart)
                values.append(_charts_text(charts, visual_ids) if charts else f"область layout «{key}»")
            else:
                values.append(_layout_reference(layout, layout_ids.get(key)))
        elif key == model.dashboard.uuid:
            values.append(f"дашборд «{model.dashboard.title}» (D01)")
        else:
            values.append(f"элемент scope «{key}»")
    if item.excluded:
        chart_index = _chart_lookup(model)
        visual_ids = _visual_ids(model)
        excluded = []
        for key in item.excluded:
            chart = chart_index.get(key)
            excluded.append(_chart_reference(chart, visual_ids) if chart else f"элемент «{key}»")
        values.append("исключены: " + ", ".join(excluded))
    return "; ".join(values)


def _named_dependencies(item: DashboardFilter, model: CompassModel) -> str:
    filter_ids = _filter_ids(model)
    names: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for index, filter_ in enumerate(model.dashboard.native_filters, 1):
        names[filter_.key].append((filter_.name, filter_ids[("native", filter_.key, index)]))
    values = []
    for key in item.dependencies:
        candidates = names.get(key, [])
        if len(candidates) == 1:
            values.append(_filter_reference("фильтр", candidates[0][0], candidates[0][1]))
        else:
            values.append(f"неразрешённая зависимость фильтра «{key}»")
    return "; ".join(values) or DASH


def _chart_filters(
    chart: Chart,
    model: CompassModel,
    filter_ids: dict[tuple[str, str, int], str] | None = None,
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    filter_ids = filter_ids or _filter_ids(model)
    dataset = next((item for item in model.datasets if item.uuid == chart.dataset_uuid), None)
    visual_ids = _visual_ids(model)
    chart_reference = _chart_reference(chart, visual_ids)
    for index, item in enumerate(chart.params.get("adhoc_filters", []) if isinstance(chart.params.get("adhoc_filters"), list) else [], 1):
        if not isinstance(item, dict):
            continue
        subject = compact(item.get("subject") or item.get("sqlExpression"), 500) or DASH
        field = f"{dataset.name}[{subject}]" if dataset and subject != DASH else subject
        comparator = item.get("comparator")
        values = comparator if isinstance(comparator, list) else [comparator]
        saved_values = list(dict.fromkeys(compact(value, 500) for value in values if compact(value, 500)))[:20]
        saved = "; ".join(saved_values)
        rows.append(
            (
                _filter_reference(
                    "фильтр визуализации",
                    f"Фильтр визуализации {index}",
                    filter_ids[("adhoc", chart.uuid, index)],
                ),
                "Визуализация",
                field,
                compact(item.get("operator") or item.get("clause"), 200) or DASH,
                chart_reference,
                DASH,
                saved or DASH,
            )
        )
    time_range = compact(chart.params.get("time_range"), 500)
    if time_range:
        rows.append(
            (
                _filter_reference(
                    "временной фильтр",
                    "Временной диапазон",
                    filter_ids[("time", chart.uuid, 0)],
                ),
                "Визуализация",
                dataset.main_dttm_col if dataset and dataset.main_dttm_col else DASH,
                "time_range",
                chart_reference,
                DASH,
                time_range,
            )
        )
    return rows


def _filters_by_chart(model: CompassModel) -> dict[str, list[str]]:
    result: defaultdict[str, list[str]] = defaultdict(list)
    filter_ids = _filter_ids(model)
    layout_keys: defaultdict[str, set[str]] = defaultdict(set)
    chart_lookup = _chart_lookup(model)
    for item in model.dashboard.layout.values():
        chart = chart_lookup.get(item.chart_ref or "") if item.kind == "CHART" else None
        if chart:
            layout_keys[chart.uuid].add(item.key.casefold())
    for chart in model.charts:
        for filter_index, filter_ in enumerate(model.dashboard.native_filters, 1):
            excluded = {value.casefold() for value in filter_.excluded}
            chart_keys = {chart.uuid.casefold(), (chart.exported_id or "").casefold()} | layout_keys[chart.uuid]
            if chart_keys & excluded:
                continue
            scope_nodes: set[str] = set()
            for key in filter_.scope:
                if key in model.dashboard.layout:
                    scope_nodes.update(value.casefold() for value in _descendants(model.dashboard.layout, [key]))
                else:
                    scope_nodes.add(key.casefold())
            applies = (
                not filter_.scope
                or model.dashboard.uuid.casefold() in scope_nodes
                or bool(scope_nodes & layout_keys[chart.uuid])
                or chart.uuid.casefold() in scope_nodes
                or bool(chart.exported_id and chart.exported_id.casefold() in scope_nodes)
            )
            if applies:
                result[chart.uuid].append(
                    _filter_reference(
                        "фильтр дашборда",
                        filter_.name,
                        filter_ids[("native", filter_.key, filter_index)],
                    )
                )
        if compact(chart.params.get("time_range"), 500):
            result[chart.uuid].append(
                _filter_reference(
                    "временной фильтр",
                    "Временной диапазон",
                    filter_ids[("time", chart.uuid, 0)],
                )
            )
        adhoc_filters = chart.params.get("adhoc_filters")
        if isinstance(adhoc_filters, list):
            for index, item in enumerate(adhoc_filters, 1):
                if isinstance(item, dict):
                    result[chart.uuid].append(
                        _filter_reference(
                            "фильтр визуализации",
                            f"Фильтр визуализации {index}",
                            filter_ids[("adhoc", chart.uuid, index)],
                        )
                    )
    return result


def _filter_ids(model: CompassModel) -> dict[tuple[str, str, int], str]:
    result: dict[tuple[str, str, int], str] = {}
    number = 1
    for index, item in enumerate(model.dashboard.native_filters, 1):
        result[("native", item.key, index)] = f"F{number:02d}"
        number += 1
    for chart in model.charts:
        adhoc_filters = chart.params.get("adhoc_filters")
        if isinstance(adhoc_filters, list):
            for index, item in enumerate(adhoc_filters, 1):
                if isinstance(item, dict):
                    result[("adhoc", chart.uuid, index)] = f"F{number:02d}"
                    number += 1
        if compact(chart.params.get("time_range"), 500):
            result[("time", chart.uuid, 0)] = f"F{number:02d}"
            number += 1
    return result


def _filter_reference(kind: str, name: str, local_id: str) -> str:
    return f"{kind} «{name}» ({local_id})"


def _interaction_target(
    item: object,
    chart_index: dict[str, Chart],
    dashboard: object | None = None,
    visual_ids: dict[str, str] | None = None,
    layout_ids: dict[str, str] | None = None,
) -> str:
    target_ref = getattr(item, "target_ref", None)
    target = chart_index.get(target_ref or "")
    if target:
        return _chart_reference(target, visual_ids or {})
    layout = getattr(dashboard, "layout", {}) if dashboard else {}
    layout_item = layout.get(target_ref or "")
    if layout_item:
        return _layout_reference(layout_item, (layout_ids or {}).get(target_ref or ""))
    if dashboard and target_ref == getattr(dashboard, "uuid", None):
        return f"дашборд «{getattr(dashboard, 'title', DASH)}» (D01)"
    return compact(target_ref, 500) or DASH


def _chart_definition_signature(chart: Chart, model: CompassModel) -> tuple[str, bool]:
    dataset = next((item for item in model.datasets if item.uuid == chart.dataset_uuid), None)
    raw_metrics = chart.params.get("metrics") or chart.params.get("metric") or []
    raw_metrics = raw_metrics if isinstance(raw_metrics, list) else [raw_metrics]
    resolved: list[object] = []
    complete = dataset is not None
    for value in raw_metrics:
        if isinstance(value, str) and dataset:
            metric = next((item for item in dataset.metrics if value in {item.name, item.label}), None)
            if metric:
                resolved.append({"label": metric.label, "expression": metric.expression, "aggregation": metric.aggregation})
                continue
            complete = False
        elif isinstance(value, dict) and not (value.get("sqlExpression") or value.get("expression")):
            complete = False
        resolved.append(value)
    signature = {
        "dataset": dataset.name if dataset else chart.dataset_uuid,
        "metrics": resolved,
    }
    return compact(json.dumps(signature, ensure_ascii=False, sort_keys=True), 3_000), complete


def _chart_context_signature(chart: Chart, dashboard_filters: list[str]) -> str:
    nested_post_processing = []
    queries = chart.query_context.get("queries")
    if isinstance(queries, list):
        nested_post_processing = [
            query.get("post_processing")
            for query in queries
            if isinstance(query, dict) and query.get("post_processing") is not None
        ]
    context = {
        "dashboard_filters": dashboard_filters,
        "dimensions": chart.params.get("groupby") or chart.params.get("columns") or chart.params.get("all_columns"),
        "filters": chart.params.get("adhoc_filters"),
        "time_range": chart.params.get("time_range"),
        "time_grain": chart.params.get("time_grain_sqla"),
        "post_processing": chart.params.get("post_processing")
        or chart.query_context.get("post_processing")
        or nested_post_processing,
    }
    return compact(json.dumps(context, ensure_ascii=False, sort_keys=True), 3_000)


def _consumer_context(charts: list[Chart], filters: dict[str, list[str]]) -> str:
    if not charts:
        return DASH
    values = [_chart_context_signature(chart, filters.get(chart.uuid, [])) for chart in charts]
    return " | ".join(dict.fromkeys(values))


def _expression_mentions(expression: str | None, field: str) -> bool:
    return bool(expression and re.search(rf"(?<![\w]){re.escape(field)}(?![\w])", expression, re.IGNORECASE))


def _visual_ids(model: CompassModel) -> dict[str, str]:
    chart_index: dict[str, Chart] = {}
    for chart in model.charts:
        chart_index[chart.uuid] = chart
        if chart.exported_id:
            chart_index[chart.exported_id] = chart
    result: dict[str, str] = {}
    number = 1
    for _, keys in _layout_groups(model.dashboard.layout, model.dashboard.root_key):
        for key in keys:
            item = model.dashboard.layout.get(key)
            if not item or item.kind in {"ROOT", "ROW", "COLUMN", "TAB"}:
                continue
            chart = chart_index.get(item.chart_ref or "") if item.kind == "CHART" else None
            if chart:
                result[chart.uuid] = f"V{number:03d}"
            number += 1
    return result


def _layout_reference(item: LayoutItem, local_id: str | None = None) -> str:
    kind = {"TAB": "вкладка", "MARKDOWN": "текстовый блок", "HEADER": "заголовок"}.get(
        item.kind, "элемент"
    )
    return f"{kind} «{item.title or item.key}»" + (f" ({local_id})" if local_id else "")


def _chart_lookup(model: CompassModel) -> dict[str, Chart]:
    result: dict[str, Chart] = {}
    for chart in model.charts:
        result[chart.uuid] = chart
        if chart.exported_id:
            result[chart.exported_id] = chart
    return result


def _chart_reference(chart: Chart, visual_ids: dict[str, str]) -> str:
    return f"визуализация «{chart.title}»" + (f" ({visual_ids[chart.uuid]})" if chart.uuid in visual_ids else "")


def _layout_ids(model: CompassModel) -> dict[str, str]:
    result: dict[str, str] = {}
    root = model.dashboard.layout.get(model.dashboard.root_key or "")
    tab_keys = [
        key for key in (root.children if root else [])
        if key in model.dashboard.layout and model.dashboard.layout[key].kind == "TAB"
    ]
    tab_keys.extend(
        key for key, item in sorted(model.dashboard.layout.items())
        if item.kind == "TAB" and key not in tab_keys
    )
    for index, key in enumerate(tab_keys, 1):
        result[key] = f"T{index:02d}"
    visual_number = 1
    for _, keys in _layout_groups(model.dashboard.layout, model.dashboard.root_key):
        for key in keys:
            item = model.dashboard.layout.get(key)
            if not item:
                continue
            if item.kind not in {"ROOT", "ROW", "COLUMN", "TAB"}:
                result[key] = f"V{visual_number:03d}"
                visual_number += 1
    return result


def _access_restrictions(model: CompassModel) -> list[object]:
    if not model.dashboard.access_restrictions:
        return []
    rows = [
        (item.name, item.permission or DASH, item.scope or DASH)
        for item in model.dashboard.access_restrictions
    ]
    return [
        HeadingBlock(1, "Доступы и ограничения"),
        TableBlock(("Ограничение", "Разрешение", "Область"), rows, (4_800, 4_000, 5_600)),
    ]


def _descendants(layout: dict[str, LayoutItem], roots: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def visit(key: str) -> None:
        if key in seen:
            return
        seen.add(key)
        item = layout.get(key)
        if not item:
            return
        result.append(key)
        for child in item.children:
            visit(child)

    for root in roots:
        visit(root)
    return result


def _chart_fields(chart: Chart) -> str:
    values: list[str] = []
    for key in ("groupby", "columns", "all_columns", "metrics", "metric"):
        value = chart.params.get(key)
        if isinstance(value, list):
            values.extend(compact(item, 500) for item in value if compact(item, 500))
        elif value:
            values.append(compact(value, 500))
    return "; ".join(dict.fromkeys(values)) or DASH


def _chart_calculation(chart: Chart) -> str:
    metric = chart.params.get("metric") or chart.params.get("metrics")
    return compact(metric, 4_000) or DASH


def _viz_label(value: str) -> str:
    labels = {
        "bar": "Диаграмма",
        "dist_bar": "Диаграмма",
        "echarts_timeseries_bar": "Временная диаграмма",
        "line": "Линейный график",
        "echarts_timeseries_line": "Временной график",
        "table": "Таблица",
        "pivot_table_v2": "Сводная таблица",
        "big_number": "Карточка KPI",
        "big_number_total": "Карточка KPI",
        "filter_box": "Элемент фильтрации",
    }
    return labels.get(value, f"Визуализация ({value})")


def _layout_label(value: str) -> str:
    return {"MARKDOWN": "Текстовый блок", "HEADER": "Заголовок", "DIVIDER": "Разделитель"}.get(
        value, f"Элемент layout ({value})"
    )


def _status_label(status: str) -> str:
    return {"OK": "Полное по поддерживаемой metadata", "PARTIAL": "Частичное", "FAILED": "Ошибка"}.get(status, status)


def _all_text(model: SafeDocumentModel) -> list[str]:
    result = [model.title, model.subtitle, model.source_name]
    for block in model.blocks:
        if isinstance(block, HeadingBlock):
            result.append(block.text)
        elif isinstance(block, ParagraphBlock):
            result.append(block.text)
        elif isinstance(block, TableBlock):
            result.extend(block.headers)
            result.extend(value for row in block.rows for value in row)
    return result
