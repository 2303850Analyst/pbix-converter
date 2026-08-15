from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path(SPECPATH).resolve().parent
datas = (
    collect_data_files("docx")
    + collect_data_files("tzdata")
    + copy_metadata("pbixray")
)
hiddenimports = collect_submodules("pbixray") + [
    "apsw",
    "xmhuffman",
    "xpress8",
    "xpress9",
]

analysis = Analysis(
    [str(project_root / "exe_main.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="PBIXConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
