#!/usr/bin/env python3
"""Build-validation tests for the Lambda packaging system.

Updated for the merged lambda-handler-decomposition layout (GitLab #11, task 4.1). The previous
version was STALE: it referenced a top-level ``lambda/`` dir (now ``source/lambda/``),
``scripts/package_lambda.sh`` (now ``deployment/infrastructure/package_lambda.sh``), a legacy
``lambda_deployment.zip`` and ``package-partner-api`` / ``package-ui-backend`` Makefile targets that
no longer exist, and it asserted shared modules at a top-level ``auth/`` / ``utils/`` (they live under
``shared/`` — the very phantom-flattening assumption this spec removes).

It also used to *run* packaging and then ``unlink`` the produced zips in ``finally`` — which clobbered
the shared ``source/lambda/*.zip`` artifacts that ``tests/infrastructure/``, the AST zip validator, and
``test_deployed_imports.py`` depend on. This version instead validates the ALREADY-BUILT zips (as the
other zip suites do), skipping cleanly when they are absent, so it is fast and side-effect-free. CLI
contract tests (help / invalid function / missing source path) still run the script directly since they
produce no artifacts.
"""

import ast
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_DIR = PROJECT_ROOT / "source" / "lambda"
PACKAGE_SCRIPT = PROJECT_ROOT / "deployment" / "infrastructure" / "package_lambda.sh"

# (zip name, whether a root-level handler.py is expected) for the request-handler Lambdas.
HANDLER_ZIPS = [
    ("partner_api_deployment.zip", True),
    ("ui_backend_deployment.zip", True),
    ("shared_services_deployment.zip", False),  # shared-code bundle, no request handler at root
    ("search_projection_backfill_deployment.zip", True),
]


def _zip_names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return zf.namelist()


