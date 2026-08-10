from __future__ import annotations

import re
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .domain import (
    ColumnInfo,
    Issue,
    MeasureInfo,
    QueryInfo,
    RelationshipInfo,
    SecurityRule,
    SemanticModelInfo,
    TableInfo,
)


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
        result.connection_types = sorted(
            {
                str(item.get("connectionType") or item.get("ConnectionType") or "LIVE")
                for item in getattr(error, "connections", [])
                if isinstance(item, dict)
            }
        )
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
        table.columns.append(
            ColumnInfo(
                table_name,
                name,
                text_or_none(rget(schema, "PandasDataType", "DataType") or rget(row, "DataType")),
                bool_value(rget(row, "IsHidden", "Hidden")),
                int(cardinality) if cardinality is not None else None,
                text_or_none(rget(calculated, "Expression")),
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

    queries: list[QueryInfo] = []
    for row in safe_records(model, "power_query", issues):
        name = text_or_none(rget(row, "TableName", "Name"))
        if name:
            expression = text_or_none(rget(row, "Expression"))
            queries.append(QueryInfo(name, "POWER_QUERY", bool(expression)))

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

    connection_types = sorted(
        {
            str(item.get("connectionType") or item.get("ConnectionType") or "UNKNOWN")
            for item in getattr(model, "connections", [])
            if isinstance(item, dict)
        }
    )
    return SemanticModelInfo(
        "EMBEDDED",
        sorted(table_map.values(), key=lambda item: item.name.casefold()),
        sorted(measures, key=lambda item: (item.table.casefold(), item.name.casefold())),
        relationships,
        queries,
        rls,
        ols,
        connection_types,
        package_version(),
    )


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


def profile_examples(
    model: Any,
    semantic: SemanticModelInfo,
    example_limit: int,
    issues: list[Issue],
) -> None:
    candidates: dict[str, list[ColumnInfo]] = defaultdict(list)
    for table in semantic.tables:
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
