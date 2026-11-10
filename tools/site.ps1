param(
    [ValidateSet('serve', 'build', 'setup', 'check', 'stop')]
    [string]$Action = 'serve',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
if ($Action -eq 'stop') {
    $recordPath = Join-Path $repoRoot 'logs\preview-process.json'
    if (-not (Test-Path -LiteralPath $recordPath)) {
        Write-Host 'No managed preview process is recorded.'
        exit 0
    }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $running = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    $expected = Join-Path $repoRoot '.venv\Scripts\zensical.exe'
    $sameStart = $running -and ([string]$running.StartTime.ToUniversalTime().Ticks -eq [string]$record.startTimeUtcTicks)
    if ($running -and $running.Path -eq $expected -and $sameStart) {
        & taskkill.exe /PID $record.pid /T /F
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host 'Preview stopped.'
    } else {
        Write-Host 'The recorded preview is not running.'
    }
    exit 0
}
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$zensical = Join-Path $repoRoot '.venv\Scripts\zensical.exe'

if (-not (Test-Path -LiteralPath $python)) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) {
        $basePython = $candidate.Source
    } elseif (Test-Path -LiteralPath $bundled) {
        $basePython = $bundled
    } else {
        throw 'Python 3.11 or newer is required. Install Python and run tools\setup.cmd again.'
    }
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Action -eq 'setup' -or -not (Test-Path -LiteralPath $zensical)) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Action) {
    'setup' { Write-Host 'Zensical environment is ready.' }
    'build' {
        & $zensical build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host "Static site: $(Join-Path $repoRoot 'site')"
    }
    'check' { & $python tools\check_site.py }
    'serve' {
        $selectedPort = & $python tools\preview_port.py $Port
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $Port = [int]$selectedPort
        Write-Host "Preview: http://127.0.0.1:$Port/linux-doc/"
        & $zensical serve --dev-addr "127.0.0.1:$Port"
    }
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
