[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$CheckRuntimeOnly
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$releaseRoot = $PSScriptRoot
$wheelhouse = Join-Path $releaseRoot "wheelhouse"
$manifestPath = Join-Path $releaseRoot "SHA256SUMS"
$releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$venvPrefix = (Join-Path $releaseRoot ".venv").TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$listedFiles = @()

foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if ($line -notmatch "^([0-9a-f]{64})  (.+)$") { throw "Invalid SHA256SUMS" }
    $relative = $Matches[2]
    $file = [IO.Path]::GetFullPath((Join-Path $releaseRoot $relative.Replace("/", [IO.Path]::DirectorySeparatorChar)))
    if (-not $file.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe path in SHA256SUMS" }
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing release file: $($Matches[2])" }
    $actual = Get-Sha256Hex -Path $file
    if ($actual -ne $Matches[1]) { throw "Checksum mismatch: $($Matches[2])" }
    $listedFiles += $relative
}

$actualFiles = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Where-Object {
        $_.Name -ne "SHA256SUMS" -and
        -not $_.FullName.StartsWith($venvPrefix, [StringComparison]::OrdinalIgnoreCase)
    } |
    ForEach-Object { $_.FullName.Substring($releaseRoot.Length + 1).Replace("\", "/") }
if ($listedFiles.Count -ne ($listedFiles | Sort-Object -Unique).Count -or (Compare-Object ($listedFiles | Sort-Object) ($actualFiles | Sort-Object))) {
    throw "Release contents do not match SHA256SUMS"
}

$targetRuntime = (Get-Content -LiteralPath (Join-Path $releaseRoot "TARGET_RUNTIME") -Raw).Trim()
$runtimeCode = "import platform,struct,sys; machine=platform.machine().lower(); machine='amd64' if machine=='x86_64' else machine; print('|'.join((sys.platform,platform.python_implementation(),str(struct.calcsize('P')*8),machine,f'{sys.version_info.major}.{sys.version_info.minor}')))"
$actualRuntime = (& $Python -c $runtimeCode).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRuntime -ne $targetRuntime) {
    throw "This release requires runtime $targetRuntime; selected interpreter is $actualRuntime"
}
if ($CheckRuntimeOnly) { return }

$env:PIP_NO_INDEX = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$applicationWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "pbix_doc-*.whl")
if ($applicationWheels.Count -ne 1) { throw "Expected exactly one application wheel" }
& $Python -m pip --isolated --disable-pip-version-check install --no-index --only-binary=:all: "--find-links=$wheelhouse" --require-hashes --requirement (Join-Path $releaseRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Offline dependency installation failed" }
& $Python -m pip --isolated --disable-pip-version-check install --no-index --only-binary=:all: --no-deps $applicationWheels[0].FullName
if ($LASTEXITCODE -ne 0) { throw "Offline application installation failed" }
& $Python -m pip --disable-pip-version-check check
if ($LASTEXITCODE -ne 0) { throw "Installed environment is inconsistent" }
& $Python -c "import docx,pbix_doc,pbixray"
if ($LASTEXITCODE -ne 0) { throw "Installed application import check failed" }
Write-Host "PBIX Converter installed without network access"
