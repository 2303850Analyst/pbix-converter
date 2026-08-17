from __future__ import annotations

import unittest
from unittest.mock import patch

from data_compass.frozen import main, verify_bundled_dependencies
from pbix_doc.runtime import RuntimePolicyViolation


class DataCompassFrozenTests(unittest.TestCase):
    def test_dependency_self_check_covers_yaml_docx_and_tk(self) -> None:
        verify_bundled_dependencies()
        self.assertEqual(main(["--self-check"]), 0)

    def test_self_check_has_distinct_policy_and_broken_bundle_exit_codes(self) -> None:
        with patch("data_compass.frozen.verify_bundled_dependencies", side_effect=RuntimePolicyViolation("blocked")):
            self.assertEqual(main(["--self-check"]), 4)
        with patch("data_compass.frozen.verify_bundled_dependencies", side_effect=ImportError("broken")):
            self.assertEqual(main(["--self-check"]), 5)


if __name__ == "__main__":
    unittest.main()
