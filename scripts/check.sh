#!/usr/bin/env bash
# The ONE verification gate — run before every commit/merge.
#
# Everything here was adopted AT ZERO findings (2026-09-01), so any output is
# a regression, not noise:
#   ruff  — correctness families F+B (style is documented house style, see pyproject)
#   mypy  — backend/, lenient tier + pydantic plugin
#   pytest — the full backend suite (needs the dev DB on 127.0.0.1:5435)
#   vitest — frontend guards (XSS escape contract + orbital-parity anchor)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== ruff (F+B) =="
.venv/bin/ruff check backend/ ./*.py tests/ scripts/

echo "== mypy (backend/) =="
.venv/bin/mypy backend/

echo "== pytest =="
.venv/bin/python -m pytest -q

echo "== vitest (frontend) =="
(cd frontend && npx vitest run --reporter=basic 2>/dev/null || npx vitest run)

echo
echo "ALL GREEN — safe to commit."
