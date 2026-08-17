from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_compass.cli import main
from data_compass.gui import status_presentation
from tests.test_data_compass_converter import write_bundle
from tests.test_data_compass_security import rewrite_member


class DataCompassCliTests(unittest.TestCase):
    def test_gui_presents_ok_partial_and_failed_distinctly(self) -> None:
        self.assertEqual(status_presentation("OK")[0], "info")
        self.assertEqual(status_presentation("PARTIAL")[0], "warning")
        self.assertEqual(status_presentation("FAILED")[0], "error")

    def test_cli_returns_ok_partial_input_and_output_codes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.zip"
            write_bundle(source)
            self.assertEqual(main([str(source), str(root / "ok.docx")]), 0)

            partial = root / "partial.zip"
            write_bundle(partial)
            rewrite_member(
                partial,
                "charts/1.yaml",
                lambda payload: payload.replace(b"viz_type: bar", b"viz_type: unknown_plugin"),
            )
            self.assertEqual(main([str(partial), str(root / "partial.docx")]), 2)

            self.assertEqual(main([str(root / "missing.zip"), str(root / "missing.docx")]), 1)
            self.assertEqual(main([str(source), str(root / "missing" / "output.docx")]), 3)


if __name__ == "__main__":
    unittest.main()
