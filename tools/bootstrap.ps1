# First step of SETUP.cmd (Windows PowerShell 5.1): this project's own Python and its packages, then tools/setup.py.
# - runtime/python: CPython 3.12 from python-build-standalone (checked against its published SHA-256).
# - .venv: built from it with the pinned requirements.txt.
# Safe to run again: each step is skipped when already done.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest very slow on PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# UTF-8 for every Python started from here: the DCS manuals' file names hold non-Latin letters (the F-5E's
# "F-5Е" has a Cyrillic Е), which a console or pipe in the Windows code page cannot print.
$env:PYTHONUTF8 = '1'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$PythonTag = '20260901'
$PythonVersion = '3.12.14'
$PythonArchive = "cpython-$PythonVersion+$PythonTag-x86_64-pc-windows-msvc-install_only.tar.gz"
$PythonSha256 = 'e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f'
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonTag/" +
             [Uri]::EscapeDataString($PythonArchive)

$Runtime = Join-Path $Root 'runtime'
$Python = Join-Path $Runtime 'python\python.exe'
$Venv = Join-Path $Root '.venv\Scripts\python.exe'

Write-Host "`n[1/3] Python $PythonVersion" -ForegroundColor Cyan
if (Test-Path $Python) {
    Write-Host '  already installed'
} else {
    New-Item -ItemType Directory -Force $Runtime | Out-Null
    $download = Join-Path $Runtime $PythonArchive
    Write-Host "  downloading $PythonUrl"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $download -UseBasicParsing
    $hash = (Get-FileHash $download -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $PythonSha256) {
        Remove-Item $download -Force
        throw "Python download checksum mismatch ($hash): not installed"
    }
    & tar.exe -xzf $download -C $Runtime   # the archive holds one folder: python/
    if ($LASTEXITCODE -ne 0) { throw 'could not extract the Python archive' }
    Remove-Item $download -Force
    Write-Host "  installed in $Runtime\python"
}

Write-Host "`n[2/3] Python packages" -ForegroundColor Cyan
if (-not (Test-Path $Venv)) {
    & $Python -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'could not create .venv' }
}
& $Venv -m pip install --disable-pip-version-check --quiet -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
Write-Host '  done'

Write-Host "`n[3/3] DCS-Instructor setup" -ForegroundColor Cyan
& $Venv (Join-Path $Root 'tools\setup.py')
exit $LASTEXITCODE
