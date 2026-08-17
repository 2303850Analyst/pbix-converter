from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from document_core.model import HeadingBlock, SafeDocumentModel, TableBlock
from document_core.ooxml import validate_docx
from document_core.ooxml import UnsafeDocxError
from document_core.renderer import atomic_write, render_document


class DocumentCoreContractTests(unittest.TestCase):
    def test_atomic_publication_failure_preserves_output_and_removes_temporary_file(self) -> None:
        model = SafeDocumentModel(
            title="Safe",
            subtitle="Test",
            source_name="synthetic.zip",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            overall_status="OK",
            product_name="Converter",
            product_subject="Test",
            blocks=[HeadingBlock(1, "Test")],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "result.docx")
            output.write_bytes(b"previous")
            with patch("document_core.renderer.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    atomic_write(output, render_document(model), prefix=".test_output_")
            self.assertEqual(output.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).glob(".test_output_*.tmp")), [])

    def test_ooxml_rejects_normalized_traversal_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "unsafe.docx")
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("word/document.xml", "<document/>")
                archive.writestr("word/../evil.xml", "<evil/>")
            with self.assertRaises(UnsafeDocxError):
                validate_docx(output)

    def test_neutral_model_renders_and_publishes_valid_landscape_docx(self) -> None:
        model = SafeDocumentModel(
            title="Data Compass",
            subtitle="Документация дашборда",
            source_name="synthetic.zip",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            overall_status="OK",
            product_name="Data Compass Converter",
            product_subject="Локальная документация Data Compass",
            blocks=[
                HeadingBlock(1, "Об отчёте"),
                TableBlock(("Параметр", "Значение"), [("Формат", "Superset")], (4_680, 4_680)),
            ],
        )

        payload = render_document(model)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "result.docx")
            atomic_write(output, payload)
            validate_docx(output)
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn('w:tblInd w:w="120" w:type="dxa"', xml)
            self.assertIn('w:tcMar', xml)


if __name__ == "__main__":
    unittest.main()
