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
    power_query_rows = safe_records(model, "power_query", issues)
    native_types = native_query_column_types(power_query_rows)

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
        source_column = text_or_none(rget(row, "SourceColumn", "SourceName"))
        native_type = native_types.get((table_name.casefold(), (source_column or name).casefold()))
        if native_type is None and source_column:
            native_type = native_types.get((table_name.casefold(), name.casefold()))
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
                summarize_by=text_or_none(rget(row, "SummarizeBy", "DefaultSummarization")),
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
                text_or_none(rget(row, "DataType", "Type")),
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
    for row in power_query_rows:
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


def model_data_type(row: dict[str, Any], schema: dict[str, Any]) -> str | None:
    raw = rget(row, "DataType", "Type")
    if raw is not None and not isinstance(raw, (int, float)):
        text = text_or_none(raw)
        if text and not text.isdigit():
            return text
    return text_or_none(
        rget(schema, "SemanticType", "DataType", "PandasDataType")
        or raw
    )


def native_query_column_types(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for row in rows:
        table = text_or_none(rget(row, "TableName", "Name"))
        expression = text_or_none(rget(row, "Expression"))
        if not table or not expression:
            continue
        for column, values in extract_native_types(expression).items():
            result[(table.casefold(), column.casefold())] = "; ".join(values)
    return result


def extract_native_types(expression: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
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
    for arguments in _function_call_arguments(uncommented, "Value.NativeQuery"):
        if len(arguments) < 2:
            continue
        sql_text = _decode_m_string(arguments[1])
        if sql_text is None:
            continue
        for pattern in (cast_pattern, convert_pattern):
            for match in pattern.finditer(sql_text):
                data_type = compact_metadata(match.group(1))
                alias = next((value for value in match.groups()[1:] if value), None)
                if alias and data_type:
                    _append_unique(found[compact_metadata(alias)], f"SQL: {data_type}")

    m_type_pattern = re.compile(
        r'\{\s*"((?:[^\"]|\"\")+)"\s*,\s*'
        r'((?:type\s+(?:nullable\s+)?)?[A-Za-z_][A-Za-z0-9_.]*)\s*\}',
        re.IGNORECASE,
    )
    for arguments in _function_call_arguments(uncommented, "Table.TransformColumnTypes"):
        if len(arguments) < 2:
            continue
        for match in m_type_pattern.finditer(arguments[1]):
            column = compact_metadata(match.group(1).replace('""', '"'))
            data_type = compact_metadata(match.group(2))
            if column and data_type:
                _append_unique(found[column], f"M: {data_type}")
    return dict(found)


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
    calls: list[list[str]] = []
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
                    calls.append(arguments)
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
