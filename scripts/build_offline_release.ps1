[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$OutputDirectory,
    [ValidatePattern("^3\.(11|12|13)$")]
    [string]$TargetPythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
Import-Module Microsoft.PowerShell.Archive -ErrorAction Stop
. (Join-Path $PSScriptRoot "common.ps1")

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyproject = Join-Path $projectRoot "pyproject.toml"
$version = (& $Python -c "import pathlib,sys,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['version'])" $pyproject).Trim()
if ($LASTEXITCODE -ne 0 -or -not $version) { throw "Unable to determine project version" }

$targetParts = $TargetPythonVersion.Split(".")
$targetAbi = "cp$($targetParts[0])$($targetParts[1])"
$targetPlatform = "win_amd64"
$runtimeFingerprint = "win32|CPython|64|amd64|$TargetPythonVersion"
$releaseRoot = if ($OutputDirectory) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
} else {
    Join-Path $projectRoot "dist\pbix-doc-$version-$targetAbi-$targetPlatform"
}
$archivePath = "$releaseRoot.zip"
if ((Test-Path -LiteralPath $releaseRoot) -or (Test-Path -LiteralPath $archivePath)) {
    throw "Release directory or archive already exists: $releaseRoot"
}

$wheelhouse = New-Item -ItemType Directory -Path (Join-Path $releaseRoot "wheelhouse")
& $Python -m pip --isolated --disable-pip-version-check download `
    --require-hashes `
    --only-binary=:all: `
    --platform $targetPlatform `
    --implementation cp `
    --python-version $TargetPythonVersion `
    --abi $targetAbi `
    --dest $wheelhouse.FullName `
    --requirement (Join-Path $projectRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Unable to build the offline wheelhouse" }

$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ("pbix-doc-build_" + [guid]::NewGuid().ToString("N"))
$buildTools = New-Item -ItemType Directory -Path (Join-Path $buildRoot "tools")
$buildEnvironment = Join-Path $buildRoot "venv"
try {
    & $Python -m pip --isolated --disable-pip-version-check download --require-hashes --only-binary=:all: --no-deps --dest $buildTools.FullName --requirement (Join-Path $projectRoot "requirements-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to download verified build tools" }
    & $Python -m venv $buildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the isolated build environment" }
    $buildPython = Join-Path $buildEnvironment "Scripts\python.exe"
    & $buildPython -m pip --isolated --disable-pip-version-check install --no-index --only-binary=:all: "--find-links=$($buildTools.FullName)" --require-hashes --requirement (Join-Path $projectRoot "requirements-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Unable to prepare the isolated build environment" }
    & $buildPython -m pip --isolated --disable-pip-version-check wheel --no-build-isolation --no-deps --wheel-dir $wheelhouse.FullName $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to build the application wheel" }
} finally {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath (Join-Path $projectRoot "requirements.lock") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "common.ps1") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_offline.ps1") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "setup_offline.ps1") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run_pbix_doc.ps1") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "start_pbix_doc.cmd") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "OFFLINE_README.txt") -Destination $releaseRoot
$runtimeFingerprint | Set-Content -LiteralPath (Join-Path $releaseRoot "TARGET_RUNTIME") -Encoding ascii

$manifest = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($releaseRoot.Length + 1).Replace("\", "/")
        "{0}  {1}" -f (Get-Sha256Hex -Path $_.FullName), $relative
    }
$manifest | Set-Content -LiteralPath (Join-Path $releaseRoot "SHA256SUMS") -Encoding ascii
Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
Write-Host "Offline release created: $archivePath"
