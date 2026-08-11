from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class Issue:
    severity: str
    component: str
    code: str
    message: str
    source_path: str | None = None
    page_id: str | None = None
    visual_id: str | None = None


@dataclass(slots=True)
class FileInfo:
    path: Path
    size_bytes: int
    sha256: str
    modified_at: datetime


@dataclass(slots=True)
class FieldRef:
    table: str | None
    name: str
    object_type: str
    role: str | None
    usage: str
    aggregation: str | None
    source_path: str
    status: str = "UNRESOLVED"
    reason: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]" if self.table else self.name


@dataclass(slots=True)
class FilterInfo:
    scope: str
    field: str
    filter_type: str
    saved_value_count: int
    source_path: str
    saved_values: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActionInfo:
    action_type: str
    target: str | None
    source_path: str


@dataclass(slots=True)
class VisualInfo:
    id: str
    title: str
    technical_type: str
    category: str
    x: float | None
    y: float | None
    z: float | None
    width: float | None
    height: float | None
    visible: bool
    fields: list[FieldRef] = field(default_factory=list)
    filters: list[FilterInfo] = field(default_factory=list)
    actions: list[ActionInfo] = field(default_factory=list)
    source_path: str = ""


@dataclass(slots=True)
class PageInfo:
    id: str
    name: str
    order: int
    visible: bool
    page_type: str
    width: float | None
    height: float | None
    filters: list[FilterInfo] = field(default_factory=list)
    visuals: list[VisualInfo] = field(default_factory=list)
    source_path: str = ""


@dataclass(slots=True)
class BookmarkInfo:
    id: str
    name: str
    group: str | None
    order: int
    associated_page_id: str | None
    state_scope: str | None
    affected_visual_ids: list[str]
    trigger_refs: list[str] = field(default_factory=list)
    source_path: str = ""
    saved_values: list[str] = field(default_factory=list)
    saved_value_count: int = 0


@dataclass(slots=True)
class NavigationEdge:
    source_page_id: str
    source_page_name: str
    source_visual_id: str
    source_visual_title: str
    action_type: str
    target_kind: str
    target_id: str | None
    target_name: str | None
    target_page_id: str | None
    target_page_name: str | None
    status: str


@dataclass(slots=True)
class ReportInfo:
    title: str
    report_format: str
    format_version: str | None
    pages: list[PageInfo] = field(default_factory=list)
    bookmarks: list[BookmarkInfo] = field(default_factory=list)
    report_filters: list[FilterInfo] = field(default_factory=list)
    navigation: list[NavigationEdge] = field(default_factory=list)


@dataclass(slots=True)
class ColumnInfo:
    table: str
    name: str
    data_type: str | None
    hidden: bool | None
    cardinality: int | None
    expression: str | None = None
    examples: list[str] = field(default_factory=list)
    example_status: str = "NOT_PROFILED"
    format_string: str | None = None
    source_column: str | None = None
    native_data_type: str | None = None
    summarize_by: str | None = None


@dataclass(slots=True)
class TableInfo:
    name: str
    hidden: bool | None
    storage_mode: str | None
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass(slots=True)
class MeasureInfo:
    table: str
    name: str
    expression: str
    format_string: str | None
    display_folder: str | None
    description: str | None
    data_type: str | None = None


@dataclass(slots=True)
class RelationshipInfo:
    from_object: str
    to_object: str
    cardinality: str | None
    active: bool | None
    filter_direction: str | None
    security_filter: str | None


@dataclass(slots=True)
class QueryInfo:
    name: str
    kind: str
    expression_available: bool
    connection_types: list[str] = field(default_factory=list)
    source_details: list[str] = field(default_factory=list)
    connection_strings: list[str] = field(default_factory=list)
    expressions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConnectionInfo:
    connection_type: str | None
    source_details: list[str] = field(default_factory=list)
    connection_string: str | None = None


@dataclass(slots=True)
class SecurityRule:
    security_type: str
    role: str
    table: str | None
    column: str | None
    expression: str | None
    permission: str | None


@dataclass(slots=True)
class SemanticModelInfo:
    status: str
    tables: list[TableInfo] = field(default_factory=list)
    measures: list[MeasureInfo] = field(default_factory=list)
    relationships: list[RelationshipInfo] = field(default_factory=list)
    queries: list[QueryInfo] = field(default_factory=list)
    rls: list[SecurityRule] = field(default_factory=list)
    ols: list[SecurityRule] = field(default_factory=list)
    connection_types: list[str] = field(default_factory=list)
    library_version: str | None = None
    connections: list[ConnectionInfo] = field(default_factory=list)


@dataclass(slots=True)
class DocumentationModel:
    file: FileInfo
    report: ReportInfo
    semantic_model: SemanticModelInfo
    issues: list[Issue]
    generated_at: datetime
    application_version: str
