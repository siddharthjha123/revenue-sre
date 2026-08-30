$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$viteEntry = Join-Path $PSScriptRoot "node_modules\vite\bin\vite.js"
$bundledPnpm = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

if (-not (Test-Path -LiteralPath $viteEntry)) {
    Write-Host "Frontend dependencies are missing. Installing with pnpm..." -ForegroundColor Yellow

    $pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
    if ($pnpmCommand) {
        & $pnpmCommand.Source install
    }
    elseif (Test-Path -LiteralPath $bundledPnpm) {
        & $bundledPnpm install
    }
    else {
        throw "pnpm is unavailable. Repair Node.js/Corepack, then run 'pnpm install'."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Frontend dependency installation failed with exit code $LASTEXITCODE."
    }
}

Write-Host "Starting Revenue SRE dashboard at http://127.0.0.1:5173" -ForegroundColor Green
& node $viteEntry --host 127.0.0.1 --port 5173 --strictPort
