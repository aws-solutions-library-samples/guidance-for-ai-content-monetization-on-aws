"""Author-time Import_Guard (GitLab #11, Requirement 7.6).

Anti-regression lock for the phantom-``utils`` import bug class. Under the single canonical import
root (Option A, Req 6) the deployed Lambda zip root is ``source/lambda``, so a first-party import
MUST be canonical: ``from shared.utils.X``, ``from shared.auth.X``, ``from shared.handlers.X`` —
NOT the flattened ``from utils.X`` / ``from auth.X`` / ``from handlers.X`` that only resolved under
the old multi-root pythonpath and broke in the zip with ``No module named 'utils'``.

This is an AST-only check (it never imports/executes the code under test). It scans every first-party
Python file — Lambda source and tests — and fails if any top-level ``import``/``from`` names one of the
phantom top-level packages that the removed flattening roots (``source/lambda/shared``) used to create.

Scope / deliberate exclusions:
  * Excludes vendored ``source/lambda/python/`` and deprecated ``source/lambda/utils_deprecated/``.
  * Excludes the parked ``tests/analytics_agent/`` suite.
  * Does NOT flag ``routes``, ``handler``, ``extractor`` or ``geo_enricher``: those are legitimate
    *zip-root sibling* imports inside a single Lambda's own package (Req 7.7 documented contract),
    canonical relative to that function's zip root — not the shared-flattening bug class.
"""

from __future__ import annotations

import ast
import os

import pytest

# The phantom top-level packages created by the old ``source/lambda/shared`` flattening root.
# Canonical form prefixes each with ``shared.``.
PHANTOM_TOP_LEVEL = {"utils", "auth", "handlers"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# First-party roots to scan, and subtrees to exclude.
SCAN_ROOTS = [
    os.path.join(REPO_ROOT, "source", "lambda"),
    os.path.join(REPO_ROOT, "source", "mock_data_generator"),
    os.path.join(REPO_ROOT, "tests"),
]
EXCLUDE_DIRS = {
    os.path.join(REPO_ROOT, "source", "lambda", "python"),
    os.path.join(REPO_ROOT, "source", "lambda", "utils_deprecated"),
    os.path.join(REPO_ROOT, "source", "agent"),
    os.path.join(REPO_ROOT, "tests", "analytics_agent"),
}


def _iter_first_party_py_files():
    for root in SCAN_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            # prune excluded subtrees and caches
            if any(dirpath == ex or dirpath.startswith(ex + os.sep) for ex in EXCLUDE_DIRS):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _top_level(module: str | None) -> str | None:
    if not module:
        return None
    return module.split(".", 1)[0]


def find_non_canonical_imports(source: str, filename: str) -> list[tuple[int, str]]:
    """Return (lineno, offending-top-level-name) for phantom-root imports (AST-only)."""
    violations: list[tuple[int, str]] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import; not the phantom class
            if node.level and node.level > 0:
                continue
            top = _top_level(node.module)
            if top in PHANTOM_TOP_LEVEL:
                violations.append((node.lineno, top))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_level(alias.name)
                if top in PHANTOM_TOP_LEVEL:
                    violations.append((node.lineno, top))
    return violations


def test_no_non_canonical_root_imports_in_first_party_code():
    """No first-party module or test may import the phantom utils/auth/handlers top-level packages."""
    offenders: list[str] = []
    for path in _iter_first_party_py_files():
        with open(path, "r", encoding="utf-8") as fh:
            try:
                source = fh.read()
                for lineno, name in find_non_canonical_imports(source, path):
                    rel = os.path.relpath(path, REPO_ROOT)
                    offenders.append(
                        f"{rel}:{lineno}: non-canonical-root import of '{name}' "
                        f"(use 'shared.{name}...' — the deployed zip root is source/lambda)"
                    )
            except SyntaxError as exc:  # pragma: no cover - a syntax error is a different failure
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}: could not parse: {exc}")
    assert not offenders, (
        "Non-canonical-root imports found (phantom-utils bug class, GitLab #11 Req 7.6):\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Example / edge-case tests for the guard itself (task 3.4).
# ---------------------------------------------------------------------------

def test_guard_rejects_phantom_from_import():
    v = find_non_canonical_imports("from utils.feature_flags import FeatureFlagController", "x.py")
    assert v == [(1, "utils")]


def test_guard_rejects_phantom_plain_import():
    v = find_non_canonical_imports("import auth", "x.py")
    assert v == [(1, "auth")]


def test_guard_accepts_canonical_shared_import():
    assert find_non_canonical_imports("from shared.utils.feature_flags import X", "x.py") == []


def test_guard_accepts_zip_root_sibling_imports():
    # routes/handler/extractor are legitimate zip-root siblings (Req 7.7), not phantom-shared.
    src = "from routes import resolve_route\nimport handler\nfrom extractor import extract\n"
    assert find_non_canonical_imports(src, "x.py") == []


def test_guard_ignores_relative_imports():
    assert find_non_canonical_imports("from .utils import helper", "x.py") == []
