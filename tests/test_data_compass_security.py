from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

from data_compass.container import CompassBundle, UnsafeBundleError, _validate_member, load_yaml
from data_compass.pipeline import generate_documentation
from data_compass.pipeline import CancellationToken, ConversionCancelled
from document_core.ooxml import validate_docx
from tests.test_data_compass_converter import docx_text, write_bundle, write_representative_bundle


def rewrite_member(path: Path, suffix: str, transform) -> None:
    with zipfile.ZipFile(path) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    name = next(item for item in files if item.endswith(suffix))
    files[name] = transform(files[name])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item, payload in files.items():
            archive.writestr(item, payload)


class DataCompassSecurityTests(unittest.TestCase):
    def test_absolute_paths_encryption_and_data_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, name in enumerate(("/metadata.yaml", "C:/metadata.yaml")):
                source = root / f"absolute-{index}.zip"
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr(name, "version: 1.0.0\n")
                with self.assertRaises(UnsafeBundleError):
                    with CompassBundle(source):
                        pass

            unsupported = root / "unsupported.zip"
            with zipfile.ZipFile(unsupported, "w") as archive:
                archive.writestr("metadata.yaml", "version: 1.0.0\n")
                archive.writestr("rows.csv", "secret,value\n")
            with self.assertRaises(UnsafeBundleError):
                with CompassBundle(unsupported):
                    pass

            encrypted = zipfile.ZipInfo("metadata.yaml")
            encrypted.flag_bits |= 0x1
            with self.assertRaises(UnsafeBundleError):
                _validate_member(encrypted, encrypted.filename)

    def test_member_and_total_limits_are_enforced_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "limits.zip")
            write_bundle(source)
            with patch("data_compass.container.MAX_TOTAL_BYTES", 1):
                with self.assertRaises(UnsafeBundleError):
                    with CompassBundle(source):
                        pass
            with patch("data_compass.container.MAX_MEMBER_BYTES", 1):
                with self.assertRaises(UnsafeBundleError):
                    with CompassBundle(source):
                        pass
            with patch("data_compass.container.MAX_MEMBERS", 1):
                with self.assertRaises(UnsafeBundleError):
                    with CompassBundle(source):
                        pass

    def test_yaml_node_and_string_limits_are_enforced(self) -> None:
        with patch("data_compass.container.MAX_NODES", 2):
            with self.assertRaises(UnsafeBundleError):
                load_yaml(b"a: 1\nb: 2\n", "metadata.yaml")
        with patch("data_compass.container.MAX_STRING", 3):
            with self.assertRaises(UnsafeBundleError):
                load_yaml(b"value: long\n", "metadata.yaml")

    def test_long_expression_and_active_markdown_are_safely_shortened(self) -> None:
        canary = "ACTIVE-MARKDOWN-CANARY"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "long-content.zip")
            output = Path(directory, "output.docx")
            write_representative_bundle(source)

            def long_metric(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                data["metrics"][0]["expression"] = "SUM(" + "x" * 8_100 + ")"
                return yaml.safe_dump(data, allow_unicode=True).encode()

            def active_markdown(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                layout = __import__("json").loads(data["position_json"])
                layout["MD-A"]["meta"]["text"] = f"<script>{canary}</script>" + "текст " * 1_000
                data["position_json"] = __import__("json").dumps(layout, ensure_ascii=False)
                return yaml.safe_dump(data, allow_unicode=True).encode()

            rewrite_member(source, "datasets/core/sales.yaml", long_metric)
            rewrite_member(source, "dashboards/main.yaml", active_markdown)
            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            text = docx_text(output)
            self.assertNotIn(canary, text)
            self.assertIn("PARTIAL", text)

    def test_malformed_or_overdeep_optional_json_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed.zip"
            write_bundle(malformed)
            rewrite_member(
                malformed,
                "charts/1.yaml",
                lambda payload: payload.replace(b"params:\n", b"params: '{broken'\nignored:\n"),
            )
            result = generate_documentation(malformed, root / "malformed.docx")
            self.assertEqual(result.status, "PARTIAL")

            deep = root / "deep.zip"
            write_bundle(deep)
            nested: object = "leaf"
            for _ in range(55):
                nested = {"nested": nested}

            def inject_deep(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                data["params"] = __import__("json").dumps(nested)
                return yaml.safe_dump(data).encode()

            rewrite_member(deep, "charts/1.yaml", inject_deep)
            result = generate_documentation(deep, root / "deep.docx")
            self.assertEqual(result.status, "PARTIAL")
            self.assertTrue(any(issue.code == "MALFORMED_OPTIONAL_METADATA" for issue in result.issues))

    def test_extended_credential_patterns_are_redacted(self) -> None:
        canaries = {
            "client_secret": "client_secret=CLIENT-SECRET-CANARY",
            "authorization": "Bearer BEARER-CANARY",
            "private_key": "-----BEGIN PRIVATE KEY----- PRIVATE-KEY-CANARY -----END PRIVATE KEY-----",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            write_bundle(source)

            def inject(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                data["metrics"] = [
                    {"metric_name": key, "verbose_name": key, "expression": value}
                    for key, value in canaries.items()
                ]
                return yaml.safe_dump(data, allow_unicode=True).encode()

            rewrite_member(source, "datasets/core/sales.yaml", inject)
            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            text = docx_text(output)
            for canary in ("CLIENT-SECRET-CANARY", "BEARER-CANARY", "PRIVATE-KEY-CANARY"):
                self.assertNotIn(canary, text)

    def test_preflight_rejects_case_duplicates_symlinks_and_compression_bombs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "duplicate-path.zip"
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("bundle/metadata.yaml", "version: 1.0.0")
                archive.writestr("bundle/METADATA.yaml", "version: 1.0.0")
            with self.assertRaises(UnsafeBundleError):
                with CompassBundle(duplicate):
                    pass

            symlink = root / "symlink.zip"
            with zipfile.ZipFile(symlink, "w") as archive:
                info = zipfile.ZipInfo("metadata.yaml")
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, "target")
            with self.assertRaises(UnsafeBundleError):
                with CompassBundle(symlink):
                    pass

            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("metadata.yaml", b"0" * (1024 * 1024))
            with self.assertRaises(UnsafeBundleError):
                with CompassBundle(bomb):
                    pass

    def test_yaml_aliases_and_custom_tags_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, payload in enumerate(
                (
                    b"version: &v 1.0.0\ntype: *v\n",
                    b"version: !!python/object/apply:os.system ['whoami']\n",
                )
            ):
                source = root / f"unsafe-{index}.zip"
                write_bundle(source)
                rewrite_member(source, "metadata.yaml", lambda _: payload)
                with self.assertRaises(UnsafeBundleError):
                    generate_documentation(source, root / f"output-{index}.docx")

    def test_yaml_anchor_without_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "anchor.zip")
            write_bundle(source)
            rewrite_member(source, "metadata.yaml", lambda _: b"version: &version 1.0.0\n")
            with self.assertRaises(UnsafeBundleError):
                generate_documentation(source, Path(directory, "output.docx"))

    def test_unknown_yaml_extension_is_partial_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "extension.zip")
            output = Path(directory, "output.docx")
            write_bundle(source)
            with zipfile.ZipFile(source) as archive:
                files = {info.filename: archive.read(info) for info in archive.infolist()}
            files["export/extensions/corporate.yaml"] = b"kind: corporate_widget\n"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            validate_docx(output)

    def test_cancellation_before_atomic_commit_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            output.write_bytes(b"previous")
            write_bundle(source)
            token = CancellationToken()

            def cancel_before_commit(path: Path) -> None:
                validate_docx(path)
                self.assertTrue(token.cancel())

            with patch("document_core.renderer.validate_docx", side_effect=cancel_before_commit):
                with self.assertRaises(ConversionCancelled):
                    generate_documentation(source, output, cancellation=token)

            self.assertEqual(output.read_bytes(), b"previous")

    def test_zip_traversal_and_duplicate_yaml_keys_are_rejected_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output.docx"
            output.write_bytes(b"previous")
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("../metadata.yaml", "version: 1.0.0")
            with self.assertRaises(UnsafeBundleError):
                generate_documentation(traversal, output)
            self.assertEqual(output.read_bytes(), b"previous")

            duplicate = root / "duplicate.zip"
            write_bundle(duplicate)
            rewrite_member(duplicate, "metadata.yaml", lambda _: b"version: 1.0.0\nversion: 1.0.0\n")
            with self.assertRaises(UnsafeBundleError):
                generate_documentation(duplicate, output)
            self.assertEqual(output.read_bytes(), b"previous")

    def test_credentials_and_user_identities_never_reach_docx(self) -> None:
        canary = "SECRET-CANARY-937"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            write_representative_bundle(source)

            def inject(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                data["sqlalchemy_uri"] = f"postgresql://alice:{canary}@db.local/warehouse"
                data["encrypted_extra"] = {"api_token": canary}
                data["owners"] = [f"alice-{canary}"]
                return yaml.safe_dump(data, allow_unicode=True).encode()

            rewrite_member(source, "databases/core.yaml", inject)
            result = generate_documentation(source, output)

            self.assertEqual(result.status, "OK")
            self.assertNotIn(canary, docx_text(output))

    def test_unknown_version_and_visualization_are_partial_but_publish_meaningful_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            write_bundle(source)
            rewrite_member(
                source,
                "metadata.yaml",
                lambda payload: payload.replace(b"1.0.0", b"9.9.9"),
            )
            rewrite_member(
                source,
                "charts/1.yaml",
                lambda payload: payload.replace(b"viz_type: bar", b"viz_type: corporate_plugin"),
            )

            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            self.assertTrue(output.is_file())
            self.assertIn("corporate_plugin", docx_text(output))

    def test_url_userinfo_query_and_fragment_are_removed_and_mark_partial(self) -> None:
        canary = "URL-CANARY-118"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            write_representative_bundle(source)

            def inject(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                metadata = __import__("json").loads(data["json_metadata"])
                metadata["interactions"][0]["url"] = f"https://alice:password@example.test/dashboard?token={canary}#details"
                data["json_metadata"] = __import__("json").dumps(metadata, ensure_ascii=False)
                return yaml.safe_dump(data, allow_unicode=True).encode()

            rewrite_member(source, "dashboards/main.yaml", inject)
            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            text = docx_text(output)
            self.assertIn("https://example.test/dashboard", text)
            self.assertNotIn(canary, text)
            self.assertNotIn("alice:password", text)

    def test_unsafe_metric_expression_is_hidden_and_marks_result_partial(self) -> None:
        canary = "PASSWORD-CANARY-444"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "synthetic.zip")
            output = Path(directory, "output.docx")
            write_bundle(source)

            def inject(payload: bytes) -> bytes:
                data = yaml.safe_load(payload)
                data["metrics"][0]["expression"] = f"Password={canary}"
                return yaml.safe_dump(data, allow_unicode=True).encode()

            rewrite_member(source, "datasets/core/sales.yaml", inject)
            result = generate_documentation(source, output)

            self.assertEqual(result.status, "PARTIAL")
            text = docx_text(output)
            self.assertNotIn(canary, text)
            self.assertIn("СКРЫТО", text)


if __name__ == "__main__":
    unittest.main()
