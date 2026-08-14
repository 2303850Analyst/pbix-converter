from __future__ import annotations

import unittest

from pbix_doc.frozen import verify_bundled_dependencies


class FrozenBundleTests(unittest.TestCase):
    def test_dependency_self_check_covers_native_and_document_components(self) -> None:
        verify_bundled_dependencies()


if __name__ == "__main__":
    unittest.main()
