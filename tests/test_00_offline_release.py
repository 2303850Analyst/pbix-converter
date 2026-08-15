from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_FILES = (
    "scripts/common.ps1",
    "scripts/build_offline_release.ps1",
    "scripts/build_windows_exe.ps1",
    "scripts/install_offline.ps1",
    "scripts/setup_offline.ps1",
    "scripts/run_pbix_doc.ps1",
    "scripts/start_pbix_doc.cmd",
)


class OfflineReleaseContractTests(unittest.TestCase):
    def test_launcher_scripts_are_ascii_for_windows_powershell_51(self) -> None:
        for relative in SCRIPT_FILES:
            payload = ROOT.joinpath(relative).read_bytes()
            self.assertTrue(all(byte < 128 for byte in payload), relative)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell contract")
    def test_powershell_scripts_parse_in_windows_powershell_51(self) -> None:
        for relative in SCRIPT_FILES[:-1]:
            path = str(ROOT.joinpath(relative).resolve()).replace("'", "''")
            command = (
                "$tokens=$null;$errors=$null;"
                f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',"
                "[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, f"{relative}: {result.stdout}{result.stderr}")

    def test_bundle_is_targeted_and_installs_without_an_index(self) -> None:
        build = ROOT.joinpath("scripts/build_offline_release.ps1").read_text(encoding="ascii")
        install = ROOT.joinpath("scripts/install_offline.ps1").read_text(encoding="ascii")
        setup = ROOT.joinpath("scripts/setup_offline.ps1").read_text(encoding="ascii")

        for option in ("--platform", "--implementation", "--python-version", "--abi"):
            self.assertIn(option, build)
        self.assertIn('[string]$TargetPythonVersion = "3.12"', build)
        self.assertIn('$targetPlatform = "win_amd64"', build)
        self.assertIn('$env:PIP_NO_INDEX = "1"', install)
        self.assertGreaterEqual(install.count("--no-index"), 2)
        self.assertLess(install.index("$actualRuntime -ne $targetRuntime"), install.index("pip --isolated"))
        self.assertLess(setup.index("-CheckRuntimeOnly"), setup.index("-m venv"))

    def test_exe_builder_embeds_dependencies_without_runtime_downloads(self) -> None:
        build = ROOT.joinpath("scripts/build_windows_exe.ps1").read_text(encoding="ascii")
        spec = ROOT.joinpath("packaging/PBIXConverter.spec").read_text(encoding="utf-8")
        one_file_spec = ROOT.joinpath("packaging/PBIXConverterOneFile.spec").read_text(encoding="utf-8")

        self.assertIn("requirements-exe-build.lock", build)
        self.assertIn("PyInstaller", build)
        self.assertIn('console=False', spec)
        self.assertIn('copy_metadata("pbixray")', spec)
        self.assertIn("analysis.binaries", one_file_spec)
        self.assertIn("analysis.datas", one_file_spec)
        self.assertNotIn("COLLECT(", one_file_spec)

    def test_root_executable_is_a_single_file_windows_binary(self) -> None:
        executable = ROOT.joinpath("PBIXConverter.exe")

        self.assertTrue(executable.is_file())
        with executable.open("rb") as stream:
            self.assertEqual(stream.read(2), b"MZ")
        self.assertLess(executable.stat().st_size, 100_000_000)


if __name__ == "__main__":
    unittest.main()
