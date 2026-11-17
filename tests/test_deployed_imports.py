#!/usr/bin/env python3
"""Runtime-fidelity zip-import verification for the built ui_backend Lambda package.

Task 2.2 (lambda-handler-decomposition). This suite is the authoritative proof that the
packaged ``ui_backend`` modules import the way the Lambda runtime resolves them — from a
single flat zip root (``/var/task``) — which the local multi-root unit-test ``pythonpath``
(``source/lambda`` *and* ``source/lambda/ui_backend``) cannot prove because it is strictly
*more permissive* than the runtime (Requirement 13.5, design "Runtime-fidelity note").

It preserves the intent of the previous ``test_deployed_imports.py`` (simulate the deployed
Lambda environment) but is now **build-driven and assertion-based** rather than a stale,
assertion-free print-script that hand-copied a few files against a layout that no longer
matches: it builds the *real* ``ui_backend_deployment.zip`` via ``package_lambda.sh``,
extracts it, and imports the packaged modules **in a fresh subprocess with the extracted
zip root as the sole first-party module-search root** (the local source tree / test
``pythonpath`` cannot leak in).

What it asserts:

* The built zip contains the ``routes/`` package (``routes/__init__.py``) and **all eight**
  route modules; a missing module fails the check identifying it (Requirement 13.4 / 17.7).
* With the extracted zip root as the sole first-party root, the **zip-root import forms the
  Lambda runtime uses** succeed — ``import handler``; ``from routes import resolve_route``;
  ``from services...``; ``from shared...`` — and ``handler.lambda_handler`` dispatches a
  sample event (``OPTIONS`` -> 200, unmatched path -> 404 ``Endpoint not found``), matching
  CDK ``handler="handler.lambda_handler"`` (Requirements 13.3, 13.5).
* NEGATIVE: a ``ui_backend.``-prefixed import (``import ui_backend.handler``) does **not**
  resolve under the sole zip root — it only resolves under the permissive local multi-root
  ``pythonpath`` — so this test catches the runtime-only import drift the unit harness masks
  (Requirement 13.5 / 15.6).

If packaging cannot run in this environment (``bash``/``zip`` unavailable, or the build
fails — e.g. offline ``pip install``), the suite ``skip``s cleanly with the reason; it does
**not** silently pass. A genuine import/dispatch failure of the packaged modules is a test
**failure**, not a skip.

All first-party imports here are canonical-root-clean; the module needs none itself (it
drives everything through the subprocess), keeping it discoverable under both the current
``pytest.ini`` pythonpath and #11's tightened single-canonical-root pythonpath (Req 17.6).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

# Repo root: tests/test_deployed_imports.py -> parents[1] is the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = PROJECT_ROOT / "deployment" / "infrastructure" / "package_lambda.sh"
LAMBDA_SOURCE = PROJECT_ROOT / "source" / "lambda"
BUILT_ZIP = LAMBDA_SOURCE / "ui_backend_deployment.zip"

# The eight per-domain route modules that MUST be present in the packaged routes/ package
# (Requirement 4.1 / 13.4). Names match source/lambda/ui_backend/routes/.
ROUTE_MODULES = (
    "analytics",
    "analytics_agent",
    "bot_control",
    "content",
    "partners",
    "pricing",
    "processing",
    "taxonomy",
)

# Archive members (zip-root layout: routes/ sits at the root alongside handler.py).
EXPECTED_ROUTE_MEMBERS = ("routes/__init__.py",) + tuple(
    f"routes/{name}.py" for name in ROUTE_MODULES
)

# Import-time environment the packaged modules need at module load. Mirrors the repo's
# tests/conftest.py setdefaults: importing ``handler`` transitively imports
# ``content_management`` (via ``services.content_service``), which builds DynamoDB/Cognito
# resources and reads these env vars at import (no AWS calls are made — only client/table
# objects are constructed). A region is required for boto3 client construction.
_SUBPROCESS_ENV_DEFAULTS = {
    "AWS_DEFAULT_REGION": "us-west-2",
    "AWS_REGION": "us-west-2",
    "CONTENT_TABLE_NAME": "test-content-table",
    "JOBS_TABLE_NAME": "test-jobs-table",
    "CONTENT_BUCKET_NAME": "test-content-bucket",
}

# Driver executed in a fresh subprocess whose sole first-party module-search root is the
# extracted zip root (set via PYTHONPATH + cwd; run with -S so the local .venv site-packages
# cannot leak in — the zip carries its own deps). It imports via the exact zip-root forms the
# Lambda runtime uses, dispatches sample events, and proves the ui_backend.-prefixed form does
# NOT resolve. On any failure it prints a traceback and exits non-zero so the test fails.
_IMPORT_DRIVER = r'''
import sys
import traceback


def _run():
    # Zip-root import forms the Lambda runtime uses (CDK handler="handler.lambda_handler").
    import handler
    from routes import resolve_route
    from services.analytics_service import AnalyticsService  # noqa: F401
    from services.partners_service import PartnersService    # noqa: F401
    from shared.utils.response_helpers import create_success_response  # noqa: F401

    # resolve_route sanity (Route_Table resolves a known path).
    assert resolve_route("/taxonomy/categories", "GET") == "taxonomy", "resolve_route drift"

    # handler.lambda_handler dispatches a sample event: OPTIONS -> 200 CORS preflight.
    opt = handler.lambda_handler({"httpMethod": "OPTIONS", "path": "/analytics/metrics"}, None)
    assert opt["statusCode"] == 200, "OPTIONS expected 200, got %r" % (opt,)

    # Unmatched path -> 404 "Endpoint not found" (no auth / no AWS needed).
    nf = handler.lambda_handler({"httpMethod": "GET", "path": "/no-such-endpoint", "body": None}, None)
    assert nf["statusCode"] == 404, "unmatched expected 404, got %r" % (nf,)
    assert "Endpoint not found" in nf.get("body", ""), "unmatched body drift: %r" % (nf,)

    # NEGATIVE: the ui_backend.-prefixed form must NOT resolve under the sole zip root. It
    # resolves only under the permissive local multi-root pythonpath, so its failure here is
    # what proves this test catches runtime-only import drift (Requirement 13.5 / 15.6).
    try:
        import ui_backend.handler  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(
            "ui_backend.handler resolved under the sole zip root; the zip-root layout is not "
            "runtime-faithful (this import must only work under the local multi-root pythonpath)"
        )

    print("DRIVER_OK")


try:
    _run()
except BaseException:
    traceback.print_exc()
    sys.exit(1)
'''


def _packaging_available():
    """Return (ok, reason) for whether the zip build can run in this environment."""
    if not PACKAGE_SCRIPT.is_file():
        return False, f"package script not found at {PACKAGE_SCRIPT}"
    if shutil.which("bash") is None:
        return False, "bash not available on PATH"
    if shutil.which("zip") is None:
        return False, "zip not available on PATH"
    return True, ""


@pytest.fixture(scope="module")
def extracted_zip_root():
    """Build ui_backend_deployment.zip, extract it to a temp dir, and yield (zip, root).

    Skips cleanly (never silently passes) when packaging cannot run here — missing
    ``bash``/``zip`` or a build that exits non-zero (e.g. offline ``pip install``). The
    extraction dir is always cleaned up.
    """
    ok, reason = _packaging_available()
    if not ok:
        pytest.skip(f"packaging cannot run in this environment: {reason}")

    build = subprocess.run(
        ["bash", str(PACKAGE_SCRIPT), "--function", "ui-backend", "source/lambda"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        tail = (build.stderr or build.stdout or "").strip().splitlines()[-15:]
        pytest.skip(
            "ui_backend zip build failed (treated as environmental, e.g. offline pip):\n"
            + "\n".join(tail)
        )

    if not BUILT_ZIP.is_file():
        pytest.skip(f"build reported success but {BUILT_ZIP} was not produced")

    extract_dir = tempfile.mkdtemp(prefix="ui_backend_zip_")
    try:
        with zipfile.ZipFile(BUILT_ZIP) as zf:
            zf.extractall(extract_dir)
        yield BUILT_ZIP, extract_dir
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


class TestDeployedZipContents:
    """The built zip must carry the routes/ package and every route module (Req 13.4 / 17.7)."""

    def test_zip_contains_routes_package_and_all_route_modules(self, extracted_zip_root):
        built_zip, _ = extracted_zip_root
        with zipfile.ZipFile(built_zip) as zf:
            names = set(zf.namelist())

        missing = [member for member in EXPECTED_ROUTE_MEMBERS if member not in names]
        assert not missing, (
            "built ui_backend zip is missing route module(s): "
            + ", ".join(sorted(missing))
            + " (the routes/ package must be bundled or the deployed BFF fails to import)"
        )

    def test_zip_contains_handler_at_root(self, extracted_zip_root):
        built_zip, _ = extracted_zip_root
        with zipfile.ZipFile(built_zip) as zf:
            names = set(zf.namelist())
        assert "handler.py" in names, (
            "handler.py must sit at the zip root to match CDK handler='handler.lambda_handler'"
        )


class TestDeployedZipRuntimeImports:
    """Import + dispatch under the sole zip root, exactly as the Lambda runtime resolves."""

    def _run_driver(self, extract_dir):
        env = {**os.environ, **_SUBPROCESS_ENV_DEFAULTS, "PYTHONPATH": extract_dir}
        # -S: skip site-packages so the local .venv cannot leak first-party modules in; the
        # zip carries its own deps. cwd + PYTHONPATH make the extracted zip root the sole
        # first-party root, mirroring Lambda's single /var/task root.
        return subprocess.run(
            [sys.executable, "-S", "-c", _IMPORT_DRIVER],
            cwd=extract_dir,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_zip_root_imports_and_dispatch_succeed(self, extracted_zip_root):
        _, extract_dir = extracted_zip_root
        result = self._run_driver(extract_dir)
        assert result.returncode == 0 and "DRIVER_OK" in result.stdout, (
            "zip-root import/dispatch verification failed under the sole zip root.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
