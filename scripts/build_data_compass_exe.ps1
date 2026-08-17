[CmdletBinding()]
param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtime = (& $Python -c "import platform,struct,sys; print('|'.join((sys.platform,platform.python_implementation(),str(struct.calcsize('P')*8),platform.machine().lower(),f'{sys.version_info.major}.{sys.version_info.minor}')))").Trim()
if ($LASTEXITCODE -ne 0 -or $runtime -notmatch '^win32\|CPython\|64\|(amd64|x86_64)\|3\.(11|12|13)$') {
    throw "Build requires 64-bit CPython 3.11-3.13 for Windows"
}

$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ("data-compass-exe-" + [guid]::NewGuid().ToString("N"))
$wheelhouse = Join-Path $buildRoot "wheelhouse"
$environment = Join-Path $buildRoot "venv"
$dist = Join-Path $buildRoot "dist"
$work = Join-Path $buildRoot "work"
New-Item -ItemType Directory -Path $wheelhouse | Out-Null
try {
    & $Python -m pip --isolated --disable-pip-version-check --quiet download --require-hashes --only-binary=:all: --dest $wheelhouse --requirement (Join-Path $projectRoot "requirements-data-compass.lock") --requirement (Join-Path $projectRoot "requirements-exe-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to download verified build dependencies" }
    & $Python -m venv $environment
    $buildPython = Join-Path $environment "Scripts\python.exe"
    & $buildPython -m pip --isolated --disable-pip-version-check --quiet install --no-index --only-binary=:all: "--find-links=$wheelhouse" --require-hashes --requirement (Join-Path $projectRoot "requirements-data-compass.lock") --requirement (Join-Path $projectRoot "requirements-exe-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to prepare isolated build environment" }
    & $buildPython -m PyInstaller --noconfirm --clean --log-level=WARN "--distpath=$dist" "--workpath=$work" (Join-Path $projectRoot "packaging\DataCompassConverterOneFile.spec")
    if ($LASTEXITCODE -ne 0) { throw "Unable to build DataCompassConverter.exe" }
    $built = Join-Path $dist "DataCompassConverter.exe"
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) { throw "DataCompassConverter.exe was not produced" }
    $target = Join-Path $projectRoot "DataCompassConverter.exe"
    Copy-Item -LiteralPath $built -Destination $target -Force
    & $Python -c "import subprocess,sys; raise SystemExit(subprocess.run([sys.argv[1], '--self-check'], timeout=30).returncode)" $target
    if ($LASTEXITCODE -ne 0) { throw "Frozen self-check failed" }
    Write-Host "Created: $target"
} finally {
    if (Test-Path -LiteralPath $buildRoot) {
        $resolved = (Resolve-Path -LiteralPath $buildRoot).Path
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
        if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved).StartsWith("data-compass-exe-")) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
