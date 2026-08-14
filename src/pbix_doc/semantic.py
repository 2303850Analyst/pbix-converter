from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .domain import (
    ColumnInfo,
    ConnectionInfo,
    Issue,
    MeasureInfo,
    QueryInfo,
    RelationshipInfo,
    SecurityRule,
    SemanticModelInfo,
    TableInfo,
)
from .policy import METADATA_EXCLUSION_POLICY


SENSITIVE_NAME_PARTS = (
    "login",
    "user",
    "upn",
    "email",
    "mail",
    "phone",
    "account",
    "guid",
    "uuid",
    "secret",
    "token",
    "password",
    "логин",
    "пользоват",
    "почт",
    "телефон",
    "парол",
    "токен",
)

SUMMARIZE_BY_CODES = {
    1: "По умолчанию",
    2: "Без агрегации",
    3: "SUM",
    4: "MIN",
    5: "MAX",
    6: "COUNT",
    7: "AVERAGE",
    8: "DISTINCT_COUNT",
}

SUMMARIZE_BY_NAMES = {
    "default": "По умолчанию",
    "none": "Без агрегации",
    "donotsummarize": "Без агрегации",
    "sum": "SUM",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "average": "AVERAGE",
    "avg": "AVERAGE",
    "distinctcount": "DISTINCT_COUNT",
}

MODEL_DATA_TYPE_CODES = {
    1: "Automatic",
    2: "String",
    6: "Int64",
    8: "Double",
    9: "DateTime",
    10: "Decimal",
    11: "Boolean",
    17: "Binary",
    19: "Unknown",
    20: "Variant",
}


def normalize_summarize_by(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    normalized: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"[+-]?\d+", text):
            normalized = int(text)
        else:
            key = re.sub(r"[^a-z]", "", text.casefold())
            return SUMMARIZE_BY_NAMES.get(key, text)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            normalized = int(value)
    if isinstance(normalized, int):
        return SUMMARIZE_BY_CODES.get(normalized, f"НЕИЗВЕСТНЫЙ КОД ({normalized})")
    return str(normalized).strip() or None


def records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return list(value.to_dict("records"))
        except TypeError:
            pass
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    if hasattr(value, "tolist"):
        items = value.tolist()
        if isinstance(items, list):
            return [{"Name": item} for item in items]
    return []


