$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .env)) { throw 'Create .env from .env.example and configure it first.' }
docker compose -f compose.yml up -d --build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Output 'Research UI: http://localhost:8501'

