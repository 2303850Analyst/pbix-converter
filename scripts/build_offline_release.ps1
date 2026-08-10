param(
    [string]$Python = "python",
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyproject = Join-Path $projectRoot "pyproject.toml"
$version = (& $Python -c "import pathlib,sys,tomllib; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['version'])" $pyproject).Trim()
if ($LASTEXITCODE -ne 0) { throw "Не удалось определить версию проекта" }

$releaseRoot = if ($OutputDirectory) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
} else {
    Join-Path $projectRoot "dist\pbix-doc-$version-windows-x64"
}
$archivePath = "$releaseRoot.zip"
if ((Test-Path -LiteralPath $releaseRoot) -or (Test-Path -LiteralPath $archivePath)) {
    throw "Каталог или архив релиза уже существует: $releaseRoot"
}

$wheelhouse = New-Item -ItemType Directory -Path (Join-Path $releaseRoot "wheelhouse")
& $Python -m pip download --require-hashes --only-binary=:all: --dest $wheelhouse.FullName --requirement (Join-Path $projectRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Не удалось собрать локальный wheelhouse" }

$buildRoot = Join-Path ([IO.Path]::GetTempPath()) ("pbix-doc-build_" + [guid]::NewGuid().ToString("N"))
$buildTools = New-Item -ItemType Directory -Path (Join-Path $buildRoot "tools")
$buildEnvironment = Join-Path $buildRoot "venv"
try {
    & $Python -m pip download --require-hashes --only-binary=:all: --no-deps --dest $buildTools.FullName --requirement (Join-Path $projectRoot "requirements-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось получить проверенные инструменты сборки" }
    & $Python -m venv $buildEnvironment
    $buildPython = Join-Path $buildEnvironment "Scripts\python.exe"
    & $buildPython -m pip install --no-index "--find-links=$($buildTools.FullName)" --require-hashes --requirement (Join-Path $projectRoot "requirements-build.lock")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось подготовить изолированную среду сборки" }
    & $buildPython -m pip wheel --no-build-isolation --no-deps --wheel-dir $wheelhouse.FullName $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Не удалось собрать wheel приложения" }
} finally {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath (Join-Path $projectRoot "requirements.lock") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_offline.ps1") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "run_pbix_doc.ps1") -Destination $releaseRoot

$manifest = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Sort-Object FullName |
    ForEach-Object {
        $relative = $_.FullName.Substring($releaseRoot.Length + 1).Replace("\", "/")
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
    }
$manifest | Set-Content -LiteralPath (Join-Path $releaseRoot "SHA256SUMS") -Encoding ascii
Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal
Write-Host "Офлайн-релиз создан: $archivePath"
