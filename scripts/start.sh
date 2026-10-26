#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then python3.12 -m venv .venv; fi
.venv/bin/python -m pip install -e '.[dev]'
npm ci
npm run build
exec .venv/bin/creative-agent serve
