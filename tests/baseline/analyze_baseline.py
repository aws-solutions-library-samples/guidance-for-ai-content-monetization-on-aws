#!/usr/bin/env python3
"""Baseline analyzer for the test-suite-overhaul spec (GitLab #11), Phase 1.

Parses the JUnit XML produced by ``run_baseline.py`` (pytest in-scope, pytest
parked, and Vitest) and cross-references each collected Python test file with an
AST scan for assertion presence. Emits a machine-readable JSON summary that
seeds the checked-in ``baseline.md`` and the per-file classification table
(VALID / STALE / ORPHAN / ASSERTION-FREE / QUARANTINE / PARKED).

AST-only for the assertion scan: it never imports or executes test code.

Usage:
    python tests/baseline/analyze_baseline.py

Reads:
    .kiro/specs/test-suite-overhaul/baseline/pytest-inscope.xml
    .kiro/specs/test-suite-overhaul/baseline/pytest-parked.xml
    .kiro/specs/test-suite-overhaul/baseline/vitest.xml   (optional)
Writes:
    .kiro/specs/test-suite-overhaul/baseline/baseline-summary.json
"""

from __future__ import annotations

import ast
import json
import os
import xml.etree.ElementTree as ET
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE_DIR = os.path.join(
    REPO_ROOT, ".kiro", "specs", "test-suite-overhaul", "baseline"
)
TESTS_DIR = os.path.join(REPO_ROOT, "tests")

# unittest assertion methods and mock assertion methods count as real assertions
_ASSERT_METHOD_PREFIXES = ("assert",)  # self.assertEqual, mock.assert_called, ...


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def scan_assertions(pyfile: str) -> bool:
    """Return True if the file contains at least one real assertion (AST-only)."""
    try:
        with open(pyfile, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=pyfile)
    except (SyntaxError, UnicodeDecodeError):
        return False

    for node in ast.walk(tree):
        # bare `assert ...`
        if isinstance(node, ast.Assert):
            return True
        # pytest.raises / pytest.warns as context manager or call
        if isinstance(node, ast.Call):
            func = node.func
            # attribute call: x.assertEqual(...), m.assert_called_once(), pytest.raises(...)
            if isinstance(func, ast.Attribute):
                attr = func.attr
                if attr.startswith(_ASSERT_METHOD_PREFIXES):
                    return True
                if attr in ("raises", "warns", "approx"):
                    # pytest.raises / pytest.warns / pytest.approx
                    return True
    return False


def collect_test_files() -> dict[str, dict]:
    """Walk tests/ for test_*.py and record assertion presence + suite."""
    files: dict[str, dict] = {}
    for dirpath, _dirs, filenames in os.walk(TESTS_DIR):
        for fn in filenames:
            if not (fn.startswith("test_") and fn.endswith(".py")):
                continue
            full = os.path.join(dirpath, fn)
            rel = _rel(full)
            # suite = first path component under tests/
            parts = rel.split(os.sep)
            suite = parts[1] if len(parts) > 2 else "(root)"
            files[rel] = {
                "suite": suite,
                "has_assertions": scan_assertions(full),
            }
    return files


def _dotted_to_file(dotted: str) -> str | None:
    """Map a dotted classname/name to the test file on disk (longest prefix)."""
    if not dotted:
        return None
    parts = dotted.split(".")
    # progressively try prefixes, longest first, that resolve to a .py file
    for end in range(len(parts), 0, -1):
        candidate = os.path.join(REPO_ROOT, *parts[:end]) + ".py"
        if os.path.exists(candidate):
            return _rel(candidate)
    return None


def parse_junit(xml_path: str) -> dict[str, dict]:
    """Return per-file outcome aggregates keyed by relative test file path."""
    per_file: dict[str, dict] = defaultdict(
        lambda: {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "skipped": 0,
            "messages": [],
        }
    )
    if not os.path.exists(xml_path):
        return {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for tc in root.iter("testcase"):
        fpath = tc.get("file")
        if not fpath:
            dotted = tc.get("classname", "") or tc.get("name", "")
            fpath = _dotted_to_file(dotted) or (dotted.replace(".", os.sep) + ".py")
        rec = per_file[fpath]
        failure = tc.find("failure")
        error = tc.find("error")
        skipped = tc.find("skipped")
        if error is not None:
            rec["error"] += 1
            msg = (error.get("message") or "").strip().splitlines()
            if msg:
                rec["messages"].append("ERROR: " + msg[0][:200])
        elif failure is not None:
            rec["failed"] += 1
            msg = (failure.get("message") or "").strip().splitlines()
            if msg:
                rec["messages"].append("FAIL: " + msg[0][:200])
        elif skipped is not None:
            rec["skipped"] += 1
        else:
            rec["passed"] += 1
    # dedupe messages, keep a few representative ones
    for rec in per_file.values():
        seen = []
        for m in rec["messages"]:
            if m not in seen:
                seen.append(m)
        rec["messages"] = seen[:3]
    return dict(per_file)


def totals(per_file: dict[str, dict]) -> dict:
    agg = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "files": len(per_file)}
    for rec in per_file.values():
        for k in ("passed", "failed", "error", "skipped"):
            agg[k] += rec[k]
    return agg


def main() -> None:
    test_files = collect_test_files()
    inscope = parse_junit(os.path.join(BASELINE_DIR, "pytest-inscope.xml"))
    parked = parse_junit(os.path.join(BASELINE_DIR, "pytest-parked.xml"))

    # merge assertion info into outcome records
    def enrich(per_file):
        out = {}
        for fpath, rec in per_file.items():
            meta = test_files.get(fpath, {})
            out[fpath] = {
                **rec,
                "suite": meta.get("suite", "(unknown)"),
                "has_assertions": meta.get("has_assertions"),
            }
        return out

    inscope_e = enrich(inscope)
    parked_e = enrich(parked)

    # per-suite aggregation for in-scope
    suites: dict[str, dict] = defaultdict(
        lambda: {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "files": 0}
    )
    for rec in inscope_e.values():
        s = suites[rec["suite"]]
        for k in ("passed", "failed", "error", "skipped"):
            s[k] += rec[k]
        s["files"] += 1

    # assertion-free collected files (in-scope only, excludes parked suite)
    assertion_free = sorted(
        fpath
        for fpath, meta in test_files.items()
        if meta["has_assertions"] is False and meta["suite"] != "analytics_agent"
    )

    summary = {
        "inscope_totals": totals(inscope),
        "parked_totals": totals(parked),
        "collected_test_files": len(test_files),
        "assertion_free_files_inscope": assertion_free,
        "per_suite_inscope": dict(sorted(suites.items())),
        "per_file_inscope": dict(sorted(inscope_e.items())),
        "per_file_parked": dict(sorted(parked_e.items())),
    }

    out_path = os.path.join(BASELINE_DIR, "baseline-summary.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=False)
    print(f"Wrote {_rel(out_path)}")
    print(
        "In-scope:", summary["inscope_totals"], "| Parked:", summary["parked_totals"]
    )
    print("Assertion-free in-scope files:", len(assertion_free))


if __name__ == "__main__":
    main()
