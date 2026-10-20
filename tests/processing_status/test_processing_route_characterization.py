"""Characterization test for the processing domain route module (task 4.2, REQUIRED GATE).

This is the per-domain route-equivalence Characterization_Test that gated the ``/processing/status``
dispatcher flip to the new route module (task 4.4, Requirements 16.1, 17.4, 17.5). It pins the
behavior of ``ui_backend.routes.processing`` (delegating to
``services.processing_jobs_service.ProcessingJobsService``) against the characterized contract of
the pre-refactor inline ``ui_backend.handler.handle_processing_status``.

Properties validated:

* **Property 1 — Route equivalence** (Requirements 2.1, 2.2, 2.3): for the same request the route
  returns the same HTTP status code, the same response body (fields, nesting, values), and the same
  CORS headers as the inline handler characterized here — across the mock path, the real path, the
  real-fails-then-graceful-fallback path, and the error path.
* **Property 2 — Auth preserved** (Requirements 2.5, 6.6): a missing or invalid Cognito credential
  yields ``401`` via the unchanged ``@require_cognito_auth`` wrapper, without invoking the
  processing-jobs service.

Golden-master baseline (post-flip form): before task 4.4 this suite compared the route module
against the inline ``ui_backend/handler.py:handle_processing_status`` handler. That inline handler
was **deleted in the same change that flipped the dispatcher** to ``routes/processing.py``
(Requirement 16.3), so the old-vs-new diff is no longer possible. The inline handler's characterized
output is therefore pinned here as an explicit golden baseline, constructed via the shared
``create_success_response`` / ``create_error_response`` helpers (independent of the route module's
own code path) — the same helpers both the inline handler and the route module use. The route
module must still reproduce that exact status/body/CORS contract. This mirrors how the taxonomy
Characterization_Test (task 3.3) was converted to golden-master form after its inline handler was
deleted.

Canonical-root-clean (Requirement 17.6): every first-party import below is absolute and rooted at
the canonical import root ``source/lambda/`` (resolved by ``pytest.ini`` ``pythonpath`` and
``tests/conftest.py``); there are no relative or bare-sibling imports and no ``sys.path``
manipulation.
"""

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import get_validator
from ui_backend.routes.processing import route

# Patch target for the route/service under test. ProcessingJobsService imports the real-data
# helper at module top level, so the bound name lives in the services.processing_jobs_service
# namespace. Real-data-only (wave0 Req 9): the mock generator / feature-flag / graceful-fallback
# machinery was removed, so there is a single retrieval function to patch.
_SVC_REAL = "services.processing_jobs_service.get_real_processing_status"

# Deterministic status payloads (shape is opaque to this test; equivalence is what matters).
REAL_STATUS = {
    "jobs": [{"job_id": "job_real_1", "status": "completed", "created_at": "2025-01-15T10:00:00Z"}],
    "active_jobs": 4,
    "queued_jobs": 2,
    "system_status": "healthy",
    "source": "real",
}

CORS_HEADER_KEYS = {
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
}


@pytest.fixture(autouse=True)
def _disable_response_validation():
    """Neutralize the shared ``@validate_response`` decorator for a deterministic golden baseline.

    The route is wrapped by the ``@validate_response`` decorator, which (outside production) injects
    a ``_validation`` block containing a per-call ``timestamp`` into the response body. The golden
    baselines are built with the raw ``create_success_response`` / ``create_error_response`` helpers
    (which never add ``_validation``), so disabling validation on the shared validator singleton
    keeps the route's body directly comparable to the baseline without that timestamp artifact.
    """
    validator = get_validator()
    original = validator.validation_enabled
    validator.validation_enabled = False
    try:
        yield
    finally:
        validator.validation_enabled = original


def _authed_event():
    """API Gateway event carrying a valid Cognito authorizer context."""
    return {
        "httpMethod": "GET",
        "path": "/processing/status",
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer valid-token"},
        "body": None,
        "queryStringParameters": None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "test-user-123",
                    "email": "test@example.com",
                    "custom:publisher_id": "test-publisher",
                }
            }
        },
    }


def _call_new(event):
    """Invoke the new route module's uniform dispatch entry."""
    return route(event, "GET", "/processing/status")


