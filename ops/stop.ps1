$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose -f compose.yml --profile '*' stop
exit $LASTEXITCODE

