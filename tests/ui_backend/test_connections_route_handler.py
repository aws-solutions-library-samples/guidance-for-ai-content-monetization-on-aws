#!/usr/bin/env python3
"""Connections Route_Module handler UNIT tests (integration-adapters, GitLab #35, task 9.3).

Example-class (plain pytest) unit tests for the internal, Cognito-authenticated Connections
handler ``source/lambda/ui_backend/routes/connections.py`` (task 9.1). They exercise the uniform
``route(event, method, path)`` entry through the same path the ui_backend thin dispatcher uses,
against **fakes** injected at the handler's module-level seams — ``connections._store`` (the
``cm-connections`` :class:`~services.connections_store.ConnectionsStore`),
``connections._registry`` (the :class:`~integrations.registry.Registry`), and
``connections._secretsmanager_client`` (the ``secretsmanager`` client used for secret
persistence). No live AWS is touched: every seam is monkeypatched to an in-memory fake, so these
run deterministically in the gated unit tier (``-m "not integration"``).

What each test asserts (contract shapes from design.md "Connections API (internal, Cognito)"):

* **CRUD** — ``POST /connections`` creates (201) and **persists via the store**; ``GET
  /connections`` lists; ``PUT /connections/{id}`` updates; ``DELETE /connections/{id}`` deletes,
  and returns 404 via :class:`~services.connections_store.ConnectionNotFoundError` for an unknown
  id (Requirements 6.5, 7.1).
* **Catalog** — ``GET /connections/adapters`` returns the adapter catalog grouped by domain
  (Requirement 6.1, surfaced by the handler).
* **Test connection (fail-informative)** — ``POST /connections/{id}/test`` returns a
  ``TestResult``-shaped ``{ok, message}`` body; when the adapter's ``test()`` **raises** or the
  adapter is **not wired**, the handler returns ``ok: False`` with a diagnostic and a **200** — it
  does NOT let a ``test()`` failure propagate as a 5xx (Requirements 6.5, 7.3).
* **Status** — ``GET /connections/{id}/status`` returns ``{healthy, detail}`` (Requirement 6.8).
* **Mirror (read-only)** — ``GET /connections/mirrors/{domain}`` returns the mirror for a known
  domain with no write/edit affordance, and 404 for an unknown domain (Requirement 6.9). The
  route table exposes no POST/PUT/DELETE for the mirror path (read-only by construction).
* **Cognito gate** — every handler is behind ``@require_cognito_auth``: a request with no
  authorizer claims (and no valid credential) is rejected with 401 ``"Invalid or missing Cognito
  token"`` and the store/registry seam is never consulted — matching how the sibling ui_backend
  route characterization suites assert auth.

Scope discipline (task 9.3): this file only ADDS tests. It does not import or reference the
``TestResult`` type (avoids the pytest ``Test*`` collection warning) and names no helper/class
``Test*`` for a non-test purpose; the in-memory doubles are ``Fake*``/``_Stub*``.

Authored canonical-root-clean: every first-party import is absolute, rooted at the canonical
import root ``source/lambda/`` (``ui_backend.*``, ``services.*``), no relative/bare-sibling forms
and no ``sys.path`` manipulation.

_Requirements: 6.5, 6.9, 7.1, 7.3_
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

import ui_backend.routes.connections as connections
from services.connections_store import ConnectionNotFoundError
from ui_backend.routes.connections import route as connections_route

# CORS header names every response (success or error) must attach.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)


# --------------------------------------------------------------------------------------------
# Event builders + invocation helpers
# --------------------------------------------------------------------------------------------
def _authorizer_claims() -> Dict[str, str]:
    """API Gateway Cognito authorizer claims that satisfy ``extract_cognito_user_context``."""
    return {
        "sub": "test-user-123",
        "email": "test@example.com",
        "custom:publisher_id": "test-publisher",
        "cognito:username": "test_user",
    }


def _event(method: str, path: str, *, body: Any = None, authed: bool = True,
           auth_header: Optional[str] = None, query: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build an API Gateway proxy event (Cognito-authed via authorizer claims by default)."""
    event: Dict[str, Any] = {
        "httpMethod": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }
    if auth_header is not None:
        event["headers"]["Authorization"] = auth_header
    if authed:
        event["requestContext"] = {"authorizer": {"claims": _authorizer_claims()}}
    return event


