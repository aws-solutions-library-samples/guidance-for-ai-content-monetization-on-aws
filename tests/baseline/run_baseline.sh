#!/usr/bin/env bash
# Baseline_Runner for the test-suite-overhaul spec (GitLab #11), Phase 1.
#
# Runs the full test surface and emits JUnit XML, so the baseline can be
# reproduced and used as the Phase-2 regression reference:
#   - pytest in-scope   (all suites EXCEPT the parked tests/analytics_agent/)
#   - pytest parked     (tests/analytics_agent/ — recorded separately; its
#                        ModuleNotFoundError: strands is out of scope)
#   - vitest            (source/ui frontend)
# Then runs analyze_baseline.py to produce baseline-summary.json.
#
# CI-parity prerequisites (this script does NOT do them for you):
#   1. A venv with requirements.txt + requirements-dev.txt installed.
#   2. The two per-Lambda third-party deps needed to import in-scope suites:
#        pip install "markdownify>=0.13.1" "opensearch-py>=2.6.0"
#   3. Built Lambda zips (make package-lambda) — required by
#      tests/infrastructure/ (CDK Code.from_asset) and the deployed-import check.
#   4. source/ui deps installed (npm install).
#
# Usage:  tests/baseline/run_baseline.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
OUT=".kiro/specs/test-suite-overhaul/baseline"
mkdir -p "$OUT"
PY="${PYTHON:-.venv/bin/python}"

echo "== pytest in-scope (excluding parked tests/analytics_agent/) =="
"$PY" -m pytest tests/ --ignore=tests/analytics_agent \
  --continue-on-collection-errors -q -p no:cacheprovider --timeout=60 \
  --junitxml="$OUT/pytest-inscope.xml" || true

echo "== pytest PARKED (tests/analytics_agent/) — recorded separately =="
"$PY" -m pytest tests/analytics_agent/ \
  --continue-on-collection-errors -q -p no:cacheprovider --timeout=60 \
  --junitxml="$OUT/pytest-parked.xml" || true

echo "== vitest (source/ui) =="
( cd source/ui && npx vitest run --reporter=default --reporter=junit \
    --outputFile="../../$OUT/vitest.xml" ) || true

echo "== analyze =="
"$PY" tests/baseline/analyze_baseline.py