class TestLambdaSourceLayout:
    """The source tree matches the merged (decomposition) layout the packaging script expects."""

    def test_package_script_exists_and_executable(self):
        assert PACKAGE_SCRIPT.exists(), f"Package script not found: {PACKAGE_SCRIPT}"
        assert os.access(PACKAGE_SCRIPT, os.X_OK), "Package script is not executable"

    def test_lambda_directory_structure(self):
        assert LAMBDA_DIR.exists()
        assert (LAMBDA_DIR / "shared").exists()
        assert (LAMBDA_DIR / "partner_api").exists()
        assert (LAMBDA_DIR / "ui_backend").exists()
        assert (LAMBDA_DIR / "llms_txt_worker").exists()
        assert (LAMBDA_DIR / "search_projection_backfill").exists()
        # Per-Lambda handlers (canonical decomposition layout)
        assert (LAMBDA_DIR / "partner_api" / "handler.py").exists()
        assert (LAMBDA_DIR / "ui_backend" / "handler.py").exists()
        # ui_backend routes package introduced by the decomposition
        assert (LAMBDA_DIR / "ui_backend" / "routes").exists()
        # shared subpackages resolve under shared/ (NOT phantom top-level auth/ utils/)
        assert (LAMBDA_DIR / "shared" / "auth").exists()
        assert (LAMBDA_DIR / "shared" / "utils").exists()

    def test_requirements_files_exist(self):
        assert (LAMBDA_DIR / "ui_backend" / "requirements.txt").is_file()
        assert (LAMBDA_DIR / "partner_api" / "requirements.txt").is_file()
        for opt in ("shared", "content_pipeline"):
            req = LAMBDA_DIR / opt / "requirements.txt"
            if req.exists():
                assert req.is_file()

    def test_retired_enhanced_search_handler_is_absent(self):
        assert not (LAMBDA_DIR / "enhanced_search_handlers.py").exists()
        assert "enhanced_search_handlers.py" not in PACKAGE_SCRIPT.read_text()

    def test_lambda_sources_do_not_import_retired_enhanced_search_handler(self):
        offenders = []
        for source_file in LAMBDA_DIR.rglob("*.py"):
            relative_path = source_file.relative_to(LAMBDA_DIR)
            if "python" in relative_path.parts or any(part.endswith("_deprecated") for part in relative_path.parts):
                continue
            tree = ast.parse(source_file.read_text(), filename=str(source_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or "", *(alias.name for alias in node.names)]
                else:
                    continue
                if any("enhanced_search_handlers" in name.split(".") for name in imported):
                    offenders.append(f"{relative_path}:{node.lineno}")
        assert not offenders, "Lambda sources import the retired enhanced search handler:\n" + "\n".join(offenders)


class TestBuiltPackageContents:
    """Validate the contents of the already-built deployment zips (skip if not built)."""

    @pytest.mark.parametrize("zip_name,expect_handler", HANDLER_ZIPS)
    def test_built_zip_contents(self, zip_name, expect_handler):
        zip_path = LAMBDA_DIR / zip_name
        if not zip_path.exists():
            pytest.skip(f"{zip_name} not built — run `make package-lambda` (CI builds it in build-backend)")
        names = _zip_names(zip_path)
        assert names, f"{zip_name} is empty"
        assert not any(Path(name).name == "enhanced_search_handlers.py" for name in names), (
            f"{zip_name} contains the retired enhanced search handler"
        )

        # Handler at the zip root for the request-handler Lambdas (CDK handler='handler.lambda_handler')
        if expect_handler:
            assert "handler.py" in names, f"handler.py missing from {zip_name}"

        # Shared modules ship under shared/ (canonical), never a phantom top-level auth/ or utils/
        assert any(n.startswith("shared/") for n in names), f"shared/ missing from {zip_name}"
        assert not any(n.startswith("auth/") or n.startswith("utils/") for n in names), (
            f"{zip_name} contains phantom top-level auth/ or utils/ (should be under shared/)"
        )

        # The services/ package (thin-service layer) ships in every first-party zip
        assert any(n.startswith("services/") for n in names), f"services/ missing from {zip_name}"

        # No __pycache__ leaked into the artifact
        assert not any("__pycache__" in n for n in names), f"{zip_name} contains __pycache__"

    def test_ui_backend_zip_contains_routes(self):
        zip_path = LAMBDA_DIR / "ui_backend_deployment.zip"
        if not zip_path.exists():
            pytest.skip("ui_backend_deployment.zip not built")
        names = _zip_names(zip_path)
        assert any(n.startswith("routes/") for n in names), "routes/ package missing from ui_backend zip"

    def test_llms_txt_worker_zip_contains_handler_and_service(self):
        zip_path = LAMBDA_DIR / "llms_txt_worker_deployment.zip"
        if not zip_path.exists():
            pytest.skip("llms_txt_worker_deployment.zip not built")
        names = _zip_names(zip_path)
        assert "handler.py" in names
        assert "services/llms_txt_service.py" in names
        assert "shared/utils/partner_api_url.py" in names  # absolute Access links in the manifest
        # Importing shared.utils.partner_api_url executes shared/utils/__init__.py, which eagerly
        # imports these three modules (relative imports the AST zip-validator cannot resolve). If any
        # is missing the worker fails at import — the whole generation path crashes. Guard the closure.
        for required in (
            "shared/utils/__init__.py",
            "shared/utils/response_helpers.py",
            "shared/utils/response_validator.py",
            "shared/utils/safe_datetime_parser.py",
        ):
            assert required in names, f"worker zip missing {required} (shared.utils import closure)"
        # response_validator imports yaml at module top; PyYAML must be vendored or the worker can't import.
        assert any(n == "yaml/__init__.py" or n.startswith("yaml/") for n in names), "worker zip missing PyYAML"
        assert not any("__pycache__" in n for n in names)

    def test_content_pipeline_zip_contains_native_edge_addressing(self):
        zip_path = LAMBDA_DIR / "content_pipeline_deployment.zip"
        if not zip_path.exists():
            pytest.skip("content_pipeline_deployment.zip not built")
        names = _zip_names(zip_path)
        assert "handler.py" in names
        assert "shared/utils/canonical_url.py" in names
        assert "shared/utils/edge_address.py" in names
        assert "shared/utils/models.py" in names
        assert "services/search_projection.py" in names

    def test_search_projection_backfill_zip_contains_projection_dependencies(self):
        zip_path = LAMBDA_DIR / "search_projection_backfill_deployment.zip"
        if not zip_path.exists():
            pytest.skip("search_projection_backfill_deployment.zip not built")
        names = _zip_names(zip_path)
        assert "handler.py" in names
        assert "services/search_projection.py" in names
        assert "services/opensearch_client.py" in names
        assert "shared/utils/canonical_url.py" in names
        assert "shared/s3_vector_service.py" not in names


class TestPackageScriptCLI:
    """The packaging CLI contract (no artifacts produced by these)."""

    def test_help(self):
        result = subprocess.run(
            [str(PACKAGE_SCRIPT), "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert "--function" in result.stdout

    def test_invalid_function_name(self):
        result = subprocess.run(
            [str(PACKAGE_SCRIPT), "--function", "invalid-function", str(LAMBDA_DIR)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Unknown function name" in (result.stdout + result.stderr)

    def test_missing_source_path(self):
        result = subprocess.run(
            [str(PACKAGE_SCRIPT), "--function", "partner-api"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "source_path is required" in (result.stdout + result.stderr)


class TestMakefileIntegration:
    """The Makefile drives packaging through the single `package-lambda` target."""

    def test_makefile_package_lambda_target(self):
        makefile = PROJECT_ROOT / "Makefile"
        assert makefile.exists(), "Makefile not found"
        content = makefile.read_text()
        assert "package-lambda:" in content, "package-lambda target missing"
        # It invokes the script per function (the decomposition replaced the old
        # package-partner-api / package-ui-backend targets with one package-lambda target)
        for fn in (
            "partner-api",
            "ui-backend",
            "shared-services",
            "content-pipeline",
            "llms-txt-worker",
            "search-projection-backfill",
        ):
            assert f"--function {fn}" in content, f"package-lambda does not build {fn}"


if __name__ == "__main__":
    pytest.main([__file__])
