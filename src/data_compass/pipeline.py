from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Callable

from document_core.renderer import atomic_write, render_document

from .domain import ConversionResult
from .parser import parse_bundle
from .projector import project_document


Progress = Callable[[str], None]


class ConversionCancelled(Exception):
    pass


class OutputFailure(OSError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled = False
        self._publishing = False

    def cancel(self) -> bool:
        with self._lock:
            if self._publishing:
                return False
            self._cancelled = True
            return True

    def check(self) -> None:
        with self._lock:
            if self._cancelled:
                raise ConversionCancelled("Операция отменена")

    def begin_publication(self) -> None:
        with self._lock:
            if self._cancelled:
                raise ConversionCancelled("Операция отменена")
            self._publishing = True


def generate_documentation(
    input_path: Path,
    output_path: Path,
    progress: Progress | None = None,
    cancellation: CancellationToken | None = None,
) -> ConversionResult:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ValueError("Входной и выходной пути не должны совпадать")
    if output_path.suffix.casefold() != ".docx":
        raise ValueError("Выходной файл должен иметь расширение .docx")
    if not output_path.parent.is_dir():
        raise OutputFailure("Каталог выходного файла не существует")
    cancellation = cancellation or CancellationToken()
    cancellation.check()
    notify = progress or (lambda _: None)
    notify("Проверка ZIP и чтение metadata")
    model = parse_bundle(input_path)
    cancellation.check()
    notify("Формирование документа")
    payload = render_document(project_document(model))
    cancellation.check()
    try:
        atomic_write(
            output_path,
            payload,
            prefix=".data_compass_output_",
            before_commit=cancellation.begin_publication,
        )
    except (OSError, ValueError) as error:
        if isinstance(error, ConversionCancelled):
            raise
        raise OutputFailure("Не удалось безопасно сохранить DOCX") from error
    notify("Готово")
    return ConversionResult(model.status, model.issues)
