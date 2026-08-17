from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class Issue:
    severity: str
    code: str
    message: str


@dataclass(slots=True)
class Database:
    uuid: str
    name: str
    backend: str | None = None


@dataclass(slots=True)
class Column:
    name: str
    verbose_name: str | None = None
    data_type: str | None = None
    expression: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    aggregations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Metric:
    name: str
    label: str
    expression: str | None
    metric_type: str = "saved"
    aggregation: str | None = None


@dataclass(slots=True)
class Dataset:
    uuid: str
    database_uuid: str | None
    name: str
    schema: str | None
    catalog: str | None
    sql: str | None
    main_dttm_col: str | None
    columns: list[Column] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)


@dataclass(slots=True)
class Chart:
    uuid: str
    exported_id: str | None
    title: str
    viz_type: str
    dataset_uuid: str | None
    params: dict[str, object]
    query_context: dict[str, object]


@dataclass(slots=True)
class LayoutItem:
    key: str
    kind: str
    title: str | None
    children: list[str]
    chart_ref: str | None = None
    text: str | None = None


@dataclass(slots=True)
class DashboardFilter:
    key: str
    name: str
    filter_type: str
    dataset_uuid: str | None
    column: str | None
    operator: str | None
    scope: list[str]
    excluded: list[str]
    dependencies: list[str]
    values: list[str]
    source: str = "native"


@dataclass(slots=True)
class Interaction:
    source_ref: str
    action_type: str
    target_ref: str | None
    target_url: str | None = None


@dataclass(slots=True)
class AccessRestriction:
    name: str
    permission: str | None
    scope: str | None


@dataclass(slots=True)
class Dashboard:
    uuid: str
    title: str
    published: bool | None
    layout: dict[str, LayoutItem]
    root_key: str | None
    native_filters: list[DashboardFilter]
    interactions: list[Interaction]
    access_restrictions: list[AccessRestriction] = field(default_factory=list)


@dataclass(slots=True)
class CompassModel:
    source_path: Path
    export_version: str | None
    generated_at: datetime
    databases: list[Database]
    datasets: list[Dataset]
    charts: list[Chart]
    dashboard: Dashboard
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "PARTIAL" if self.issues else "OK"


@dataclass(slots=True)
class ConversionResult:
    status: str
    issues: list[Issue]
