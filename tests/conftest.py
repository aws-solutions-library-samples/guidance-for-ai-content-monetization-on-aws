"""
Root conftest.py — adds source paths to sys.path for all tests and sets import-time env defaults.

This ensures tests can import from source/lambda/, source/agent/, etc. without needing sys.path
manipulation in every test file.

It also sets default environment variables required at *module load time* by application modules.
Task 5.3 of the lambda-handler-decomposition spec flipped the ``/content*`` dispatcher entry to
``ui_backend/routes/content.py``, so importing ``ui_backend.handler`` now transitively imports
``content_management``, which builds its DynamoDB resources and reads ``CONTENT_TABLE_NAME`` /
``JOBS_TABLE_NAME`` / ``CONTENT_BUCKET_NAME`` at import (via ``os.environ[...]``). Providing these
as ``setdefault`` values lets any suite that imports the handler collect without an import-time
``KeyError``; suites needing real values still set their own (setdefault never overrides). No AWS
calls are made at import — only client/table objects are constructed.
"""

import os
import sys

import pytest

# Import-time environment defaults (see module docstring). ``setdefault`` so a caller or CI that
# provides real values is never overridden.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

# Project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Add source paths so tests can import application modules.
#
# GitLab #11 (Requirement 6, Option A): the single canonical import root is
# ``source/lambda`` — byte-for-byte identical to every deployed Lambda zip root — so a test
# import that resolves here resolves in production. The former flattening roots
# (``source/lambda/shared``, ``source/lambda/ui_backend``, ``source/lambda/services``,
# ``source/lambda/shared/services``) are DELIBERATELY NOT added: they created phantom top-level
# packages (``utils``, ``auth``, ``handlers``, bare ``handler``/``routes``/service modules) that
# resolve in tests but NOT in the zip — the ``from utils.X`` bug class. First-party Lambda imports
# must now be canonical (``from shared.utils.X``, ``from services.X``, bare top-level handler
# modules).
#
# ``source`` and the repo root are retained ONLY for non-Lambda first-party packages that are not
# deployed as Lambda zips and so are outside the zip-fidelity constraint: ``source`` lets the
# ``mock_data_generator`` dev tool import ``from mock_data_generator.X`` (as its own CLI does), and
# the repo root lets ``tests/infrastructure/`` import ``from infrastructure.X``. Neither can alias
# Lambda code (``from lambda.X`` is a syntax error), so they cannot reintroduce the bug class.
paths_to_add = [
    os.path.join(PROJECT_ROOT, "source", "lambda"),
    os.path.join(PROJECT_ROOT, "source"),
    PROJECT_ROOT,
]

for path in paths_to_add:
    if path not in sys.path:
        sys.path.insert(0, path)


# ---------------------------------------------------------------------------
# Documented test quarantine (GitLab #11, Req 4.5 / task 4.2 + 5.2).
#
# Skips exactly the node-ids listed in tests/quarantine_registry.py, each with its stated reason,
# so quarantined tests are explicitly documented-and-skipped rather than silently omitted. This keeps
# the gated suite green without editing assertions to mask behavior or deleting genuinely-useful
# tests; every quarantine is recorded as a gap in the Coverage_Inventory.
def _wave0_reset_shared_state():
    """Reset cross-test module-level singletons/caches that would otherwise leak between
    co-run tests (wave0 Phase 5 / Req 16).

    After the Phase-3 feature-flag removal, the remaining shared-state surfaces are:
    - ``services.content_service.taxonomy_manager`` — a module-level ``TaxonomyManager`` singleton
      with a 1-hour-TTL ``_cache``; without a reset, one test's (moto-backed) taxonomy is served to
      the next, and its ``.table`` stays bound to a stale/foreign DynamoDB context.
    - ``content_pipeline.geo_enricher`` module globals (``_bedrock`` / ``_taxonomy_block`` /
      ``_valid_category_ids``).
    Each reset is best-effort: a module that isn't importable in a given suite is simply skipped.
    """
    # content_service taxonomy singleton
    try:
        import services.content_service as _cs

        tm = getattr(_cs, "taxonomy_manager", None)
        if tm is not None:
            tm._cache = {}
            tm._cache_timestamp = None
            try:
                import boto3

                tm.table = boto3.resource("dynamodb").Table(
                    os.environ.get("TAXONOMY_TABLE_NAME", getattr(tm, "table_name", "cm-taxonomy"))
                )
            except Exception:  # noqa: BLE001 — best-effort rebind
                pass
    except Exception:  # noqa: BLE001 — module not importable in this suite
        pass

    # content_pipeline geo_enricher module globals
    try:
        import content_pipeline.geo_enricher as _ge

        for _attr in ("_bedrock", "_taxonomy_block", "_valid_category_ids"):
            if hasattr(_ge, _attr):
                setattr(_ge, _attr, None)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _wave0_isolate_shared_state():
    """Autouse: reset shared module state and restore os.environ around every test so that
    suite/collection order (including pytest-randomly) does not affect outcomes (Req 16/17)."""
    saved_env = dict(os.environ)
    _wave0_reset_shared_state()
    try:
        yield
    finally:
        _wave0_reset_shared_state()
        os.environ.clear()
        os.environ.update(saved_env)


