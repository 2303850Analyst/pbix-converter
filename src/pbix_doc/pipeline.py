from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__
from .container import PbixContainer
from .domain import DocumentationModel, FileInfo, Issue, SemanticModelInfo
from .report import parse_report
from .runtime import install_offline_guard, managed_workspace
from .semantic import load_semantic_model


ProgressCallback = Callable[[str], None]
MAX_EXAMPLE_VALUES = 5


@dataclass(slots=True)
class Options:
    example_limit: int = 5


def analyze_pbix(
    input_path: Path,
    options: Options | None = None,
    progress: ProgressCallback | None = None,
    *,
    workspace_parent: Path | None = None,
) -> DocumentationModel:
    install_offline_guard()
    options = options or Options()
    input_path = input_path.resolve()
    if input_path.suffix.casefold() != ".pbix":
        raise ValueError("Входной файл должен иметь расширение .pbix")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    notify = progress or (lambda _: None)
    notify("Проверка входного файла")
    stat = input_path.stat()
    file_info = FileInfo(
        input_path,
        stat.st_size,
        file_sha256(input_path),
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )
    issues: list[Issue] = []

    notify("Чтение структуры отчёта")
    with PbixContainer(input_path) as container:
        report = parse_report(container, input_path.stem, issues)

    notify("Чтение семантической модели")
    workspace_parent = (workspace_parent or input_path.parent).resolve()
    with managed_workspace(workspace_parent) as temp_path:
        semantic = load_semantic_model(
            input_path,
            temp_path,
            min(MAX_EXAMPLE_VALUES, max(0, options.example_limit)),
            issues,
        )

    notify("Сопоставление полей")
    resolve_fields(report.pages, semantic, issues)
    validate_model(report, semantic, issues)
    notify("Подготовка документа")
    return DocumentationModel(
        file_info,
        report,
        semantic,
        issues,
        datetime.now(timezone.utc),
        __version__,
    )


def generate_documentation(
    input_path: Path,
    output_path: Path,
    options: Options | None = None,
    progress: ProgressCallback | None = None,
) -> DocumentationModel:
    from .renderer import atomic_write, render_document

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ValueError("Входной и выходной пути не должны совпадать")
    if output_path.suffix.casefold() != ".txt":
        raise ValueError("Выходной файл должен иметь расширение .txt")
    if not output_path.parent.is_dir():
        raise FileNotFoundError(output_path.parent)
    model = analyze_pbix(
        input_path,
        options,
        progress,
        workspace_parent=output_path.parent,
    )
    if progress:
        progress("Запись TXT")
    atomic_write(output_path, render_document(model))
    if progress:
        progress("Готово")
    return model


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_fields(pages: list, semantic: SemanticModelInfo, issues: list[Issue]) -> None:
    column_index: dict[tuple[str, str], tuple[str, str]] = {}
    column_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for table in semantic.tables:
        for column in table.columns:
            key = (table.name.casefold(), column.name.casefold())
            value = (table.name, column.name)
            column_index[key] = value
            column_by_name[column.name.casefold()].append(value)

    measure_index: dict[tuple[str, str], tuple[str, str]] = {}
    measure_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for measure in semantic.measures:
        key = (measure.table.casefold(), measure.name.casefold())
        value = (measure.table, measure.name)
        measure_index[key] = value
        measure_by_name[measure.name.casefold()].append(value)

    unresolved = 0
    for page in pages:
        for visual in page.visuals:
            for field in visual.fields:
                exact_key = ((field.table or "").casefold(), field.name.casefold())
                matched: tuple[str, str] | None = None
                object_type = field.object_type
                if object_type == "MEASURE":
                    matched = measure_index.get(exact_key)
                elif object_type in {"COLUMN", "HIERARCHY", "HIERARCHY_LEVEL"}:
                    matched = column_index.get(exact_key)
                else:
                    matched = measure_index.get(exact_key)
                    if matched:
                        object_type = "MEASURE"
                    else:
                        matched = column_index.get(exact_key)
                        if matched:
                            object_type = "COLUMN"

                if not matched:
                    measure_candidates = measure_by_name.get(field.name.casefold(), [])
                    column_candidates = column_by_name.get(field.name.casefold(), [])
                    candidates = measure_candidates + column_candidates
                    if len(candidates) == 1:
                        matched = candidates[0]
                        object_type = "MEASURE" if measure_candidates else "COLUMN"

                if matched:
                    field.table, field.name = matched
                    field.object_type = object_type
                    field.status = "RESOLVED"
                    field.reason = None
                else:
                    unresolved += 1
                    if semantic.status != "EMBEDDED":
                        field.reason = f"Локальная модель недоступна: {semantic.status}"
                    elif not field.reason:
                        field.reason = "Объект не найден в извлечённой семантической модели"

    if unresolved:
        issues.append(
            Issue(
                "WARNING",
                "FIELD_RESOLVER",
                "UNRESOLVED_REFERENCES",
                f"Не разрешено ссылок на поля: {unresolved}",
            )
        )


def validate_model(report, semantic: SemanticModelInfo, issues: list[Issue]) -> None:
    page_ids = [page.id for page in report.pages]
    if len(page_ids) != len(set(page_ids)):
        issues.append(Issue("ERROR", "VALIDATION", "DUPLICATE_PAGE_ID", "Обнаружены повторяющиеся ID листов"))
    for page in report.pages:
        visual_ids = [visual.id for visual in page.visuals]
        if len(visual_ids) != len(set(visual_ids)):
            issues.append(
                Issue(
                    "ERROR",
                    "VALIDATION",
                    "DUPLICATE_VISUAL_ID",
                    f"Повторяющиеся ID визуалов на листе {page.name}",
                    page_id=page.id,
                )
            )
    missing_targets = sum(edge.status == "UNRESOLVED" for edge in report.navigation)
    if missing_targets:
        issues.append(
            Issue(
                "WARNING",
                "VALIDATION",
                "UNRESOLVED_NAVIGATION_TARGETS",
                f"Неразрешённых навигационных целей: {missing_targets}",
            )
        )
    if not report.pages:
        issues.append(Issue("ERROR", "VALIDATION", "NO_PAGES", "Не извлечено ни одного листа"))
    if semantic.status == "FAILED":
        issues.append(Issue("WARNING", "VALIDATION", "MODEL_PARTIAL", "Документ будет сформирован без модели"))
