#!/usr/bin/env python3
"""Licenses Route_Module dispatch UNIT tests (license-lifecycle, GitLab #3, task 4.2).

Minimal example-class tests for ``source/lambda/ui_backend/routes/licenses.py``: they prove the
uniform ``route(event, method, path)`` entry dispatches each ``/licenses*`` method+path
combination to the right ``services/license_service.py`` function, extracts the ``{license_id}``
path parameter, 404s an unmatched combination, and gates every handler behind
``@require_cognito_auth``.

Scope note: the comprehensive routing coverage (all eight paths resolving through the real
``resolve_route``, per-operation 401s, longest-prefix non-regression) is owned by **task 4.4**;
this file deliberately stays at module-level dispatch only and does not duplicate it.

Every service function is monkeypatched at the route module's import-site seam, so no DynamoDB is
touched and the route module's thin-adapter contract (no domain logic, no ``cm-licenses`` access)
is what gets asserted.

First-party imports are absolute, rooted at the canonical import root ``source/lambda/``.

_Requirements: 3.1, 3.2, 3.5, 4.1, 4.5_
"""

import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

import ui_backend.routes.licenses as licenses
from ui_backend.routes.licenses import route as licenses_route

# The route module's service seams, in (attribute name, expected call-arity) form.
_SERVICE_SEAMS = (
    "list_licenses",
    "create_license",
    "get_license",
    "update_license",
    "archive_license",
    "renew_license",
    "get_license_versions",
    "validate_license",
)


def _authorizer_claims() -> Dict[str, str]:
    """API Gateway Cognito authorizer claims that satisfy ``extract_cognito_user_context``."""
    return {
        "sub": "test-user-123",
        "email": "test@example.com",
        "custom:publisher_id": "test-publisher",
        "cognito:username": "test_user",
    }


def _event(
    method: str,
    path: str,
    *,
    body: Any = None,
    authed: bool = True,
    query: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build an API Gateway proxy event (Cognito-authed via authorizer claims by default)."""
    event: Dict[str, Any] = {
        "httpMethod": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }
    if authed:
        event["requestContext"] = {"authorizer": {"claims": _authorizer_claims()}}
    return event


def _invoke(event: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the Licenses route module as the dispatcher would."""
    return licenses_route(event, event["httpMethod"], event["path"])


class _Recorder:
    """Records which service seam was called, with what positional args."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []

    def stub(self, name: str):
        def _fn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            self.calls.append((name, args))
            return {"statusCode": 200, "headers": {}, "body": json.dumps({"called": name})}

        return _fn


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    """Patch every ``license_service`` seam the route module imported to a recorder."""
    rec = _Recorder()
    for name in _SERVICE_SEAMS:
        monkeypatch.setattr(licenses, name, rec.stub(name))
    return rec


class TestLicenseDispatch:
    """Each method+path combination reaches exactly one license_service function."""

    @pytest.mark.parametrize(
        "method,path,body,expected",
        [
            ("GET", "/licenses", None, "list_licenses"),
            ("POST", "/licenses", {"title": "t"}, "create_license"),
            ("GET", "/licenses/lic_abc", None, "get_license"),
            ("PUT", "/licenses/lic_abc", {"title": "t2"}, "update_license"),
            ("DELETE", "/licenses/lic_abc", None, "archive_license"),
            ("POST", "/licenses/lic_abc/renew", {"expiration_date": "2030-01-01"}, "renew_license"),
            ("GET", "/licenses/lic_abc/versions", None, "get_license_versions"),
            ("POST", "/licenses/lic_abc/validate", {"intended_use": "inference"}, "validate_license"),
        ],
    )
    def test_dispatches_to_expected_service_function(self, method, path, body, expected, recorder):
        """The dispatch table routes each operation to its designated service function."""
        response = _invoke(_event(method, path, body=body))

        assert response["statusCode"] == 200
        assert [name for name, _ in recorder.calls] == [expected]

    def test_license_id_path_parameter_is_extracted(self, recorder):
        """The ``{license_id}`` segment is parsed from the path and passed to the service."""
        _invoke(_event("GET", "/licenses/lic_from_path/versions"))

        name, args = recorder.calls[0]
        assert name == "get_license_versions"
        assert args[0] == "lic_from_path"

    def test_validate_stamps_license_id_from_path_onto_payload(self, recorder):
        """POST /licenses/{id}/validate sends the path id to the service, not just the body."""
        _invoke(_event("POST", "/licenses/lic_v/validate", body={"intended_use": "search"}))

        name, args = recorder.calls[0]
        assert name == "validate_license"
        assert args[0]["license_id"] == "lic_v"
        assert args[0]["intended_use"] == "search"

    def test_authenticated_username_is_passed_through(self, recorder):
        """The authenticated caller's username reaches the service for audit stamping."""
        _invoke(_event("POST", "/licenses", body={"title": "t"}))

        name, args = recorder.calls[0]
        assert name == "create_license"
        assert args[1] == "test_user"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("DELETE", "/licenses"),
            ("PUT", "/licenses"),
            ("GET", "/licenses/lic_abc/renew"),
            ("POST", "/licenses/lic_abc/versions"),
            ("GET", "/licenses/lic_abc/validate"),
            ("GET", "/licenses/lic_abc/unknown"),
        ],
    )
    def test_unmatched_method_or_path_returns_404(self, method, path, recorder):
        """An unmatched method/path combination 404s deliberately and calls no service function."""
        response = _invoke(_event(method, path))

        assert response["statusCode"] == 404
        assert json.loads(response["body"])["error"] == "License endpoint not found"
        assert recorder.calls == []


class TestCognitoGate:
    """Every handler is behind ``@require_cognito_auth`` (internal plane, Requirement 4.5)."""

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/licenses", None),
            ("POST", "/licenses", {"title": "t"}),
            ("GET", "/licenses/lic_abc", None),
            ("PUT", "/licenses/lic_abc", {"title": "t2"}),
            ("DELETE", "/licenses/lic_abc", None),
            ("POST", "/licenses/lic_abc/renew", None),
            ("GET", "/licenses/lic_abc/versions", None),
            ("POST", "/licenses/lic_abc/validate", {"intended_use": "inference"}),
        ],
    )
    def test_missing_credential_returns_401_and_skips_service(self, method, path, body, monkeypatch):
        """With no authorizer claims the gate 401s before any service seam is consulted."""

        def _boom(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            raise AssertionError("service invoked despite failed auth")

        for name in _SERVICE_SEAMS:
            monkeypatch.setattr(licenses, name, _boom)

        response = _invoke(_event(method, path, body=body, authed=False))

        assert response["statusCode"] == 401
        assert json.loads(response["body"])["error"] == "Invalid or missing Cognito token"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
