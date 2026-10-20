"""Contract tests for managed llms.txt."""

from pathlib import Path

import yaml

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi-spec.yaml").read_text())


def test_exact_management_paths_and_methods():
    expected = {
        "/content/llms-txt": {"get"},
        "/content/llms-txt/runs": {"post"},
        "/content/llms-txt/runs/{run_id}": {"get"},
        "/content/llms-txt/drafts/{run_id}": {"put"},
        "/content/llms-txt/drafts/{run_id}/publish": {"post"},
    }
    for path, methods in expected.items():
        assert path in SPEC["paths"]
        assert methods <= set(SPEC["paths"][path])


def test_management_operations_are_cognito_only():
    for path, path_item in SPEC["paths"].items():
        if not path.startswith("/content/llms-txt"):
            continue
        for method, operation in path_item.items():
            if method in {"get", "post", "put"}:
                assert operation["security"] == [{"CognitoAuth": []}]
                assert operation["x-audience"] == "internal"


def test_edit_and_publish_require_revision_header():
    for path, method in (
        ("/content/llms-txt/drafts/{run_id}", "put"),
        ("/content/llms-txt/drafts/{run_id}/publish", "post"),
    ):
        operation = SPEC["paths"][path][method]
        if_match = next(parameter for parameter in operation["parameters"] if parameter["name"] == "If-Match")
        assert if_match["in"] == "header"
        assert if_match["required"] is True
        assert "412" in operation["responses"]
        assert "409" in operation["responses"]


def test_run_and_draft_state_enums_are_exact():
    schemas = SPEC["components"]["schemas"]
    assert schemas["LlmsTxtRun"]["properties"]["status"]["enum"] == [
        "queued",
        "processing",
        "completed",
        "failed",
        "no_change",
    ]
    assert schemas["LlmsTxtDraft"]["properties"]["status"]["enum"] == [
        "staged",
        "in_review",
        "published",
        "superseded",
    ]
    assert schemas["LlmsTxtDraft"]["properties"]["source"]["enum"] == [
        "manual",
        "pipeline",
    ]


def test_public_manifest_has_one_identical_markdown_representation():
    operation = SPEC["paths"]["/llms.txt"]["get"]
    assert operation["security"] == [{"PartnerKeyAuth": []}, {}]
    assert "parameters" not in operation
    assert set(operation["responses"]["200"]["content"]) == {"text/markdown"}


def test_unpublished_bootstrap_metadata_is_nullable():
    published = SPEC["components"]["schemas"]["LlmsTxtPublishedManifest"]
    assert published["required"] == ["content"]
    for field in ("run_id", "revision", "snapshot_fingerprint", "published_at"):
        assert published["properties"][field]["nullable"] is True
