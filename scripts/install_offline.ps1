param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$releaseRoot = $PSScriptRoot
$wheelhouse = Join-Path $releaseRoot "wheelhouse"
$manifestPath = Join-Path $releaseRoot "SHA256SUMS"
$releasePrefix = $releaseRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$listedFiles = @()

foreach ($line in Get-Content -LiteralPath $manifestPath) {
    if ($line -notmatch "^([0-9a-f]{64})  (.+)$") { throw "Повреждён SHA256SUMS" }
    $relative = $Matches[2]
    $file = [IO.Path]::GetFullPath((Join-Path $releaseRoot $relative.Replace("/", [IO.Path]::DirectorySeparatorChar)))
    if (-not $file.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Недопустимый путь в SHA256SUMS" }
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Не найден файл: $($Matches[2])" }
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Matches[1]) { throw "Контрольная сумма не совпала: $($Matches[2])" }
    $listedFiles += $relative
}

$actualFiles = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Where-Object Name -ne "SHA256SUMS" |
    ForEach-Object { $_.FullName.Substring($releaseRoot.Length + 1).Replace("\", "/") }
if ($listedFiles.Count -ne ($listedFiles | Sort-Object -Unique).Count -or (Compare-Object ($listedFiles | Sort-Object) ($actualFiles | Sort-Object))) {
    throw "Состав релиза не совпадает с SHA256SUMS"
}

$applicationWheels = @(Get-ChildItem -LiteralPath $wheelhouse -Filter "pbix_doc-*.whl")
if ($applicationWheels.Count -ne 1) { throw "Ожидался ровно один wheel приложения" }
& $Python -m pip install --no-index "--find-links=$wheelhouse" --require-hashes --requirement (Join-Path $releaseRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Офлайн-установка зависимостей завершилась с ошибкой" }
& $Python -m pip install --no-index --no-deps $applicationWheels[0].FullName
if ($LASTEXITCODE -ne 0) { throw "Офлайн-установка завершилась с ошибкой" }
Write-Host "PBIX Converter установлен без сетевого доступа"
