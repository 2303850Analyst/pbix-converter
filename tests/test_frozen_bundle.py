from __future__ import annotations

import unittest
from unittest.mock import patch

from pbix_doc.frozen import main, verify_bundled_dependencies
from pbix_doc.runtime import RuntimePolicyViolation


class FrozenBundleTests(unittest.TestCase):
    def test_dependency_self_check_covers_native_and_document_components(self) -> None:
        verify_bundled_dependencies()

    def test_runtime_policy_failure_during_self_check_is_reported_as_policy_exit(self) -> None:
        with patch(
            "pbix_doc.frozen.verify_bundled_dependencies",
            side_effect=RuntimePolicyViolation("blocked"),
        ):
            self.assertEqual(main(["--self-check"]), 4)


if __name__ == "__main__":
    unittest.main()
