[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = "python",
    [switch]$InstallOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ApplicationArguments
)

$ErrorActionPreference = "Stop"
$releaseRoot = $PSScriptRoot
. (Join-Path $releaseRoot "common.ps1")
$venvRoot = Join-Path $releaseRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$installer = Join-Path $releaseRoot "install_offline.ps1"
$runner = Join-Path $releaseRoot "run_pbix_doc.ps1"

$runtimePython = if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } else { $Python }
& $installer -Python $runtimePython -CheckRuntimeOnly

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $venvRoot) { throw "The local .venv is incomplete; remove it and run setup again" }
    & $Python -m venv $venvRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Unable to create the local .venv"
    }
}

$bundleHash = Get-Sha256Hex -Path (Join-Path $releaseRoot "SHA256SUMS")
$stampPath = Join-Path $venvRoot ".pbix_doc_bundle"
$installedHash = if (Test-Path -LiteralPath $stampPath -PathType Leaf) { (Get-Content -LiteralPath $stampPath -Raw).Trim() } else { "" }
$importReady = $false
if ($installedHash -eq $bundleHash) {
    & $venvPython -c "import docx,pbix_doc,pbixray" 2>$null
    $importReady = $LASTEXITCODE -eq 0
}
if (-not $importReady) {
    & $installer -Python $venvPython
    $bundleHash | Set-Content -LiteralPath $stampPath -Encoding ascii
}

if ($InstallOnly) { return }
& $runner -Python $venvPython @ApplicationArguments
exit $LASTEXITCODE
