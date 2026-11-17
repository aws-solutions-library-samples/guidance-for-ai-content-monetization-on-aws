"""AST-based zip-import validator — the defense-in-depth backstop (GitLab #11, Requirement 1).

For each built ``source/lambda/*.zip`` artifact this parses every FIRST-PARTY ``.py`` file's AST
(including lazy imports inside function/method bodies, Req 1.2) and fails if any absolute top-level
import name does not resolve against that same zip's contents plus the Python standard library
(Req 1.3/1.5). It NEVER imports or executes the code under test (Req 1.4).

This is the backstop, not the primary import-correctness gate — the primary mechanism is the single
canonical import root (Req 6) plus the author-time Import_Guard (Req 7.6). This validator catches
what those miss: packaging omissions and lazy imports that only surface in the built artifact.

Design decisions:
  * Only FIRST-PARTY members are validated. Parsing the vendored dependency tree (boto3, opensearchpy,
    ...) would false-positive on the deps' own OPTIONAL lazy imports (e.g. ``import aiohttp`` inside
    opensearchpy). First-party membership is decided by the member's top-level name: a first-party
    package (``shared``/``services``/``ui_backend``/``content_pipeline``/``partner_api``), a root-level
    first-party module (basename of a ``source/lambda/*.py``), or a documented zip-root sibling that
    per-function packaging hoists to the root (``handler``/``routes``/``extractor``/``geo_enricher`` —
    Req 7.7).
  * Resolvability is checked against the union of ALL top-level names present in the zip (first-party
    AND vendored) plus ``sys.stdlib_module_names`` plus builtins. boto3/botocore are additionally
    allowlisted as they are guaranteed by the AWS Lambda runtime even when not bundled.
  * Relative imports (``from . import x``) and ``__future__`` are ignored.

If no zips are present (build not run) the suite skips cleanly with a reason — it never silently
passes. In CI the ``test`` job ``needs: build-backend`` so the zips exist.
"""

from __future__ import annotations

import ast
import os
import sys
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAMBDA_DIR = os.path.join(REPO_ROOT, "source", "lambda")

# Zip-root siblings that per-function packaging hoists to the archive root (Req 7.7).
HOISTED_FIRST_PARTY = {"handler", "routes", "extractor", "geo_enricher"}
# First-party package directories under source/lambda (vendored/deprecated excluded).
EXCLUDED_DIRS = {"python", "utils_deprecated", "__pycache__"}
# Guaranteed by the AWS Lambda Python runtime even if not bundled.
RUNTIME_PROVIDED = {"boto3", "botocore"}

_STDLIB = set(sys.stdlib_module_names) | set(sys.builtin_module_names)


def first_party_top_names() -> set[str]:
    """Top-level import names that belong to first-party code (from the repo source tree)."""
    names: set[str] = set(HOISTED_FIRST_PARTY)
    if not os.path.isdir(LAMBDA_DIR):
        return names
    for entry in os.listdir(LAMBDA_DIR):
        full = os.path.join(LAMBDA_DIR, entry)
        if os.path.isdir(full) and entry not in EXCLUDED_DIRS:
            names.add(entry)  # first-party package (shared, services, ui_backend, ...)
        elif entry.endswith(".py") and entry != "__init__.py":
            names.add(entry[:-3])  # root-level first-party module
    return names


def zip_top_level_names(zf: zipfile.ZipFile) -> set[str]:
    """Every top-level importable name present in the archive (first-party + vendored)."""
    names: set[str] = set()
    for member in zf.namelist():
        norm = member.replace("\\", "/")
        if norm.endswith("/"):
            top = norm.split("/", 1)[0]
        elif "/" in norm:
            top = norm.split("/", 1)[0]
        else:
            top = norm[:-3] if norm.endswith(".py") else norm
        if top and not top.endswith(".dist-info") and top not in {"", "__pycache__"}:
            names.add(top)
    return names


