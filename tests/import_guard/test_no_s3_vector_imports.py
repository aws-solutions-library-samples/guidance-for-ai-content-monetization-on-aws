"""Regression guard for the retired S3 Vector implementation."""

import ast
from pathlib import Path


LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "source" / "lambda"
ROUTED_PACKAGE_DIRS = (
    "partner_api",
    "ui_backend",
    "content_pipeline",
    "services",
    "shared",
    "search_projection_backfill",
)


def test_retired_s3_vector_modules_are_absent():
    assert not (LAMBDA_ROOT / "shared" / "s3_vector_service.py").exists()
    assert not (LAMBDA_ROOT / "vector_search.py").exists()
    assert not (LAMBDA_ROOT / "vector_store_management.py").exists()


def test_routed_packages_do_not_import_retired_s3_vector_code():
    offenders = []
    for package_dir in ROUTED_PACKAGE_DIRS:
        for source_file in (LAMBDA_ROOT / package_dir).rglob("*.py"):
            tree = ast.parse(source_file.read_text(), filename=str(source_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    continue
                if any("s3_vector" in name or "vector_store_management" in name for name in imported):
                    offenders.append(f"{source_file.relative_to(LAMBDA_ROOT)}:{node.lineno}:{imported}")

    assert not offenders, "Routed packages import retired S3 Vector code:\n" + "\n".join(offenders)