def rget(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    folded = {str(key).casefold(): value for key, value in record.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value is not None:
            return value
    return default


def bool_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).casefold() in {"true", "1", "yes", "active"}


def package_version() -> str | None:
    try:
        return version("pbixray")
    except PackageNotFoundError:
        return None


def empty_model(status: str) -> SemanticModelInfo:
    return SemanticModelInfo(status=status, library_version=package_version())


def load_semantic_model(
    pbix_path: Path,
    temp_dir: Path | None,
    example_limit: int,
    issues: list[Issue],
) -> SemanticModelInfo:
    try:
        from pbixray import LiveConnectionError, NoEmbeddedModelError, PBIXRay
    except ImportError:
        issues.append(
            Issue(
                "ERROR",
                "MODEL",
                "PBIXRAY_NOT_INSTALLED",
                "PBIXRay не установлен; семантическая модель недоступна",
            )
        )
        return empty_model("NOT_AVAILABLE")

    try:
        with PBIXRay(
            str(pbix_path),
            on_disk=True,
            temp_dir=str(temp_dir) if temp_dir else None,
        ) as model:
            result = read_model_metadata(model, issues)
            if example_limit > 0:
                profile_examples(model, result, example_limit, issues)
            return result
    except LiveConnectionError as error:
        result = empty_model("LIVE_CONNECTION")
        connection_rows = records(getattr(error, "connections", []))
        result.connection_types = sorted(
            {
                str(item.get("connectionType") or item.get("ConnectionType") or "LIVE")
                for item in connection_rows
                if isinstance(item, dict)
            }
        )
        result.connections = extract_model_connections(connection_rows)
        issues.append(
            Issue(
                "INFO",
                "MODEL",
                "LIVE_CONNECTION",
                "PBIX использует удалённую семантическую модель; локальная модель отсутствует",
            )
        )
        return result
    except NoEmbeddedModelError:
        issues.append(
            Issue("INFO", "MODEL", "NO_EMBEDDED_MODEL", "В PBIX не найдена встроенная семантическая модель")
        )
        return empty_model("NO_MODEL")
    except Exception as error:
        issues.append(
            Issue(
                "ERROR",
                "MODEL",
                "MODEL_EXTRACTION_FAILED",
                safe_error_message(error, pbix_path, temp_dir),
            )
        )
        return empty_model("FAILED")


def read_model_metadata(model: Any, issues: list[Issue]) -> SemanticModelInfo:
    table_rows = safe_records(model, "tmschema_tables", issues)
    column_rows = safe_records(model, "tmschema_columns", issues)
    schema_rows = safe_records(model, "schema", issues)
    statistics_rows = safe_records(model, "statistics", issues)
    calculated_rows = safe_records(model, "dax_columns", issues)
    power_query_rows = safe_records(model, "power_query", issues)
    mashup_query_rows = optional_records(model, "mashup_queries", issues)
    connection_rows = optional_records(model, "connections", issues)
    query_rows = deduplicate_query_rows(power_query_rows + mashup_query_rows)
    native_types = native_query_column_types(query_rows, issues)

    schema_index = {
        (str(rget(item, "TableName", "Table")), str(rget(item, "ColumnName", "Name"))): item
        for item in schema_rows
    }
    statistics_index = {
        (str(rget(item, "TableName", "Table")), str(rget(item, "ColumnName", "Name"))): item
        for item in statistics_rows
    }
    calculated_index = {
        (str(rget(item, "TableName", "Table")), str(rget(item, "ColumnName", "Name"))): item
        for item in calculated_rows
    }

    table_map: dict[str, TableInfo] = {}
    for row in table_rows:
        name = rget(row, "Name", "TableName")
        if not name:
            continue
        table_map[str(name)] = TableInfo(
            str(name),
            bool_value(rget(row, "IsHidden", "Hidden")),
            text_or_none(rget(row, "StorageMode", "Mode")),
        )

    for row in column_rows:
        table_name = rget(row, "TableName", "Table")
        name = rget(row, "Name", "ColumnName")
        if not table_name or not name:
            continue
        table_name = str(table_name)
        name = str(name)
        table = table_map.setdefault(table_name, TableInfo(table_name, None, None))
        schema = schema_index.get((table_name, name), {})
        stats = statistics_index.get((table_name, name), {})
        calculated = calculated_index.get((table_name, name), {})
        cardinality = rget(stats, "Cardinality")
        source_column = identifier_text(rget(row, "SourceColumn", "SourceName"))
        native_type = (
            native_types.get((table_name, source_column))
            if source_column
            else None
        )
        table.columns.append(
            ColumnInfo(
                table_name,
                name,
                model_data_type(row, schema),
                bool_value(rget(row, "IsHidden", "Hidden")),
                int(cardinality) if cardinality is not None else None,
                text_or_none(rget(calculated, "Expression")),
                format_string=text_or_none(rget(row, "FormatString", "Format")),
                source_column=source_column,
                native_data_type=native_type,
                summarize_by=normalize_summarize_by(
                    rget(row, "SummarizeBy", "DefaultSummarization")
                ),
            )
        )

    if not table_map:
        for row in safe_records(model, "tables", issues):
            name = rget(row, "TableName", "Name")
            if name:
                table_map[str(name)] = TableInfo(str(name), None, None)

    measures: list[MeasureInfo] = []
    for row in safe_records(model, "dax_measures", issues):
        table_name = text_or_none(rget(row, "TableName", "Table")) or "<UNKNOWN>"
        name = text_or_none(rget(row, "Name", "MeasureName"))
        if not name:
            continue
        measures.append(
            MeasureInfo(
                table_name,
                name,
                text_or_none(rget(row, "Expression")) or "",
                text_or_none(rget(row, "FormatString", "Format")),
                text_or_none(rget(row, "DisplayFolder")),
                text_or_none(rget(row, "Description")),
                normalize_model_data_type(rget(row, "DataType", "Type")),
            )
        )

    relationships: list[RelationshipInfo] = []
    for row in safe_records(model, "relationships", issues):
        from_table = text_or_none(rget(row, "FromTableName", "FromTable")) or "?"
        from_column = text_or_none(rget(row, "FromColumnName", "FromColumn")) or "?"
        to_table = text_or_none(rget(row, "ToTableName", "ToTable")) or "?"
        to_column = text_or_none(rget(row, "ToColumnName", "ToColumn")) or "?"
        relationships.append(
            RelationshipInfo(
                f"{from_table}[{from_column}]",
                f"{to_table}[{to_column}]",
                text_or_none(rget(row, "Cardinality")),
                bool_value(rget(row, "IsActive", "Active")),
                text_or_none(rget(row, "CrossFilteringBehavior", "FilterDirection")),
                text_or_none(rget(row, "SecurityFilteringBehavior", "SecurityFilter")),
            )
        )

    queries_by_name: dict[str, QueryInfo] = {}
    for row in query_rows:
        if bool_value(rget(row, "IsParameter")):
            continue
        name = text_or_none(rget(row, "TableName", "Name"))
        if name:
            expression = query_expression(row)
            connectors = extract_power_query_connection_types(expression or "")
            source_details, connection_strings = extract_power_query_source_metadata(
                expression or ""
            )
            safe_expression = redact_connection_secrets(expression) if expression else None
            existing = queries_by_name.get(name)
            if existing:
                existing.expression_available = existing.expression_available or bool(expression)
                existing.connection_types = sorted(
                    set(existing.connection_types) | set(connectors), key=str.casefold
                )
                existing.source_details = list(
                    dict.fromkeys(existing.source_details + source_details)
                )
                existing.connection_strings = list(
                    dict.fromkeys(existing.connection_strings + connection_strings)
                )
                if safe_expression and safe_expression not in existing.expressions:
                    existing.expressions.append(safe_expression)
            else:
                queries_by_name[name] = QueryInfo(
                    name,
                    text_or_none(rget(row, "Kind")) or "POWER_QUERY",
                    bool(expression),
                    connectors,
                    source_details,
                    connection_strings,
                    [safe_expression] if safe_expression else [],
                )
    queries = list(queries_by_name.values())

    rls: list[SecurityRule] = []
    for row in safe_records(model, "rls", issues):
        rls.append(
            SecurityRule(
                "RLS",
                text_or_none(rget(row, "RoleName", "Role")) or "<UNKNOWN>",
                text_or_none(rget(row, "TableName", "Table")),
                None,
                text_or_none(rget(row, "FilterExpression", "Expression")),
                text_or_none(rget(row, "MetadataPermission", "Permission")),
            )
        )

    ols: list[SecurityRule] = []
    for row in safe_records(model, "ols", issues):
        ols.append(
            SecurityRule(
                "OLS",
                text_or_none(rget(row, "RoleName", "Role")) or "<UNKNOWN>",
                text_or_none(rget(row, "TableName", "Table")),
                text_or_none(rget(row, "ColumnName", "Column")),
                None,
                text_or_none(rget(row, "Permission")),
            )
        )

    connection_types = {
        compact_metadata(rget(item, "connectionType", "ConnectionType", "Type"))
        for item in connection_rows
    }
    connection_types.discard("")
    for query in queries:
        connection_types.update(query.connection_types)
    return SemanticModelInfo(
        "EMBEDDED",
        sorted(table_map.values(), key=lambda item: item.name.casefold()),
        sorted(measures, key=lambda item: (item.table.casefold(), item.name.casefold())),
        relationships,
        queries,
        rls,
        ols,
        sorted(connection_types, key=str.casefold),
        package_version(),
        extract_model_connections(connection_rows),
    )


def model_data_type(row: dict[str, Any], schema: dict[str, Any]) -> str | None:
    raw = rget(row, "DataType", "Type")
    normalized = normalize_model_data_type(raw)
    if normalized:
        return normalized
    return normalize_model_data_type(
        rget(schema, "SemanticType", "DataType", "PandasDataType")
    )


def normalize_model_data_type(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    normalized: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"[+-]?\d+", text):
            normalized = int(text)
        else:
            return text
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value.is_integer():
            normalized = int(value)
    if isinstance(normalized, int):
        return MODEL_DATA_TYPE_CODES.get(normalized)
    return text_or_none(normalized)


def native_query_column_types(
    rows: list[dict[str, Any]],
    issues: list[Issue] | None = None,
) -> dict[tuple[str, str], str]:
    resolver = _MTypeLineageResolver(rows)
    result: dict[tuple[str, str], str] = {}
    for table, schema in resolver.resolve_all().items():
        for column, (_, data_type) in schema.items():
            result[(table, column)] = data_type
    if issues is not None and resolver.incomplete:
        issues.append(
            Issue(
                "WARNING",
                "POWER_QUERY",
                "M_TYPE_LINEAGE_PARTIAL",
                "Типы полей источника определены частично: итоговая ветка M содержит неподдерживаемое преобразование",
            )
        )
    return result


MTypeSchema = dict[str, tuple[str, str]]


class _MTypeLineageResolver:
    _MAX_DEPTH = 512
    _PRESERVES_SCHEMA = {
        "Table.Buffer",
        "Table.Distinct",
        "Table.FillDown",
        "Table.FillUp",
        "Table.FirstN",
        "Table.LastN",
        "Table.Range",
        "Table.RemoveFirstN",
        "Table.RemoveLastN",
        "Table.RemoveRowsWithErrors",
        "Table.ReplaceErrorValues",
        "Table.ReplaceValue",
        "Table.ReorderColumns",
        "Table.ReverseRows",
        "Table.SelectRows",
        "Table.Skip",
        "Table.Sort",
        "Table.StopFolding",
    }

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._loaded: dict[str, list[str]] = defaultdict(list)
        self._shared: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            if bool_value(rget(row, "IsParameter")):
                continue
            table_name = identifier_text(rget(row, "TableName"))
            query_name = identifier_text(rget(row, "Name"))
            name = table_name or query_name
            expression = query_expression(row)
            if not name or not expression:
                continue
            key = name
            definitions = self._loaded if table_name else self._shared
            if expression not in definitions[key]:
                definitions[key].append(expression)
        self._cache: dict[str, MTypeSchema] = {}
        self.incomplete = False

    def resolve_all(self) -> dict[str, MTypeSchema]:
        result: dict[str, MTypeSchema] = {}
        for name in self._loaded.keys() | self._shared.keys():
            if name in self._loaded:
                schemas = [
                    self._resolve_expression(expression, {}, frozenset(), frozenset(), 0)
                    for expression in self._loaded[name]
                ]
                schema = self._consensus(schemas)
            else:
                schema = self._resolve_query(name, frozenset())
            if schema:
                result[name] = schema
        return result

    def _resolve_query(self, name: str, stack: frozenset[str]) -> MTypeSchema:
        if name in stack:
            self.incomplete = True
            return {}
        cached = self._cache.get(name)
        if cached is not None:
            return dict(cached)
        next_stack = stack | {name}
        definitions = self._shared.get(name) or self._loaded.get(name) or ()
        schemas = [
            self._resolve_expression(expression, {}, next_stack, frozenset(), 0)
            for expression in definitions
        ]
        resolved = self._consensus(schemas)
        self._cache[name] = resolved
        return dict(resolved)

    def _consensus(self, schemas: list[MTypeSchema]) -> MTypeSchema:
        resolved = _m_schema_consensus(schemas)
        if any(schema != resolved for schema in schemas if schema):
            self.incomplete = True
        return resolved

    def _resolve_expression(
        self,
        expression: str,
        outer_bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        if depth > self._MAX_DEPTH:
            self.incomplete = True
            return {}
        parsed = _m_outer_let(_strip_m_outer_parentheses(expression.strip()))
        if parsed:
            local, output = parsed
            bindings = dict(outer_bindings)
            bindings.update(local)
            return self._resolve_term(
                output, bindings, query_stack, binding_stack, depth + 1
            )
        return self._resolve_term(
            expression, outer_bindings, query_stack, binding_stack, depth + 1
        )

    def _resolve_term(
        self,
        expression: str,
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        if depth > self._MAX_DEPTH:
            self.incomplete = True
            return {}
        text = _strip_m_outer_parentheses(expression.strip())
        reference = _m_reference_name(text)
        if reference:
            key = reference
            if key in bindings:
                token = (id(bindings), key)
                if token in binding_stack:
                    self.incomplete = True
                    return {}
                return self._resolve_expression(
                    bindings[key],
                    bindings,
                    query_stack,
                    binding_stack | {token},
                    depth + 1,
                )
            if key in self._shared or key in self._loaded:
                return self._resolve_query(key, query_stack)
            return {}

        call = _m_root_call(text)
        if not call:
            if text.startswith("if "):
                self.incomplete = True
            return {}
        function_name, arguments = call

        if function_name == "Table.TransformColumnTypes":
            return self._transform_column_types(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name == "Table.TransformColumns":
            return self._transform_columns(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name == "Table.AddColumn":
            return self._add_column(arguments, bindings, query_stack, binding_stack, depth)
        if function_name == "Table.RenameColumns":
            return self._rename_columns(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name in {"Value.ReplaceType", "#table"}:
            return self._replace_type(
                function_name, arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name in {"Table.FromRecords", "Table.FromRows"}:
            return self._declared_table_type(arguments, 1, bindings)
        if function_name == "Table.SelectColumns":
            return self._select_columns(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name == "Table.RemoveColumns":
            return self._remove_columns(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name == "Table.Combine":
            return self._combine(
                arguments, bindings, query_stack, binding_stack, depth
            )
        if function_name in self._PRESERVES_SCHEMA and arguments:
            return self._resolve_term(
                arguments[0], bindings, query_stack, binding_stack, depth + 1
            )
        if function_name.startswith("Table.") or (
            arguments
            and self._resolve_term(
                arguments[0], bindings, query_stack, binding_stack, depth + 1
            )
        ):
            self.incomplete = True
        return {}

    def _base_schema(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        if not arguments:
            return {}
        return self._resolve_term(
            arguments[0], bindings, query_stack, binding_stack, depth + 1
        )

    def _transform_column_types(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return {}
        rows = _m_constant_rows(arguments[1], bindings)
        if rows is None:
            self.incomplete = True
            return {}
        for row in rows:
            if len(row) != 2:
                self.incomplete = True
                return {}
            column = _decode_m_string(row[0])
            data_type = _m_type_expression(row[1])
            if not column or not data_type:
                self.incomplete = True
                return {}
            _set_m_schema_type(schema, column, data_type)
        return schema

    def _transform_columns(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return {}
        rows = _m_constant_rows(arguments[1], bindings)
        if rows is None:
            self.incomplete = True
            return {}
        if len(arguments) >= 3 and arguments[2].strip() != "null":
            schema = {}
        for row in rows:
            if len(row) not in {2, 3}:
                self.incomplete = True
                return {}
            column = _decode_m_string(row[0]) if row else None
            if not column:
                self.incomplete = True
                return {}
            data_type = _m_type_expression(row[-1]) if len(row) >= 3 else None
            if data_type:
                _set_m_schema_type(schema, column, data_type)
            else:
                schema.pop(column, None)
        return schema

    def _add_column(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return schema
        column = _decode_m_string(arguments[1])
        if not column:
            return schema
        data_type = _m_type_expression(arguments[3]) if len(arguments) >= 4 else None
        if data_type:
            _set_m_schema_type(schema, column, data_type)
        else:
            schema.pop(column, None)
        return schema

    def _rename_columns(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return {}
        rows = _m_constant_rows(arguments[1], bindings)
        if rows is None:
            self.incomplete = True
            return {}
        for row in rows:
            if len(row) != 2:
                self.incomplete = True
                return {}
            source = _decode_m_string(row[0])
            target = _decode_m_string(row[1])
            if not source or not target:
                self.incomplete = True
                return {}
            known = schema.pop(source, None)
            schema.pop(target, None)
            if known:
                schema[target] = (target, known[1])
        return schema

    def _replace_type(
        self,
        function_name: str,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        type_index = 1 if function_name == "Value.ReplaceType" else 0
        return self._declared_table_type(
            arguments,
            type_index,
            bindings,
            required=function_name == "Value.ReplaceType",
        )

    def _declared_table_type(
        self,
        arguments: list[str],
        type_index: int,
        bindings: dict[str, str],
        *,
        required: bool = False,
    ) -> MTypeSchema:
        if len(arguments) <= type_index:
            return {}
        type_expression = _m_constant_expression(arguments[type_index], bindings)
        assignments = _m_exact_table_type(type_expression)
        if assignments is None:
            if required or re.search(
                r"\btype\s+table\b", _mask_m_strings(type_expression)
            ):
                self.incomplete = True
            return {}
        schema: MTypeSchema = {}
        for column, data_type in assignments:
            _set_m_schema_type(schema, column, data_type)
        return schema

    def _select_columns(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return {}
        names = _m_constant_strings(arguments[1], bindings)
        if names is None:
            self.incomplete = True
            return {}
        selected = set(names)
        return {key: value for key, value in schema.items() if key in selected}

    def _remove_columns(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        schema = self._base_schema(
            arguments, bindings, query_stack, binding_stack, depth
        )
        if len(arguments) < 2:
            return {}
        names = _m_constant_strings(arguments[1], bindings)
        if names is None:
            self.incomplete = True
            return {}
        for name in names:
            schema.pop(name, None)
        return schema

    def _combine(
        self,
        arguments: list[str],
        bindings: dict[str, str],
        query_stack: frozenset[str],
        binding_stack: frozenset[tuple[int, str]],
        depth: int,
    ) -> MTypeSchema:
        if not arguments:
            return {}
        items = _m_constant_list(arguments[0], bindings)
        if items is None:
            self.incomplete = True
            return {}
        schemas = [
            self._resolve_term(item, bindings, query_stack, binding_stack, depth + 1)
            for item in items
        ]
        return self._consensus(schemas)


def _m_schema_consensus(schemas: list[MTypeSchema]) -> MTypeSchema:
    if not schemas:
        return {}
    common = set(schemas[0])
    for schema in schemas[1:]:
        common.intersection_update(schema)
    result: MTypeSchema = {}
    for column in common:
        values = [schema[column] for schema in schemas]
        if len({value[1] for value in values}) == 1:
            result[column] = values[0]
    return result


def _set_m_schema_type(schema: MTypeSchema, column: str, data_type: str) -> None:
    name = column
    normalized_type = compact_metadata(data_type)
    if name and normalized_type:
        schema[name] = (name, f"M: {normalized_type}")


def _m_outer_let(expression: str) -> tuple[dict[str, str], str] | None:
    text = _mask_m_comments(expression).strip()
    match = re.match(r"^let\b", text)
    if not match:
        return None
    output_position = _m_outer_in_position(text, match.end())
    if output_position is None:
        return None
    bindings: dict[str, str] = {}
    for item in _split_m_top_level(text[match.end() : output_position]):
        assignment = _m_top_level_equal_position(item)
        if assignment is None:
            continue
        name = _decode_m_identifier(item[:assignment])
        value = item[assignment + 1 :].strip()
        if name and value:
            bindings[name] = value
    output = text[output_position + 2 :].strip()
    return (bindings, output) if output else None


def _m_outer_in_position(expression: str, start: int) -> int | None:
    parentheses = braces = brackets = nested_lets = 0
    in_string = False
    index = start
    while index < len(expression):
        current = expression[index]
        following = expression[index + 1] if index + 1 < len(expression) else ""
        if in_string:
            if current == '"' and following == '"':
                index += 2
                continue
            if current == '"':
                in_string = False
            index += 1
            continue
        if current == '"':
            in_string = True
            index += 1
            continue
        if current == "(":
            parentheses += 1
        elif current == ")":
            parentheses = max(0, parentheses - 1)
        elif current == "{":
            braces += 1
        elif current == "}":
            braces = max(0, braces - 1)
        elif current == "[":
            brackets += 1
        elif current == "]":
            brackets = max(0, brackets - 1)
        elif parentheses == 0 and braces == 0 and brackets == 0 and (
            current.isalpha() or current == "_"
        ):
            end = index + 1
            while end < len(expression) and (
                expression[end].isalnum() or expression[end] == "_"
            ):
                end += 1
            keyword = expression[index:end]
            if keyword == "let":
                nested_lets += 1
            elif keyword == "in":
                if nested_lets:
                    nested_lets -= 1
                else:
                    return index
            index = end
            continue
        index += 1
    return None


def _m_top_level_equal_position(expression: str) -> int | None:
    parentheses = braces = brackets = 0
    in_string = False
    index = 0
    while index < len(expression):
        current = expression[index]
        following = expression[index + 1] if index + 1 < len(expression) else ""
        if in_string:
            if current == '"' and following == '"':
                index += 2
                continue
            if current == '"':
                in_string = False
        elif current == '"':
            in_string = True
        elif current == "(":
            parentheses += 1
        elif current == ")":
            parentheses = max(0, parentheses - 1)
        elif current == "{":
            braces += 1
        elif current == "}":
            braces = max(0, braces - 1)
        elif current == "[":
            brackets += 1
        elif current == "]":
            brackets = max(0, brackets - 1)
        elif current == "=" and parentheses == 0 and braces == 0 and brackets == 0:
            return index
        index += 1
    return None


def _strip_m_outer_parentheses(expression: str) -> str:
    text = expression.strip()
    while text.startswith("("):
        end = _matching_delimiter(text, 0, "(", ")")
        if end != len(text) - 1:
            break
        text = text[1:-1].strip()
    return text


def _m_reference_name(expression: str) -> str | None:
    text = expression.strip()
    if text.startswith('#"') and text.endswith('"'):
        return _decode_m_identifier(text)
    if re.fullmatch(r"[_\w]+", text, re.UNICODE):
        return text
    return None


def _m_root_call(expression: str) -> tuple[str, list[str]] | None:
    text = _mask_m_comments(expression).strip()
    match = re.match(r"^(#?[A-Za-z_][A-Za-z0-9_.]*)\s*\(", text)
    if not match:
        return None
    opening = text.find("(", match.start())
    closing = _matching_delimiter(text, opening, "(", ")")
    if closing is None or text[closing + 1 :].strip():
        return None
    return match.group(1), _split_m_top_level(text[opening + 1 : closing])


def _m_constant_expression(
    expression: str,
    bindings: dict[str, str],
    seen: frozenset[str] = frozenset(),
) -> str:
    text = expression.strip()
    reference = _m_reference_name(text)
    if not reference:
        return text
    key = reference
    if key not in bindings or key in seen:
        return text
    return _m_constant_expression(bindings[key], bindings, seen | {key})


def _m_constant_rows(
    expression: str,
    bindings: dict[str, str],
) -> list[list[str]] | None:
    text = _m_constant_expression(expression, bindings).strip()
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return None
    if not text[1:-1].strip():
        return []
    items = _split_m_top_level(text[1:-1])
    if len(items) >= 2 and _decode_m_string(items[0]) is not None:
        return [items]
    rows: list[list[str]] = []
    for item in items:
        resolved = _m_constant_expression(item, bindings).strip()
        if len(resolved) < 2 or resolved[0] != "{" or resolved[-1] != "}":
            return None
        nested = _m_constant_rows(resolved, bindings)
        if nested is None:
            return None
        rows.extend(nested)
    return rows


def _m_constant_strings(
    expression: str,
    bindings: dict[str, str],
) -> list[str] | None:
    text = _m_constant_expression(expression, bindings).strip()
    scalar = _decode_m_string(text)
    if scalar is not None:
        return [scalar]
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return None
    if not text[1:-1].strip():
        return []
    result: list[str] = []
    for item in _split_m_top_level(text[1:-1]):
        resolved = _m_constant_expression(item, bindings)
        value = _decode_m_string(resolved)
        if value is None:
            return None
        result.append(value)
    return result


def _m_constant_list(
    expression: str,
    bindings: dict[str, str],
) -> list[str] | None:
    text = _m_constant_expression(expression, bindings).strip()
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return None
    if not text[1:-1].strip():
        return []
    return _split_m_top_level(text[1:-1])


def _m_exact_table_type(expression: str) -> list[tuple[str, str]] | None:
    text = _strip_m_outer_parentheses(expression.strip())
    match = re.match(r"^type\s+table\s*\[", text)
    if not match:
        return None
    opening = match.end() - 1
    closing = _matching_delimiter(text, opening, "[", "]")
    if closing is None or text[closing + 1 :].strip():
        return None
    assignments: list[tuple[str, str]] = []
    for item in _split_m_top_level(text[opening + 1 : closing]):
        if not item:
            continue
        position = _m_top_level_equal_position(item)
        if position is None:
            return None
        column = _decode_m_identifier(item[:position])
        data_type = _m_type_expression(item[position + 1 :])
        if not column or not data_type:
            return None
        assignments.append((column, data_type))
    return assignments


def deduplicate_query_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        name = identifier_text(rget(row, "TableName", "Name")) or ""
        expression = query_expression(row) or ""
        key = (name, expression)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def query_expression(row: dict[str, Any]) -> str | None:
    for name in ("Expression", "QueryDefinition"):
        value = rget(row, name)
        if isinstance(value, float) and not math.isfinite(value):
            continue
        expression = text_or_none(value)
        if expression:
            return expression
    return None


def identifier_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


POWER_QUERY_CONNECTORS = {
    "sql.database": "SQL Server",
    "oracle.database": "Oracle",
    "postgresql.database": "PostgreSQL",
    "mysql.database": "MySQL",
    "odbc.datasource": "ODBC",
    "odbc.query": "ODBC",
    "oledb.datasource": "OLE DB",
    "snowflake.databases": "Snowflake",
    "teradata.database": "Teradata",
    "analysisservices.database": "Analysis Services",
    "powerbi.dataflows": "Power BI Dataflows",
    "powerplatform.dataflows": "Power Platform Dataflows",
    "commondataservice.database": "Dataverse",
    "amazonredshift.database": "Amazon Redshift",
    "amazonathena.databases": "Amazon Athena",
    "saphana.database": "SAP HANA",
    "googlebigquery.database": "Google BigQuery",
    "databricks.catalogs": "Databricks",
    "sapbusinesswarehouse.cubes": "SAP BW",
    "sharepoint.files": "SharePoint",
    "sharepoint.contents": "SharePoint",
    "azurestorage.blobs": "Azure Blob Storage",
    "azurestorage.datalake": "Azure Data Lake Storage",
    "web.contents": "Web",
    "excel.workbook": "Excel",
    "csv.document": "CSV",
    "json.document": "JSON",
    "folder.files": "Папка",
    "folder.contents": "Папка",
    "file.contents": "Файл",
}

POWER_QUERY_SOURCE_ARGUMENTS = {
    "sql.database": (("Сервер", 0), ("База", 1)),
    "oracle.database": (("Сервер / подключение", 0),),
    "postgresql.database": (("Сервер", 0), ("База", 1)),
    "mysql.database": (("Сервер", 0), ("База", 1)),
    "snowflake.databases": (("Сервер", 0), ("Хранилище", 1)),
    "teradata.database": (("Сервер", 0),),
    "analysisservices.database": (("Сервер", 0), ("База", 1)),
    "amazonredshift.database": (("Сервер", 0), ("База", 1)),
    "saphana.database": (("Сервер", 0),),
    "googlebigquery.database": (("Проект", 0),),
    "databricks.catalogs": (("Сервер", 0), ("HTTP path", 1)),
    "sharepoint.files": (("URL", 0),),
    "sharepoint.contents": (("URL", 0),),
    "azurestorage.blobs": (("Источник", 0),),
    "azurestorage.datalake": (("Источник", 0),),
    "web.contents": (("URL", 0),),
    "folder.files": (("Путь", 0),),
    "folder.contents": (("Путь", 0),),
    "file.contents": (("Путь", 0),),
}

POWER_QUERY_CONNECTION_ARGUMENTS = {
    "odbc.datasource": (0,),
    "odbc.query": (0,),
    "oledb.datasource": (0,),
}

SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?P<prefix>(?<![\w])[\"']?(?:password|pwd|passphrase|token|access[_ ]?token|"
    r"refresh[_ ]?token|client[_ ]?secret|api[_ ]?key|subscription[_ ]?key|"
    r"account[_ ]?key|private[_ ]?key|secret[_ ]?key|access[_ ]?key|"
    r"shared[_ ]?access[_ ]?signature|sas|sig|authorization)[\"']?"
    r"\s*(?:=|:)\s*)"
    r"(?P<value>\"(?:[^\"]|\"\")*\"|'[^']*'|[^;,&\]\}\)\r\n\"']+)",
)

URL_PASSWORD_PATTERN = re.compile(
    r"(?i)(https?://[^:/\s\"]+:)([^@/\s\"]+)(@)"
)


def extract_power_query_connection_types(expression: str) -> list[str]:
    if not expression:
        return []
    searchable = _mask_m_strings(_mask_m_comments(expression))
    functions = {
        match.group(1).casefold()
        for match in re.finditer(r"(?<![\w.])([A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+)\s*\(", searchable)
    }
    return sorted(
        {POWER_QUERY_CONNECTORS[name] for name in functions if name in POWER_QUERY_CONNECTORS},
        key=str.casefold,
    )


def extract_power_query_source_metadata(expression: str) -> tuple[list[str], list[str]]:
    if not expression:
        return [], []
    uncommented = _mask_m_comments(expression)
    details: list[str] = []
    connection_strings: list[str] = []
    for function_name, labels in POWER_QUERY_SOURCE_ARGUMENTS.items():
        for arguments in _function_call_arguments(uncommented, function_name):
            parts = []
            for label, index in labels:
                if index >= len(arguments):
                    continue
                value = _decode_m_string(arguments[index])
                if value is not None:
                    parts.append(f"{label}: {redact_connection_secrets(value)}")
            if parts:
                _append_unique(details, "; ".join(parts))
    for function_name, indexes in POWER_QUERY_CONNECTION_ARGUMENTS.items():
        for arguments in _function_call_arguments(uncommented, function_name):
            for index in indexes:
                if index >= len(arguments):
                    continue
                value = _decode_m_string(arguments[index])
                if value is not None:
                    _append_unique(
                        connection_strings,
                        redact_connection_secrets(value),
                    )
    return details, connection_strings


def redact_connection_secrets(value: str | None) -> str:
    text = str(value or "")

    def replace_assignment(match: re.Match[str]) -> str:
        original = match.group("value")
        if original.startswith('"'):
            replacement = '"<СКРЫТО>"'
        elif original.startswith("'"):
            replacement = "'<СКРЫТО>'"
        else:
            replacement = "<СКРЫТО>"
        return match.group("prefix") + replacement

    text = SECRET_ASSIGNMENT_PATTERN.sub(replace_assignment, text)
    return URL_PASSWORD_PATTERN.sub(r"\1<СКРЫТО>\3", text)


def extract_model_connections(rows: list[dict[str, Any]]) -> list[ConnectionInfo]:
    result: list[ConnectionInfo] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()
    for row in rows:
        connection_type = first_named_scalar(row, ("connectionType", "type", "kind"))
        details: list[str] = []
        for label, names in (
            ("Сервер", ("server", "serverName", "host", "hostname")),
            ("База", ("database", "databaseName", "initialCatalog", "catalog")),
            ("Путь", ("path", "filePath", "folderPath", "location")),
            ("URL", ("url", "uri", "endpoint")),
            ("Источник", ("dataSource", "datasource", "dsn")),
        ):
            for value in named_scalars(row, names):
                _append_unique(details, f"{label}: {redact_connection_secrets(value)}")
        strings = named_scalars(
            row,
            ("connectionString", "connection_string", "providerString"),
        )
        connection_string = (
            " | ".join(dict.fromkeys(redact_connection_secrets(value) for value in strings))
            if strings
            else None
        )
        key = (
            (connection_type or "").casefold(),
            tuple(details),
            connection_string or "",
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ConnectionInfo(connection_type, details, connection_string))
    return result


def named_scalars(node: Any, names: tuple[str, ...]) -> list[str]:
    wanted = {name.casefold() for name in names}
    result: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).casefold() in wanted and isinstance(value, (str, int, float)):
                _append_unique(result, str(value))
            if isinstance(value, (dict, list)):
                for nested in named_scalars(value, names):
                    _append_unique(result, nested)
    elif isinstance(node, list):
        for value in node:
            for nested in named_scalars(value, names):
                _append_unique(result, nested)
    return result


def first_named_scalar(node: Any, names: tuple[str, ...]) -> str | None:
    values = named_scalars(node, names)
    return values[0] if values else None


def extract_native_types(expression: str) -> dict[str, list[str]]:
    events: list[tuple[int, int, str, str, str | None]] = []
    sequence = 0

    def add_event(position: int, action: str, name: str, value: str | None = None) -> None:
        nonlocal sequence
        events.append((position, sequence, action, compact_metadata(name), value))
        sequence += 1

    cast_pattern = re.compile(
        r"\bCAST\s*\(.*?\bAS\s+"
        r"([A-Za-z][A-Za-z0-9_ ]*(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)\s*\)\s+AS\s+"
        r"(?:\[([^\]]+)\]|\"([^\"]+)\"|`([^`]+)`|([A-Za-z_][A-Za-z0-9_$]*))",
        re.IGNORECASE | re.DOTALL,
    )
    convert_pattern = re.compile(
        r"\bCONVERT\s*\(\s*"
        r"([A-Za-z][A-Za-z0-9_ ]*(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)\s*,.*?\)\s+AS\s+"
        r"(?:\[([^\]]+)\]|\"([^\"]+)\"|`([^`]+)`|([A-Za-z_][A-Za-z0-9_$]*))",
        re.IGNORECASE | re.DOTALL,
    )
    uncommented = _mask_m_comments(expression)
    native_sql: list[tuple[int, str]] = []
    for function_name, argument_index in (
        ("Value.NativeQuery", 1),
        ("Odbc.Query", 1),
    ):
        for position, arguments in _located_function_call_arguments(
            uncommented, function_name
        ):
            if len(arguments) <= argument_index:
                continue
            sql_text = _decode_m_string(arguments[argument_index])
            if sql_text is not None:
                native_sql.append((position, sql_text))
    native_sql.extend(_m_named_string_values(uncommented, ("Query", "SQLStatement")))

    for position, sql_text in sorted(native_sql):
        for pattern in (cast_pattern, convert_pattern):
            for match in pattern.finditer(sql_text):
                data_type = compact_metadata(match.group(1))
                alias = next((value for value in match.groups()[1:] if value), None)
                if alias and data_type:
                    add_event(position, "TYPE", alias, f"SQL: {data_type}")

    for position, arguments in _located_function_call_arguments(
        uncommented, "Table.TransformColumnTypes"
    ):
        if len(arguments) < 2:
            continue
        for row in _m_list_rows(arguments[1]):
            if len(row) < 2:
                continue
            column = _decode_m_string(row[0])
            data_type = _m_type_expression(row[1])
            if column and data_type:
                add_event(position, "TYPE", column, f"M: {data_type}")

    for position, arguments in _located_function_call_arguments(
        uncommented, "Table.TransformColumns"
    ):
        if len(arguments) < 2:
            continue
        for row in _m_list_rows(arguments[1]):
            if len(row) < 3:
                continue
            column = _decode_m_string(row[0])
            data_type = _m_type_expression(row[-1])
            if column and data_type:
                add_event(position, "TYPE", column, f"M: {data_type}")

    for position, arguments in _located_function_call_arguments(
        uncommented, "Table.AddColumn"
    ):
        if len(arguments) < 4:
            continue
        column = _decode_m_string(arguments[1])
        data_type = _m_type_expression(arguments[3])
        if column and data_type:
            add_event(position, "TYPE", column, f"M: {data_type}")

    for position, assignments in _m_table_type_assignments(uncommented):
        for column, data_type in assignments:
            add_event(position, "TYPE", column, f"M: {data_type}")

    for position, arguments in _located_function_call_arguments(
        uncommented, "Table.RenameColumns"
    ):
        if len(arguments) < 2:
            continue
        for row in _m_list_rows(arguments[1]):
            if len(row) < 2:
                continue
            source = _decode_m_string(row[0])
            target = _decode_m_string(row[1])
            if source and target:
                add_event(position, "RENAME", source, target)

    found: dict[str, tuple[str, str]] = {}
    for _, _, action, name, value in sorted(events):
        key = name.casefold()
        if action == "RENAME":
            existing = found.pop(key, None)
            if existing and value:
                found[compact_metadata(value).casefold()] = (compact_metadata(value), existing[1])
        elif value:
            found[key] = (name, value)
    return {name: [data_type] for name, data_type in found.values()}


def _m_list_rows(value: str) -> list[list[str]]:
    text = value.strip()
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return []
    rows: list[list[str]] = []
    for item in _split_m_top_level(text[1:-1]):
        item = item.strip()
        if len(item) >= 2 and item[0] == "{" and item[-1] == "}":
            rows.append(_split_m_top_level(item[1:-1]))
    return rows


def _m_type_expression(value: str) -> str | None:
    text = compact_metadata(value)
    if not text:
        return None
    pattern = re.compile(
        r"(?i)^(?:(?:type\s+)?nullable\s+|type\s+)?"
        r"(?:any|anynonnull|binary|date|datetime|datetimezone|duration|function|"
        r"list|logical|none|null|number|record|table|text|time|type|"
        r"[A-Za-z_][A-Za-z0-9_.]*\.Type)$"
    )
    return text if pattern.fullmatch(text) else None


def _m_table_type_assignments(expression: str) -> list[tuple[int, list[tuple[str, str]]]]:
    searchable = _mask_m_strings(expression)
    result: list[tuple[int, list[tuple[str, str]]]] = []
    pattern = re.compile(r"(?i)\btype\s+table\s*\[")
    for match in pattern.finditer(searchable):
        start = match.end() - 1
        end = _matching_delimiter(expression, start, "[", "]")
        if end is None:
            continue
        assignments: list[tuple[str, str]] = []
        for item in _split_m_top_level(expression[start + 1 : end]):
            parts = _split_m_top_level(item, delimiter="=")
            if len(parts) != 2:
                continue
            column = _decode_m_identifier(parts[0])
            data_type = _m_type_expression(parts[1])
            if column and data_type:
                assignments.append((column, data_type))
        if assignments:
            result.append((match.start(), assignments))
    return result


def _decode_m_identifier(value: str) -> str | None:
    text = value.strip()
    if text.startswith('#"') and text.endswith('"'):
        return text[2:-1].replace('""', '"') or None
    decoded = _decode_m_string(text)
    if decoded is not None:
        return decoded
    return compact_metadata(text) or None


def _m_named_string_values(
    expression: str,
    names: tuple[str, ...],
) -> list[tuple[int, str]]:
    searchable = _mask_m_strings(expression)
    pattern = re.compile(
        rf"(?i)(?<![\w])(?:{'|'.join(re.escape(name) for name in names)})\s*="
    )
    result: list[tuple[int, str]] = []
    for match in pattern.finditer(searchable):
        index = match.end()
        while index < len(expression) and expression[index].isspace():
            index += 1
        token = _m_string_token(expression, index)
        if token is not None:
            result.append((match.start(), token))
    return result


def _m_string_token(expression: str, start: int) -> str | None:
    if start >= len(expression) or expression[start] != '"':
        return None
    index = start + 1
    while index < len(expression):
        if expression[index] != '"':
            index += 1
            continue
        if index + 1 < len(expression) and expression[index + 1] == '"':
            index += 2
            continue
        return _decode_m_string(expression[start : index + 1])
    return None


def _matching_delimiter(
    expression: str,
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    depth = 0
    in_string = False
    index = start
    while index < len(expression):
        current = expression[index]
        following = expression[index + 1] if index + 1 < len(expression) else ""
        if in_string:
            if current == '"' and following == '"':
                index += 2
                continue
            if current == '"':
                in_string = False
        elif current == '"':
            in_string = True
        elif current == opening:
            depth += 1
        elif current == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_m_top_level(value: str, *, delimiter: str = ",") -> list[str]:
    items: list[str] = []
    start = 0
    parentheses = braces = brackets = nested_lets = 0
    in_string = False
    index = 0
    while index < len(value):
        current = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if in_string:
            if current == '"' and following == '"':
                index += 2
                continue
            if current == '"':
                in_string = False
        elif current == '"':
            in_string = True
        elif current == "(":
            parentheses += 1
        elif current == ")":
            parentheses = max(0, parentheses - 1)
        elif current == "{":
            braces += 1
        elif current == "}":
            braces = max(0, braces - 1)
        elif current == "[":
            brackets += 1
        elif current == "]":
            brackets = max(0, brackets - 1)
        elif parentheses == 0 and braces == 0 and brackets == 0 and (
            current.isalpha() or current == "_"
        ):
            end = index + 1
            while end < len(value) and (
                value[end].isalnum() or value[end] == "_"
            ):
                end += 1
            keyword = value[index:end]
            if keyword == "let":
                nested_lets += 1
            elif keyword == "in" and nested_lets:
                nested_lets -= 1
            index = end
            continue
        elif (
            current == delimiter
            and parentheses == 0
            and braces == 0
            and brackets == 0
            and nested_lets == 0
        ):
            items.append(value[start:index].strip())
            start = index + 1
        index += 1
    items.append(value[start:].strip())
    return items


def _mask_m_comments(expression: str) -> str:
    output: list[str] = []
    index = 0
    state = "CODE"
    while index < len(expression):
        current = expression[index]
        following = expression[index + 1] if index + 1 < len(expression) else ""
        if state == "STRING":
            output.append(current)
            if current == '"' and following == '"':
                output.append(following)
                index += 2
                continue
            if current == '"':
                state = "CODE"
        elif state == "LINE_COMMENT":
            output.append("\n" if current == "\n" else " ")
            if current == "\n":
                state = "CODE"
        elif state == "BLOCK_COMMENT":
            if current == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "CODE"
                continue
            output.append("\n" if current == "\n" else " ")
        elif current == '"':
            output.append(current)
            state = "STRING"
        elif current == "/" and following == "/":
            output.extend((" ", " "))
            index += 2
            state = "LINE_COMMENT"
            continue
        elif current == "/" and following == "*":
            output.extend((" ", " "))
            index += 2
            state = "BLOCK_COMMENT"
            continue
        else:
            output.append(current)
        index += 1
    return "".join(output)


def _function_call_arguments(expression: str, function_name: str) -> list[list[str]]:
    return [
        arguments
        for _, arguments in _located_function_call_arguments(expression, function_name)
    ]


def _located_function_call_arguments(
    expression: str,
    function_name: str,
) -> list[tuple[int, list[str]]]:
    calls: list[tuple[int, list[str]]] = []
    pattern = re.compile(rf"(?<![\w.]){re.escape(function_name)}\s*\(", re.IGNORECASE)
    searchable = _mask_m_strings(expression)
    for match in pattern.finditer(searchable):
        arguments: list[str] = []
        start = match.end()
        index = start
        parentheses = 1
        braces = 0
        brackets = 0
        in_string = False
        while index < len(expression):
            current = expression[index]
            following = expression[index + 1] if index + 1 < len(expression) else ""
            if in_string:
                if current == '"' and following == '"':
                    index += 2
                    continue
                if current == '"':
                    in_string = False
                index += 1
                continue
            if current == '"':
                in_string = True
            elif current == "(":
                parentheses += 1
            elif current == ")":
                parentheses -= 1
                if parentheses == 0:
                    arguments.append(expression[start:index].strip())
                    calls.append((match.start(), arguments))
                    break
            elif current == "{":
                braces += 1
            elif current == "}":
                braces = max(0, braces - 1)
            elif current == "[":
                brackets += 1
            elif current == "]":
                brackets = max(0, brackets - 1)
            elif current == "," and parentheses == 1 and braces == 0 and brackets == 0:
                arguments.append(expression[start:index].strip())
                start = index + 1
            index += 1
    return calls


def _mask_m_strings(expression: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    while index < len(expression):
        current = expression[index]
        following = expression[index + 1] if index + 1 < len(expression) else ""
        if not in_string:
            if current == '"':
                in_string = True
                output.append(" ")
            else:
                output.append(current)
            index += 1
            continue
        output.append("\n" if current == "\n" else " ")
        if current == '"' and following == '"':
            output.append(" ")
            index += 2
            continue
        if current == '"':
            in_string = False
        index += 1
    return "".join(output)


def _decode_m_string(argument: str) -> str | None:
    value = argument.strip()
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return None
    return value[1:-1].replace('""', '"')


def compact_metadata(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(char for char in text if ord(char) >= 32)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def safe_records(model: Any, property_name: str, issues: list[Issue]) -> list[dict[str, Any]]:
    try:
        return records(getattr(model, property_name))
    except Exception as error:
        issues.append(
            Issue(
                "WARNING",
                "MODEL",
                "MODEL_PROPERTY_UNAVAILABLE",
                f"{property_name}: {error}",
            )
        )
        return []


def optional_records(model: Any, property_name: str, issues: list[Issue]) -> list[dict[str, Any]]:
    try:
        value = getattr(model, property_name, None)
    except Exception as error:
        issues.append(
            Issue(
                "WARNING",
                "MODEL",
                "MODEL_PROPERTY_UNAVAILABLE",
                f"{property_name}: {error}",
            )
        )
        return []
    return records(value)


def profile_examples(
    model: Any,
    semantic: SemanticModelInfo,
    example_limit: int,
    issues: list[Issue],
) -> None:
    candidates: dict[str, list[ColumnInfo]] = defaultdict(list)
    for table in semantic.tables:
        if METADATA_EXCLUSION_POLICY.excludes(table.name):
            continue
        for column in table.columns:
            if table.hidden or column.hidden or is_sensitive_column(column.name):
                column.example_status = "IDENTIFIER_OR_HIDDEN"
                continue
            if column.cardinality is None or column.cardinality > 200:
                column.example_status = "HIGH_OR_UNKNOWN_CARDINALITY"
                continue
            data_type = (column.data_type or "").casefold()
            if any(token in data_type for token in ("date", "time")):
                column.example_status = "DATE_VALUES_HIDDEN"
                continue
            is_textual = any(token in data_type for token in ("str", "string", "object", "category", "bool"))
            if is_textual:
                candidates[table.name].append(column)
            else:
                column.example_status = "NUMERIC_VALUES_HIDDEN"

    for table_name, columns in candidates.items():
        counters = {column.name: Counter() for column in columns}
        try:
            iterator = model.iter_table(
                table_name,
                columns=[column.name for column in columns],
                chunk_size=250_000,
                strings_as_categorical=True,
            )
            for chunk in iterator:
                for column in columns:
                    counts = chunk[column.name].value_counts(dropna=True)
                    for value, count in counts.items():
                        safe = safe_example(value)
                        if safe:
                            counters[column.name][safe] += int(count)
            for column in columns:
                column.examples = [value for value, _ in counters[column.name].most_common(example_limit)]
                column.example_status = "OK" if column.examples else "NO_VALUES"
        except Exception as error:
            for column in columns:
                column.example_status = "PROFILE_FAILED"
            issues.append(
                Issue(
                    "WARNING",
                    "DATA_PROFILER",
                    "COLUMN_PROFILE_FAILED",
                    f"{table_name}: {error}",
                )
            )


def safe_example(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or ("@" in text and "." in text.rsplit("@", 1)[-1]):
        return None
    return text[:80] + ("…" if len(text) > 80 else "")


def is_sensitive_column(name: str) -> bool:
    folded = name.casefold()
    if any(part in folded for part in SENSITIVE_NAME_PARTS):
        return True
    tokens = [token for token in folded.replace("-", "_").split("_") if token]
    return any(token in {"id", "key"} or token.endswith("id") for token in tokens)


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def safe_error_message(error: Exception, *paths: Path | None) -> str:
    message = " ".join(str(error).splitlines())
    for path in paths:
        if path:
            message = re.sub(re.escape(str(path)), "<LOCAL_PATH>", message, flags=re.IGNORECASE)
    return message
