$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
& .venv\Scripts\python.exe -m pip install -e '.[dev]'
npm ci
npm run build
& .venv\Scripts\creative-agent.exe serve