def _import_top_names(node: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            found.append((node.lineno, alias.name.split(".", 1)[0]))
    elif isinstance(node, ast.ImportFrom):
        if not node.level and node.module:  # absolute import only
            found.append((node.lineno, node.module.split(".", 1)[0]))
    return found


def validate_zip(zip_path: str) -> list[str]:
    """Return a list of human-readable violation strings for one zip (empty == clean)."""
    violations: list[str] = []
    fp_top = first_party_top_names()
    with zipfile.ZipFile(zip_path) as zf:
        available = zip_top_level_names(zf) | _STDLIB | RUNTIME_PROVIDED | {"__future__"}
        for member in zf.namelist():
            norm = member.replace("\\", "/")
            if not norm.endswith(".py") or "/__pycache__/" in norm or norm.endswith("/"):
                continue
            member_top = norm.split("/", 1)[0] if "/" in norm else norm[:-3]
            if member_top not in fp_top:
                continue  # skip vendored/third-party members
            try:
                source = zf.read(member).decode("utf-8")
                tree = ast.parse(source, filename=member)
            except (SyntaxError, UnicodeDecodeError) as exc:
                violations.append(f"{os.path.basename(zip_path)}:{member}: parse error: {exc}")
                continue
            for node in ast.walk(tree):  # includes function/method bodies -> lazy imports (Req 1.2)
                for lineno, top in _import_top_names(node):
                    if top and top not in available:
                        violations.append(
                            f"{os.path.basename(zip_path)}:{member}:{lineno}: "
                            f"unresolved top-level import '{top}'"
                        )
    return violations


def _built_zips() -> list[str]:
    if not os.path.isdir(LAMBDA_DIR):
        return []
    return sorted(
        os.path.join(LAMBDA_DIR, f)
        for f in os.listdir(LAMBDA_DIR)
        if f.endswith(".zip")
    )


def test_all_built_zip_imports_resolve():
    """Every first-party import in every built Lambda zip resolves against that zip + stdlib."""
    zips = _built_zips()
    if not zips:
        pytest.skip(
            "No source/lambda/*.zip artifacts found — run `make package-lambda` first "
            "(CI runs this after build-backend). Backstop cannot validate what isn't built."
        )
    all_violations: list[str] = []
    for zip_path in zips:
        all_violations.extend(validate_zip(zip_path))
    assert not all_violations, (
        "AST zip-import validator found unresolved first-party imports (GitLab #11 Req 1):\n"
        + "\n".join(all_violations)
    )


# ---------------------------------------------------------------------------
# Example / edge-case tests using synthetic zips (task 8.2).
# ---------------------------------------------------------------------------

def _make_zip(tmp_path, files: dict[str, str]) -> str:
    zp = os.path.join(tmp_path, "synthetic_deployment.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return zp


def test_clean_zip_passes(tmp_path, monkeypatch):
    # 'handler' is a hoisted first-party top-level name; importing bundled 'helper' + stdlib resolves.
    monkeypatch.setattr(sys.modules[__name__], "first_party_top_names", lambda: {"handler"})
    zp = _make_zip(
        tmp_path,
        {"handler.py": "import os\nimport json\nfrom helper import thing\n", "helper.py": "thing = 1\n"},
    )
    assert validate_zip(zp) == []


def test_zip_missing_module_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "first_party_top_names", lambda: {"handler"})
    zp = _make_zip(tmp_path, {"handler.py": "from missing_pkg import thing\n"})
    violations = validate_zip(zp)
    assert len(violations) == 1 and "missing_pkg" in violations[0] and "handler.py:1" in violations[0]


def test_lazy_import_inside_function_is_inspected(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "first_party_top_names", lambda: {"handler"})
    src = "def f():\n    import definitely_absent_module\n    return definitely_absent_module\n"
    zp = _make_zip(tmp_path, {"handler.py": src})
    violations = validate_zip(zp)
    assert len(violations) == 1 and "definitely_absent_module" in violations[0]


def test_vendored_members_are_not_validated(tmp_path, monkeypatch):
    # A member whose top-level is not first-party (e.g. a vendored dep) is skipped entirely.
    monkeypatch.setattr(sys.modules[__name__], "first_party_top_names", lambda: {"handler"})
    zp = _make_zip(tmp_path, {"boto3/core.py": "import some_optional_backend\n"})
    assert validate_zip(zp) == []


# ---------------------------------------------------------------------------
# integration-adapters (GitLab #35, task 14.3): reuse #26's packaging + zip-import
# validators to assert (a) the Lambda entry points are UNTOUCHED by the additive
# integrations/ layer (Req 1.3) and (b) `from integrations...` resolves at the zip
# root of the relevant deployment zip(s) (Req 1.5).
#
# These reuse the same primitives as the #26 suite above: `validate_zip()` (the AST
# zip-import backstop) and `zip_top_level_names()`. They add NO new packaging path —
# task 9.1 already bundles integrations/ at the zip root in
# deployment/infrastructure/package_lambda.sh. If the bundling regresses, the
# `from integrations...` assertions below fail loudly rather than skipping.
#
# NOTE: this module deliberately does NOT import the `TestResult` port dataclass —
# pytest.ini sets `python_classes = Test*`, so a module-level `TestResult` name would
# be mis-collected as a test class (PytestCollectionWarning). The validators here work
# purely off the built zip contents, so the port types are never referenced.

# Entry-point Lambda source directories that MUST NOT be relocated (Req 1.3).
_ENTRY_POINT_DIRS = ("partner_api", "ui_backend", "content_pipeline")

# (source dir, deployment zip, CDK handler= wiring) — the entry-point contract #26's
# packaging guard protects; the integrations/ addition must leave all of it intact.
_ENTRY_POINT_CONTRACT = [
    ("partner_api", "partner_api_deployment.zip", "handler.lambda_handler"),
    ("ui_backend", "ui_backend_deployment.zip", "handler.lambda_handler"),
    ("content_pipeline", "content_pipeline_deployment.zip", "handler.lambda_handler"),
]

# Deployment zips that import `from integrations...` and therefore MUST carry the
# integrations/ package at their archive root (Req 1.5). ui_backend/routes/connections.py
# does `from integrations.registry import Registry`, so at minimum the ui_backend zip
# needs it. The registry pulls in the domain adapters, so its own imports must resolve too.
_ZIPS_NEEDING_INTEGRATIONS = ["ui_backend_deployment.zip"]

_CDK_HANDLER_SOURCES = (
    os.path.join(REPO_ROOT, "infrastructure", "content_monetization_stack.py"),
    os.path.join(REPO_ROOT, "infrastructure", "pipeline_stack.py"),
    os.path.join(REPO_ROOT, "infrastructure", "authentication_stack.py"),
)


def test_entry_point_source_dirs_not_relocated_by_integrations_layer():
    """The integrations/ layer is additive: partner_api/ ui_backend/ content_pipeline/ stay put (Req 1.3)."""
    if not os.path.isdir(LAMBDA_DIR):
        pytest.skip("source/lambda not present")
    missing = [d for d in _ENTRY_POINT_DIRS if not os.path.isdir(os.path.join(LAMBDA_DIR, d))]
    assert not missing, (
        "integration-adapters (#35) must NOT relocate the Lambda entry points; "
        f"missing entry-point source dir(s): {missing} (Req 1.3)"
    )
    # And the entry-point handler.py files remain at their canonical locations.
    for d in _ENTRY_POINT_DIRS:
        handler = os.path.join(LAMBDA_DIR, d, "handler.py")
        assert os.path.isfile(handler), f"{d}/handler.py relocated or removed (Req 1.3): {handler}"


def test_entry_point_cdk_handler_wiring_intact():
    """CDK still wires each entry point to its unchanged zip + handler= (Req 1.3)."""
    combined = ""
    for src in _CDK_HANDLER_SOURCES:
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as fh:
                combined += fh.read()
    assert combined, "no CDK stack sources found to validate handler= wiring"
    for _src_dir, zip_name, handler in _ENTRY_POINT_CONTRACT:
        assert f'"{handler}"' in combined or f"'{handler}'" in combined, (
            f"CDK handler= wiring '{handler}' missing — entry-point wiring changed (Req 1.3)"
        )
        asset_ref = f"source/lambda/{zip_name}"
        assert asset_ref in combined, (
            f"CDK Code.from_asset('{asset_ref}') missing — entry-point zip wiring changed (Req 1.3)"
        )


@pytest.mark.parametrize("zip_name", _ZIPS_NEEDING_INTEGRATIONS)
def test_integrations_bundled_at_zip_root(zip_name):
    """`integrations/` is bundled at the archive root so `from integrations...` resolves (Req 1.5)."""
    zip_path = os.path.join(LAMBDA_DIR, zip_name)
    if not os.path.exists(zip_path):
        pytest.skip(f"{zip_name} not built — run `make package-lambda` (CI builds it in build-backend)")
    with zipfile.ZipFile(zip_path) as zf:
        top_names = zip_top_level_names(zf)
        members = zf.namelist()
    # integrations/ present as a zip-root top-level package (not nested under some other dir).
    assert "integrations" in top_names, (
        f"{zip_name} does not contain integrations/ at the zip root — "
        f"`from integrations...` will NOT resolve at runtime (Req 1.5). "
        f"Check deployment/infrastructure/package_lambda.sh bundling."
    )
    # registry.py is the concrete module ui_backend imports (`from integrations.registry import Registry`).
    assert "integrations/registry.py" in members, (
        f"{zip_name} is missing integrations/registry.py at the zip root (Req 1.5)"
    )
    # __init__.py present so `integrations` is an importable package at the root.
    assert "integrations/__init__.py" in members, (
        f"{zip_name} is missing integrations/__init__.py — integrations/ is not an importable "
        f"package at the zip root (Req 1.5)"
    )


@pytest.mark.parametrize("zip_name", _ZIPS_NEEDING_INTEGRATIONS)
def test_integrations_imports_resolve_in_zip(zip_name):
    """Reuse the #26 AST backstop to confirm integrations/ first-party imports resolve in the zip (Req 1.5)."""
    zip_path = os.path.join(LAMBDA_DIR, zip_name)
    if not os.path.exists(zip_path):
        pytest.skip(f"{zip_name} not built — run `make package-lambda`")
    # validate_zip() walks every first-party member (incl. integrations/*, since `integrations`
    # is a source/lambda/ dir → first-party top name) and reports unresolved top-level imports.
    violations = [v for v in validate_zip(zip_path) if ":integrations/" in v]
    assert not violations, (
        "integration-adapters: `from integrations...` (or an integrations/* internal import) "
        f"does not resolve in {zip_name} (Req 1.5):\n" + "\n".join(violations)
    )