def _invoke(event: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the Connections route module as the dispatcher would."""
    return connections_route(event, event["httpMethod"], event["path"])


def _body(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON body of a proxy response."""
    return json.loads(response["body"])


def _assert_cors(response: Dict[str, Any]) -> None:
    for header in _CORS_HEADERS:
        assert header in response["headers"]


# --------------------------------------------------------------------------------------------
# In-memory fakes for the module-level seams (_store / _registry / _secretsmanager_client)
# --------------------------------------------------------------------------------------------
@dataclass
class _StubConnection:
    """Minimal stand-in for a persisted :class:`~shared.utils.models.Connection` record.

    Carries only the attributes the handler serializes / the tests assert on — including only
    ``secret_ref`` (never a secret value), matching the real record's confidentiality invariant.
    """

    connection_id: str
    domain: str
    adapter: str
    brand: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    secret_ref: str = ""
    active: bool = True
    created_at: str = "2025-01-01T00:00:00+00:00"
    updated_at: str = "2025-01-01T00:00:00+00:00"


class FakeConnectionsStore:
    """In-memory ``cm-connections`` store double matching the handler's use of ``_store()``.

    Records persistence so tests can assert ``POST`` actually persisted, and reproduces the
    real store's :class:`ConnectionNotFoundError` on unknown-id update/delete (which the handler
    maps to 404).
    """

    def __init__(self) -> None:
        self.items: Dict[str, _StubConnection] = {}
        self.put_calls: List[_StubConnection] = []

    def put(self, connection: Any) -> Any:
        self.items[connection.connection_id] = connection
        self.put_calls.append(connection)
        return connection

    def get(self, connection_id: str) -> Optional[Any]:
        return self.items.get(connection_id)

    def list(self, domain: Optional[str] = None) -> List[Any]:
        values = list(self.items.values())
        if domain is not None:
            values = [c for c in values if c.domain == domain]
        return values

    def update(self, connection_id: str, fields: Dict[str, Any]) -> Any:
        existing = self.items.get(connection_id)
        if existing is None:
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.updated_at = "2025-02-02T00:00:00+00:00"
        return existing

    def delete(self, connection_id: str) -> None:
        if connection_id not in self.items:
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")
        del self.items[connection_id]


@dataclass
class _StubTestResult:
    """Duck-typed stand-in for the port ``TestResult`` (NOT imported — avoids Test* collection)."""

    ok: bool
    message: str


@dataclass
class _StubHealthStatus:
    """Duck-typed stand-in for the port ``HealthStatus``."""

    healthy: bool
    detail: str


class _StubAdapter:
    """Adapter double whose ``test()``/``status()`` return duck-typed result objects."""

    def __init__(self, test_result: Any = None, health: Any = None,
                 test_raises: Optional[Exception] = None) -> None:
        self._test_result = test_result
        self._health = health
        self._test_raises = test_raises

    def test(self) -> Any:
        if self._test_raises is not None:
            raise self._test_raises
        return self._test_result

    def status(self) -> Any:
        return self._health


class FakeRegistry:
    """Registry double: static catalog + a fixed resolved adapter (or None = not wired)."""

    def __init__(self, adapter: Any = None, catalog: Optional[Dict[str, List[Any]]] = None) -> None:
        self._adapter = adapter
        self._catalog = catalog if catalog is not None else self._default_catalog()

    @staticmethod
    def _default_catalog() -> Dict[str, List[Any]]:
        from shared.utils.models import AdapterDescriptor

        return {
            "content": [],
            "marketplace": [
                AdapterDescriptor(domain="marketplace", adapter="internal_dynamo",
                                  available=True, has_active_connection=False)
            ],
            "telemetry": [
                AdapterDescriptor(domain="telemetry", adapter="cloudwatch",
                                  available=True, has_active_connection=False)
            ],
            "access": [
                AdapterDescriptor(domain="access", adapter="aws_waf",
                                  available=True, has_active_connection=True)
            ],
        }

    def list_adapters(self) -> Dict[str, List[Any]]:
        return self._catalog

    def resolve(self, domain: str, brand: Optional[str] = None) -> Any:
        return self._adapter


class _StubSecretsManagerClient:
    """secretsmanager double so POST/PUT with a ``secret`` never touch AWS.

    ``create_secret`` returns a fabricated ARN; the handler persists only that reference.
    """

    class _Exceptions:
        class ResourceExistsException(Exception):
            pass

    def __init__(self) -> None:
        self.exceptions = self._Exceptions()
        self.created: List[str] = []

    def create_secret(self, Name: str, SecretString: str) -> Dict[str, str]:  # noqa: N803 (boto3 kwargs)
        self.created.append(SecretString)
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:000000000000:secret:{Name}"}

    def put_secret_value(self, SecretId: str, SecretString: str) -> Dict[str, str]:  # noqa: N803
        self.created.append(SecretString)
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:000000000000:secret:{SecretId}"}


@pytest.fixture
def fake_store(monkeypatch) -> FakeConnectionsStore:
    """Patch the handler's ``_store()`` seam to a shared in-memory fake for the test."""
    store = FakeConnectionsStore()
    monkeypatch.setattr(connections, "_store", lambda: store)
    return store


@pytest.fixture
def fake_secrets(monkeypatch) -> _StubSecretsManagerClient:
    """Patch the handler's ``_secretsmanager_client()`` seam so no real AWS is called."""
    client = _StubSecretsManagerClient()
    monkeypatch.setattr(connections, "_secretsmanager_client", lambda: client)
    return client


def _patch_registry(monkeypatch, registry: FakeRegistry) -> None:
    monkeypatch.setattr(connections, "_registry", lambda: registry)


# ============================================================================================
# CRUD  (Requirements 6.5, 7.1)
# ============================================================================================
class TestConnectionCrud:
    """POST creates + persists (201); GET lists; PUT updates; DELETE deletes / 404."""

    def test_post_connections_creates_201_and_persists_via_store(self, fake_store, fake_secrets):
        """POST /connections -> 201, response carries secret_ref only, and the store persisted it."""
        event = _event("POST", "/connections", body={
            "domain": "marketplace",
            "adapter": "internal_dynamo",
            "brand": "acme",
            "config": {"region": "us-west-2"},
            "secret": "super-secret-value",
        })

        response = _invoke(event)

        assert response["statusCode"] == 201
        _assert_cors(response)
        conn = _body(response)["connection"]
        assert conn["domain"] == "marketplace"
        assert conn["adapter"] == "internal_dynamo"
        assert conn["brand"] == "acme"
        # Persisted via the store (CRUD actually wrote through the seam).
        assert len(fake_store.put_calls) == 1
        stored = fake_store.put_calls[0]
        assert stored.connection_id == conn["connection_id"]
        assert stored.connection_id in fake_store.items
        # Secret went to Secrets Manager; only the ARN reference is persisted/returned.
        assert fake_secrets.created == ["super-secret-value"]
        assert conn["secret_ref"].startswith("arn:aws:secretsmanager:")
        assert "secret" not in conn

    def test_post_connections_missing_domain_returns_400(self, fake_store, fake_secrets):
        """POST /connections without domain -> 400 (validation) and nothing persisted."""
        event = _event("POST", "/connections", body={"adapter": "internal_dynamo"})

        response = _invoke(event)

        assert response["statusCode"] == 400
        assert fake_store.put_calls == []

    def test_get_connections_lists_persisted_records(self, fake_store):
        """GET /connections lists connections (non-secret config + secret_ref only)."""
        fake_store.items["conn_a"] = _StubConnection(
            connection_id="conn_a", domain="telemetry", adapter="cloudwatch", secret_ref="arn:secret:a"
        )
        fake_store.items["conn_b"] = _StubConnection(
            connection_id="conn_b", domain="access", adapter="aws_waf"
        )

        response = _invoke(_event("GET", "/connections"))

        assert response["statusCode"] == 200
        _assert_cors(response)
        conns = _body(response)["connections"]
        ids = {c["connection_id"] for c in conns}
        assert ids == {"conn_a", "conn_b"}
        # Response exposes secret_ref, never a secret value field.
        for c in conns:
            assert "secret_ref" in c
            assert "secret" not in c

    def test_put_connections_updates_via_store(self, fake_store, fake_secrets):
        """PUT /connections/{id} applies the update through the store and returns the record."""
        fake_store.items["conn_x"] = _StubConnection(
            connection_id="conn_x", domain="access", adapter="aws_waf", active=True
        )

        event = _event("PUT", "/connections/conn_x", body={"active": False, "config": {"k": "v"}})
        response = _invoke(event)

        assert response["statusCode"] == 200
        conn = _body(response)["connection"]
        assert conn["active"] is False
        assert conn["config"] == {"k": "v"}
        assert fake_store.items["conn_x"].active is False

    def test_put_unknown_connection_returns_404(self, fake_store, fake_secrets):
        """PUT /connections/{id} for an unknown id -> 404 via ConnectionNotFoundError."""
        response = _invoke(_event("PUT", "/connections/nope", body={"active": False}))

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "Connection not found"

    def test_delete_connections_deletes(self, fake_store):
        """DELETE /connections/{id} removes the record and reports deleted."""
        fake_store.items["conn_del"] = _StubConnection(
            connection_id="conn_del", domain="telemetry", adapter="cloudwatch"
        )

        response = _invoke(_event("DELETE", "/connections/conn_del"))

        assert response["statusCode"] == 200
        assert _body(response)["deleted"] is True
        assert "conn_del" not in fake_store.items

    def test_delete_unknown_connection_returns_404(self, fake_store):
        """DELETE /connections/{id} for an unknown id -> 404 via ConnectionNotFoundError."""
        response = _invoke(_event("DELETE", "/connections/missing"))

        assert response["statusCode"] == 404
        assert _body(response)["error"] == "Connection not found"


# ============================================================================================
# Catalog  (Requirement 6.1)
# ============================================================================================
class TestAdapterCatalog:
    """GET /connections/adapters returns the catalog grouped by domain."""

    def test_get_adapters_returns_catalog_grouped_by_domain(self, monkeypatch):
        """GET /connections/adapters -> catalog with all four domain keys, adapters nested per domain."""
        _patch_registry(monkeypatch, FakeRegistry())

        response = _invoke(_event("GET", "/connections/adapters"))

        assert response["statusCode"] == 200
        _assert_cors(response)
        adapters = _body(response)["adapters"]
        assert set(adapters.keys()) == {"content", "marketplace", "telemetry", "access"}
        # access domain carries the aws_waf adapter descriptor, grouped under its domain.
        assert adapters["access"][0]["adapter"] == "aws_waf"
        assert adapters["access"][0]["domain"] == "access"
        assert adapters["access"][0]["available"] is True


# ============================================================================================
# Test connection — fail-informative  (Requirements 6.5, 7.3)
# ============================================================================================
class TestTestConnection:
    """POST /connections/{id}/test returns TestResult-shaped {ok, message}, never a 5xx on failure."""

    def test_test_connection_success_returns_ok_true_shape(self, fake_store, monkeypatch):
        """A passing adapter.test() -> 200 {ok: True, message}."""
        fake_store.items["conn_ok"] = _StubConnection(
            connection_id="conn_ok", domain="access", adapter="aws_waf"
        )
        adapter = _StubAdapter(test_result=_StubTestResult(ok=True, message="reachable"))
        _patch_registry(monkeypatch, FakeRegistry(adapter=adapter))

        response = _invoke(_event("POST", "/connections/conn_ok/test"))

        assert response["statusCode"] == 200
        payload = _body(response)
        assert payload == {"ok": True, "message": "reachable"}

    def test_test_connection_adapter_failure_is_fail_informative_no_5xx(self, fake_store, monkeypatch):
        """A failing adapter.test() -> 200 {ok: False, message} (diagnostic), NOT a 5xx."""
        fake_store.items["conn_bad"] = _StubConnection(
            connection_id="conn_bad", domain="access", adapter="aws_waf"
        )
        adapter = _StubAdapter(test_result=_StubTestResult(ok=False, message="auth failed: 403"))
        _patch_registry(monkeypatch, FakeRegistry(adapter=adapter))

        response = _invoke(_event("POST", "/connections/conn_bad/test"))

        assert response["statusCode"] == 200
        payload = _body(response)
        assert payload["ok"] is False
        assert payload["message"] == "auth failed: 403"

    def test_test_connection_raising_adapter_does_not_500(self, fake_store, monkeypatch):
        """When adapter.test() RAISES, the handler still returns 200 ok:False with a diagnostic.

        A test() failure must not propagate past the API as a 5xx (Requirements 6.5, 7.3).
        """
        fake_store.items["conn_raise"] = _StubConnection(
            connection_id="conn_raise", domain="access", adapter="aws_waf"
        )
        adapter = _StubAdapter(test_raises=RuntimeError("boto exploded"))
        _patch_registry(monkeypatch, FakeRegistry(adapter=adapter))

        response = _invoke(_event("POST", "/connections/conn_raise/test"))

        assert response["statusCode"] == 200
        assert response["statusCode"] < 500
        payload = _body(response)
        assert payload["ok"] is False
        assert "boto exploded" in payload["message"]

    def test_test_connection_not_wired_returns_ok_false(self, fake_store, monkeypatch):
        """When no adapter is wired (Registry.resolve -> None) -> 200 ok:False, not a 5xx."""
        fake_store.items["conn_unwired"] = _StubConnection(
            connection_id="conn_unwired", domain="access", adapter="aws_waf"
        )
        _patch_registry(monkeypatch, FakeRegistry(adapter=None))

        response = _invoke(_event("POST", "/connections/conn_unwired/test"))

        assert response["statusCode"] == 200
        payload = _body(response)
        assert payload["ok"] is False
        assert "not wired" in payload["message"].lower()

    def test_test_connection_unknown_id_returns_404(self, fake_store, monkeypatch):
        """POST /connections/{id}/test for an unknown connection -> 404 (not a test result)."""
        _patch_registry(monkeypatch, FakeRegistry(adapter=None))

        response = _invoke(_event("POST", "/connections/ghost/test"))

        assert response["statusCode"] == 404


# ============================================================================================
# Status  (Requirement 6.8)
# ============================================================================================
class TestConnectionStatus:
    """GET /connections/{id}/status returns {healthy, detail}."""

    def test_status_returns_healthy_detail_shape(self, fake_store, monkeypatch):
        """A resolved adapter.status() -> 200 {healthy, detail}."""
        fake_store.items["conn_s"] = _StubConnection(
            connection_id="conn_s", domain="telemetry", adapter="cloudwatch"
        )
        adapter = _StubAdapter(health=_StubHealthStatus(healthy=True, detail="ok"))
        _patch_registry(monkeypatch, FakeRegistry(adapter=adapter))

        response = _invoke(_event("GET", "/connections/conn_s/status"))

        assert response["statusCode"] == 200
        assert _body(response) == {"healthy": True, "detail": "ok"}

    def test_status_not_wired_returns_healthy_false(self, fake_store, monkeypatch):
        """No wired adapter -> 200 {healthy: False, detail}."""
        fake_store.items["conn_s2"] = _StubConnection(
            connection_id="conn_s2", domain="telemetry", adapter="cloudwatch"
        )
        _patch_registry(monkeypatch, FakeRegistry(adapter=None))

        response = _invoke(_event("GET", "/connections/conn_s2/status"))

        assert response["statusCode"] == 200
        assert _body(response)["healthy"] is False


# ============================================================================================
# Mirror — read-only  (Requirement 6.9)
# ============================================================================================
class TestMirrorReadOnly:
    """GET /connections/mirrors/{domain} is read-only; unknown domain -> 404; no write path."""

    def test_get_mirror_known_domain_returns_mirror(self, fake_store):
        """GET /connections/mirrors/marketplace -> 200 with the (read-only) mirror payload."""
        response = _invoke(_event("GET", "/connections/mirrors/marketplace"))

        assert response["statusCode"] == 200
        payload = _body(response)
        assert payload["domain"] == "marketplace"
        assert "mirror" in payload

    def test_get_mirror_unknown_domain_returns_404(self, fake_store):
        """GET /connections/mirrors/{unknown} -> 404 (no mirror for the domain)."""
        response = _invoke(_event("GET", "/connections/mirrors/not-a-domain"))

        assert response["statusCode"] == 404

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_mirror_has_no_write_path(self, method, fake_store):
        """The mirror endpoint is read-only: no write verb is routed to it (edit affordance absent).

        Any write method against the mirror path falls through the route table to a 404 rather
        than mutating the mirror (federation rule — mirror authored on the external side).
        """
        response = _invoke(_event(method, "/connections/mirrors/marketplace", body={"mirror": {"x": 1}}))

        assert response["statusCode"] == 404


# ============================================================================================
# Cognito gate  (Requirement 7.1)
# ============================================================================================
class TestCognitoGate:
    """Every handler is behind @require_cognito_auth (internal only)."""

    def _assert_unauthorized(self, response: Dict[str, Any]) -> None:
        assert response["statusCode"] == 401
        assert _body(response)["error"] == "Invalid or missing Cognito token"
        _assert_cors(response)

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/connections/adapters", None),
            ("GET", "/connections", None),
            ("POST", "/connections", {"domain": "access", "adapter": "aws_waf"}),
            ("PUT", "/connections/conn_x", {"active": False}),
            ("DELETE", "/connections/conn_x", None),
            ("POST", "/connections/conn_x/test", None),
            ("GET", "/connections/conn_x/status", None),
            ("GET", "/connections/mirrors/marketplace", None),
        ],
    )
    def test_missing_credential_returns_401_and_skips_seams(self, method, path, body, monkeypatch):
        """A request with no authorizer claims/credential -> 401 for every handler.

        The store/registry seams are patched to raise if touched, proving the Cognito gate
        rejects before any domain work runs.
        """
        def _boom(*args, **kwargs):
            raise AssertionError("seam invoked despite failed auth")

        monkeypatch.setattr(connections, "_store", _boom)
        monkeypatch.setattr(connections, "_registry", _boom)
        monkeypatch.setattr(connections, "_secretsmanager_client", _boom)

        response = _invoke(_event(method, path, body=body, authed=False))

        self._assert_unauthorized(response)

    def test_invalid_bearer_token_returns_401(self, monkeypatch):
        """A malformed bearer token (no authorizer claims) -> 401, seams untouched."""
        def _boom(*args, **kwargs):
            raise AssertionError("seam invoked despite failed auth")

        monkeypatch.setattr(connections, "_store", _boom)
        monkeypatch.setattr(connections, "_registry", _boom)

        response = _invoke(
            _event("GET", "/connections", authed=False, auth_header="Bearer not-a-real-jwt")
        )

        self._assert_unauthorized(response)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
