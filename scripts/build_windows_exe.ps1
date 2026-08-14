[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Remove-BuildWorkspace {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\") + "\"
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a build directory outside the local temp root"
    }
    if (-not (Split-Path -Leaf $resolved).StartsWith("pbix-doc-exe-build_", [StringComparison]::Ordinal)) {
        throw "Refusing to remove an unexpected build directory"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeCode = "import platform,struct,sys; machine=platform.machine().lower(); machine='amd64' if machine=='x86_64' else machine; print('|'.join((sys.platform,platform.python_implementation(),str(struct.calcsize('P')*8),machine,f'{sys.version_info.major}.{sys.version_info.minor}')))"
$runtime = (& $Python -c $runtimeCode).Trim()
if ($LASTEXITCODE -ne 0 -or $runtime -ne "win32|CPython|64|amd64|3.12") {
    throw "EXE build requires 64-bit CPython 3.12 for Windows"
}

$pyproject = Join-Path $projectRoot "pyproject.toml"
$version = (& $Python -c "import pathlib,sys,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['version'])" $pyproject).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Unable to determine project version" }

$releaseRoot = if ($OutputDirectory) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
} else {
    Join-Path $projectRoot "dist\pbix-doc-$version-windows-x64-exe"
}
$archivePath = "$releaseRoot.zip"
if (Test-Path -LiteralPath $archivePath) {
    throw "Release archive already exists: $archivePath"
}

$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ("pbix-doc-exe-build_" + [guid]::NewGuid().ToString("N"))
$wheelhouse = New-Item -ItemType Directory -Path (Join-Path $buildRoot "wheelhouse")
$buildEnvironment = Join-Path $buildRoot "venv"
$frozenDist = Join-Path $buildRoot "dist"
$frozenWork = Join-Path $buildRoot "work"
$packageRoot = Join-Path $buildRoot "package"
$stagedArchive = Join-Path $buildRoot "PBIXConverter.zip"
try {
    & $Python -m pip --isolated --disable-pip-version-check --quiet download --require-hashes --only-binary=:all: --dest $wheelhouse.FullName --requirement (Join-Path $projectRoot "requirements.lock") --requirement (Join-Path $projectRoot "requirements-exe-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to download verified EXE build dependencies" }
    & $Python -m venv $buildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the isolated EXE build environment" }
    $buildPython = Join-Path $buildEnvironment "Scripts\python.exe"
    & $buildPython -m pip --isolated --disable-pip-version-check --quiet install --no-index --only-binary=:all: "--find-links=$($wheelhouse.FullName)" --require-hashes --requirement (Join-Path $projectRoot "requirements.lock") --requirement (Join-Path $projectRoot "requirements-exe-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to prepare the isolated EXE build environment" }
    & $buildPython -m PyInstaller --noconfirm --clean --log-level=WARN "--distpath=$frozenDist" "--workpath=$frozenWork" (Join-Path $projectRoot "packaging\PBIXConverter.spec")
    if ($LASTEXITCODE -ne 0) { throw "Unable to build PBIXConverter.exe" }

    $bundleRoot = Join-Path $frozenDist "PBIXConverter"
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot "PBIXConverter.exe") -PathType Leaf)) {
        throw "PBIXConverter.exe was not produced"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot "_internal") -PathType Container)) {
        throw "Embedded runtime was not produced"
    }

    New-Item -ItemType Directory -Path $packageRoot | Out-Null
    Copy-Item -Path (Join-Path $bundleRoot "*") -Destination $packageRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "EXE_README.txt") -Destination $packageRoot
    "PBIX Converter $version`r`nEmbedded runtime: CPython 3.12 Windows x64" | Set-Content -LiteralPath (Join-Path $packageRoot "BUILD_INFO.txt") -Encoding ascii

    $manifest = Get-ChildItem -LiteralPath $packageRoot -File -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($packageRoot.Length + 1).Replace("\", "/")
            "{0}  {1}" -f (Get-Sha256Hex -Path $_.FullName), $relative
        }
    $manifest | Set-Content -LiteralPath (Join-Path $packageRoot "SHA256SUMS") -Encoding ascii

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $packageRoot,
        $stagedArchive,
        [IO.Compression.CompressionLevel]::Optimal,
        $false
    )
    $archiveParent = Split-Path -Parent $archivePath
    New-Item -ItemType Directory -Path $archiveParent -Force | Out-Null
    Move-Item -LiteralPath $stagedArchive -Destination $archivePath
    Write-Host "Windows EXE release created: $archivePath"
} finally {
    Remove-BuildWorkspace -Path $buildRoot
}