class _StatusPatches:
    """Applies the real-data-retrieval patch to the service binding site (real-data-only)."""

    def __init__(self, real_return=None, real_exc=None):
        self.real_fn = MagicMock(side_effect=real_exc) if real_exc else MagicMock(return_value=real_return)

    def apply(self, stack: ExitStack):
        stack.enter_context(patch(_SVC_REAL, self.real_fn))
        return self


def _normalize_body(raw_body):
    """Parse a response body, dropping the per-call random ``request_id`` error field.

    ``create_error_response`` stamps every error body with a fresh ``req_<uuid>`` identifier, so it
    differs between any two invocations. It is a non-behavioral artifact; equivalence is asserted on
    the rest of the body, with the presence of a ``request_id`` checked separately where relevant.
    """
    data = json.loads(raw_body)
    if isinstance(data, dict):
        data.pop("request_id", None)
    return data


def _assert_equivalent(golden_resp, new_resp):
    """Assert route equivalence: same status code, same body, same CORS headers (Property 1)."""
    assert new_resp["statusCode"] == golden_resp["statusCode"]
    assert _normalize_body(new_resp["body"]) == _normalize_body(golden_resp["body"])
    # CORS headers preserved: new response carries at least the same CORS header names + values.
    for key in CORS_HEADER_KEYS:
        assert key in golden_resp["headers"]
        assert new_resp["headers"].get(key) == golden_resp["headers"][key]


# --------------------------------------------------------------------------------------------
# Property 1 — Route equivalence (against the pinned golden baseline of the deleted inline handler)
# --------------------------------------------------------------------------------------------

class TestProcessingRouteEquivalence:
    """Property 1: the route reproduces the inline handler's characterized status/body/CORS."""

    def test_real_path_equivalent(self):
        """Real data available ⇒ route returns the real status identically (real-data-only)."""
        event = _authed_event()
        with ExitStack() as stack:
            _StatusPatches(real_return=REAL_STATUS).apply(stack)
            new_resp = _call_new(event)

        golden = create_success_response(REAL_STATUS)
        assert new_resp["statusCode"] == 200
        assert json.loads(new_resp["body"]) == REAL_STATUS
        _assert_equivalent(golden, new_resp)

    def test_error_path_equivalent(self):
        """Underlying retrieval raises ⇒ route returns 500 'Failed to retrieve processing status'."""
        event = _authed_event()
        with ExitStack() as stack:
            _StatusPatches(real_exc=RuntimeError("boom")).apply(stack)
            new_resp = _call_new(event)

        golden = create_error_response(500, "Failed to retrieve processing status")
        assert new_resp["statusCode"] == 500
        assert json.loads(new_resp["body"])["error"] == "Failed to retrieve processing status"
        _assert_equivalent(golden, new_resp)


# --------------------------------------------------------------------------------------------
# Property 2 — Auth preserved
# --------------------------------------------------------------------------------------------

class TestProcessingAuthPreserved:
    """Property 2: missing/invalid Cognito credential ⇒ 401 via @require_cognito_auth, no service call."""

    def test_missing_credential_returns_401(self):
        """No authorizer context and no Authorization header ⇒ 401, service not invoked."""
        event = {
            "httpMethod": "GET",
            "path": "/processing/status",
            "headers": {"Content-Type": "application/json"},
            "body": None,
            "queryStringParameters": None,
        }
        with ExitStack() as stack:
            patches = _StatusPatches(real_return=REAL_STATUS).apply(stack)
            new_resp = _call_new(event)

        assert new_resp["statusCode"] == 401
        # Auth short-circuits before any domain logic: the service data function never runs.
        assert patches.real_fn.call_count == 0
        # CORS headers preserved on the 401.
        for key in CORS_HEADER_KEYS:
            assert key in new_resp["headers"]

    def test_invalid_credential_returns_401(self):
        """A malformed Bearer token (no valid Cognito context) ⇒ 401, service not invoked."""
        event = {
            "httpMethod": "GET",
            "path": "/processing/status",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer not-a-real-jwt"},
            "body": None,
            "queryStringParameters": None,
        }
        with ExitStack() as stack:
            patches = _StatusPatches(real_return=REAL_STATUS).apply(stack)
            new_resp = _call_new(event)

        assert new_resp["statusCode"] == 401
        assert patches.real_fn.call_count == 0


if __name__ == "__main__":
    pytest.main([__file__])
