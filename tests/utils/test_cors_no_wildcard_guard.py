"""
TM-02 import-guard (security-hardening-phase2, task 3.5 / Requirement 4.2).

Asserts that no hand-rolled ``Access-Control-Allow-Origin: *`` literal remains
anywhere under ``source/lambda``. The CORS origin is produced solely by the
shared resolver in ``response_helpers.py`` (``_cors_origin`` / ``cors_headers``),
which never emits a bare ``*`` — so a wildcard literal reappearing in any handler
or service is a regression of the finding this fix closes.

The shared helper file itself is exempt: it mentions the string only in its
docstring/policy comments, never as an emitted header value.
"""

import re
from pathlib import Path

import pytest

_LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "source" / "lambda"

# Matches a wildcard Allow-Origin header literal in either quote style, e.g.
#   "Access-Control-Allow-Origin": "*"
#   'Access-Control-Allow-Origin': '*'
_WILDCARD_RE = re.compile(r"""Access-Control-Allow-Origin['"]\s*:\s*['"]\*['"]""")

# The shared resolver module owns the policy; it never emits "*" (the string only
# appears in its comments). Excluded by path.
_EXEMPT = {
    _LAMBDA_ROOT / "shared" / "utils" / "response_helpers.py",
}


def _python_files():
    for path in _LAMBDA_ROOT.rglob("*.py"):
        # Skip deprecated/vendored trees and bytecode caches.
        if "__pycache__" in path.parts or "utils_deprecated" in path.parts:
            continue
        if path in _EXEMPT:
            continue
        yield path


def test_no_wildcard_cors_origin_literal_in_source_lambda():
    offenders = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _WILDCARD_RE.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Hand-rolled wildcard CORS origin literal(s) found in source/lambda "
        "(TM-02 regression). Route responses through cors_headers()/_cors_origin "
        "instead of a literal '*':\n" + "\n".join(offenders)
    )


def test_guard_regex_actually_matches_a_wildcard():
    """Sanity: the guard regex is not vacuous — it matches a known wildcard literal."""
    assert _WILDCARD_RE.search('"Access-Control-Allow-Origin": "*"')
    assert _WILDCARD_RE.search("'Access-Control-Allow-Origin': '*'")
    assert not _WILDCARD_RE.search('"Access-Control-Allow-Origin": origin')


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