# TM-02 (security-hardening-phase2): CORS presence for characterization/equivalence suites.
#
# cors_headers() returns a CONFIGURED origin, or OMITS the header entirely when no allow-list is
# resolvable (fail-safe, never "*"). Characterization/equivalence suites assert the origin header is
# PRESENT and identical across code paths — true only with an allow-list configured (in a deployment
# from ALLOWED_ORIGINS / the /cm/ui/origin SSM parameter; absent in the unit env). This fixture sets
# a concrete ALLOWED_ORIGINS for exactly those suites, deterministically regardless of test order.
#
# It explicitly does NOT apply to tests/utils/, whose resolver tests delenv ALLOWED_ORIGINS to
# exercise the SSM / fail-safe-omission paths — setting it there would mask the fail-safe contract.
_CORS_PRESENCE_SUITES = (
    f"tests{os.sep}content_management{os.sep}",
    f"tests{os.sep}ui_backend{os.sep}",
    f"tests{os.sep}analytics_agent{os.sep}",
    f"tests{os.sep}analytics{os.sep}",
    f"tests{os.sep}processing_status{os.sep}",
    f"tests{os.sep}bot_control{os.sep}",
)
_TEST_ALLOWED_ORIGIN = "https://portal.test.example"


@pytest.fixture(autouse=True)
def _cors_allowlist_for_presence_suites(request, _wave0_isolate_shared_state):
    """Set ALLOWED_ORIGINS for suites that assert the CORS origin header is present.

    Depends on _wave0_isolate_shared_state so it runs INSIDE that fixture's os.environ
    save/restore window — the value is scoped to the test and never leaks. No-op for every other
    suite (notably tests/utils/, which tests fail-safe omission with ALLOWED_ORIGINS unset).
    """
    node_path = str(getattr(request, "fspath", ""))
    if any(suite in node_path for suite in _CORS_PRESENCE_SUITES):
        os.environ["ALLOWED_ORIGINS"] = _TEST_ALLOWED_ORIGIN
    yield


def pytest_collection_modifyitems(config, items):
    try:
        from tests.quarantine_registry import QUARANTINED_NODE_IDS, reason_for
    except ImportError:
        return
    for item in items:
        if item.nodeid in QUARANTINED_NODE_IDS:
            item.add_marker(
                __import__("pytest").mark.skip(
                    reason=f"QUARANTINE (GitLab #11): {reason_for(item.nodeid)}"
                )
            )


# ---------------------------------------------------------------------------
# Integration_Tier deployed-environment resolver.
#
# The Integration_Tier (`pytest -m integration`, i.e. `make test-integration`) targets a REAL
# deployed environment. Rather than hardcode API URLs / Cognito ids / table names in test files,
# we resolve them ONCE from the CloudFormation Outputs of ``ContentMonetizationStack`` (the single
# source of truth) and populate ``os.environ`` with ``setdefault`` — so an explicitly-exported env
# var always wins, and a different environment can be targeted without code changes.
#
# This runs in ``pytest_configure`` (before any collection / xunit setup_class), and ONLY when the
# active marker expression is exactly ``integration`` — so the Unit_Tier gate (`make test`, which
# runs ``-m "not integration"``) NEVER touches AWS. Every lookup is best-effort: on any failure the
# Integration_Tier tests simply fall back to their own defaults / env-guards / skips.
# ---------------------------------------------------------------------------
def _resolve_deployed_integration_env():
    import json

    try:
        import boto3
    except Exception:  # noqa: BLE001
        return

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
    try:
        cfn = boto3.client("cloudformation", region_name=region)
        outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cfn.describe_stacks(StackName="ContentMonetizationStack")["Stacks"][0].get("Outputs", [])
        }
    except Exception:  # noqa: BLE001 — stack absent / no creds: leave env untouched
        return

    # CloudFormation Output key -> environment variable(s) the integration tests read.
    output_to_env = {
        "ApiGatewayUrl": ("TEST_API_URL", "IAB_API_BASE_URL"),
        "UserPoolId": ("TEST_USER_POOL_ID",),
        "UserPoolClientId": ("TEST_CLIENT_ID",),
        "ContentTableName": ("TEST_CONTENT_TABLE", "CONTENT_TABLE_NAME"),
        "JobsTableName": ("JOBS_TABLE_NAME",),
        "ContentBucketName": ("CONTENT_BUCKET_NAME",),
        "PartnerTableName": ("TEST_PARTNER_TABLE",),
        "PartnerApiUrl": ("PARTNER_API_URL", "TEST_PARTNER_API_URL"),
    }
    # Unit-tier import-safety placeholders (set at conftest import above). For the Integration_Tier
    # these MUST be overridden by the real deployed values — otherwise handlers that bind their
    # DynamoDB table at import time (e.g. services.content_service.content_table) would target the
    # non-existent placeholder table and every read would 500 (ResourceNotFoundException). A genuine
    # caller-exported value (not a placeholder) still wins.
    _unit_placeholders = {"test-content-table", "test-jobs-table", "test-content-bucket"}
    for output_key, env_keys in output_to_env.items():
        value = outputs.get(output_key)
        if value:
            for env_key in env_keys:
                if os.environ.get(env_key) in _unit_placeholders:
                    os.environ[env_key] = value  # deployed value overrides the unit-tier placeholder
                else:
                    os.environ.setdefault(env_key, value)

    # Resolve the test partner key value from API Gateway (best-effort). The stack exports only
    # the key id (TestPartnerApiKeyId); the value is read on demand and never stored (Q6).
    key_id = outputs.get("TestPartnerApiKeyId")
    if key_id and not os.environ.get("TEST_PARTNER_KEY"):
        try:
            value = boto3.client("apigateway", region_name=region).get_api_key(apiKey=key_id, includeValue=True)["value"]
            os.environ.setdefault("TEST_PARTNER_KEY", value)
            os.environ.setdefault("PARTNER_API_KEY", value)
            os.environ.setdefault("IAB_PARTNER_KEY", value)
        except Exception:  # noqa: BLE001
            pass


def pytest_configure(config):
    """Populate the deployed-env config for the Integration_Tier only (never the unit gate)."""
    markexpr = (config.getoption("-m") or "").strip()
    if markexpr == "integration":
        _resolve_deployed_integration_env()
