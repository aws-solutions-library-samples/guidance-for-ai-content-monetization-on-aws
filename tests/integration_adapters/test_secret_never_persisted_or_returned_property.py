"""Property test for Property 4 (integration-adapters, GitLab #35, task 9.2).

Feature: integration-adapters, Property 4: A secret value never appears in a persisted record or
any API response

Validates: Requirements 6.4, 7.2.

*For any* connection-activation input containing a secret, the persisted ``cm-connections`` record
AND every Connection API response contain only the Secrets Manager reference (``secret_ref``) and
non-secret config — no field ever equals the input secret value.

This drives the real Connections route handler (``ui_backend/routes/connections.py``, task 9.1)
through its create / update / list paths with a generated secret, against IN-MEMORY FAKES (no live
AWS): a fake ``cm-connections`` store (dict-backed) and a fake Secrets Manager (records the put
secret, returns an ARN reference). The handler's module-level seams (``_store`` /
``_secretsmanager_client``) are monkeypatched so no AWS is touched, per the design "Testing
Strategy" → property-based tests, fakes-not-AWS. Hypothesis runs >=100 examples
(``@settings(max_examples=100)``). One property -> one property-based test.

After each drive the test asserts, for the generated secret value ``s``:
  (a) no field of the persisted store item equals ``s`` (only ``secret_ref`` — an ARN — is held), and
  (b) no serialized API response body (JSON string) contains ``s``.

Generated secrets are distinctive (a fixed unlikely-to-collide prefix + random suffix) so the
containment check in (b) is meaningful and cannot accidentally match config/ARN text.

Scope discipline: this module adds ONLY the test + its local fakes. It does not modify the handler,
store, or model code. Collection-warning note: it does not import ``TestResult`` (or any
``Test*``-named model), and no helper is named ``Test*``, to avoid pytest's ``Test*`` collection
glob warning.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from services.connections_store import ConnectionNotFoundError
from shared.utils.models import Connection
from ui_backend.routes import connections as connections_route

# A distinctive prefix makes the "secret appears in output" containment check meaningful: it is
# unlikely to collide with config values, ARNs, timestamps, or ids the handler emits.
_SECRET_PREFIX = "S3CR3T-DO-NOT-LEAK-"


# ─────────────────────────────────────────────────────────────────────────────
# Local in-memory fakes (no AWS): a dict-backed cm-connections store + a fake
# Secrets Manager that records the put secret and returns an ARN reference.
# ─────────────────────────────────────────────────────────────────────────────


class FakeConnectionsStore:
    """In-memory ``cm-connections`` store standing in for :class:`ConnectionsStore`.

    Backed by a plain dict of ``connection_id -> Connection``. Mirrors the real store's CRUD
    surface used by the handler (``get`` / ``put`` / ``update`` / ``delete`` / ``list``) and,
    crucially, persists via :meth:`Connection.to_item` so the *item* the test inspects is the same
    DynamoDB-shaped dict the real store would write — the secret-confidentiality invariant is a
    property of the ``Connection`` dataclass (no secret-value field), which both stores share.
    """

    _ALLOWED_UPDATE_FIELDS = frozenset(
        {"domain", "adapter", "brand", "config", "secret_ref", "active"}
    )

    def __init__(self) -> None:
        self._records: Dict[str, Connection] = {}

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get(self, connection_id: str) -> Optional[Connection]:
        existing = self._records.get(connection_id)
        return replace(existing) if existing is not None else None

    def put(self, connection: Connection) -> Connection:
        now = self._now_iso()
        if not connection.created_at:
            connection.created_at = now
        connection.updated_at = now
        # Persist the DynamoDB item shape (Connection.to_item), then keep the record.
        connection.to_item()
        self._records[connection.connection_id] = replace(connection)
        return connection

    def update(self, connection_id: str, fields: Dict[str, Any]) -> Connection:
        existing = self._records.get(connection_id)
        if existing is None:
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")
        for key, value in fields.items():
            if key in self._ALLOWED_UPDATE_FIELDS:
                setattr(existing, key, value)
        existing.updated_at = self._now_iso()
        existing.to_item()
        self._records[connection_id] = replace(existing)
        return replace(existing)

    def delete(self, connection_id: str) -> None:
        if connection_id not in self._records:
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")
        del self._records[connection_id]

    def list(self, domain: Optional[str] = None) -> List[Connection]:
        records = self._records.values()
        if domain is not None:
            records = [r for r in records if r.domain == domain]
        return [replace(r) for r in records]

    def persisted_items(self) -> List[Dict[str, Any]]:
        """Return the DynamoDB-shaped item dict for every persisted record (what would be stored)."""
        return [r.to_item() for r in self._records.values()]


class FakeSecretsManagerClient:
    """A fake ``secretsmanager`` client: records the put secret and returns an ARN reference.

    Only the two operations the handler's ``_write_secret`` uses are modeled: ``create_secret``
    (and, for rotation, ``put_secret_value``). The returned ``ARN`` is what the handler persists as
    ``secret_ref`` — never the secret value itself.
    """

    class _Exceptions:
        class ResourceExistsException(Exception):
            pass

    def __init__(self) -> None:
        self.exceptions = self._Exceptions()
        # name -> stored secret value (kept only inside the fake vault, never returned to the API).
        self.vault: Dict[str, str] = {}

    def create_secret(self, Name: str, SecretString: str) -> Dict[str, str]:  # noqa: N803 - boto3 kwarg name
        if Name in self.vault:
            raise self.exceptions.ResourceExistsException(Name)
        self.vault[Name] = SecretString
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{Name}-abc123"}

    def put_secret_value(self, SecretId: str, SecretString: str) -> Dict[str, str]:  # noqa: N803
        self.vault[SecretId] = SecretString
        return {"ARN": f"arn:aws:secretsmanager:us-west-2:123456789012:secret:{SecretId}-abc123"}


def _cognito_event(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build an API Gateway event that passes the ``@require_cognito_auth`` gate (no real JWT).

    ``extract_cognito_user_context`` reads ``requestContext.authorizer.claims``; providing a
    minimal claims dict satisfies the Cognito-internal gate without any AWS call.
    """
    event: Dict[str, Any] = {
        "headers": {},
        "requestContext": {"authorizer": {"claims": {"sub": "test-user", "email": "u@example.com"}}},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _iter_field_values(obj: Any):
    """Yield every leaf value in a nested dict/list structure (for containment checks)."""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_field_values(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _iter_field_values(value)
    else:
        yield obj


@settings(max_examples=100)
@given(
    secret=st.text(min_size=1, max_size=40).map(lambda s: _SECRET_PREFIX + s),
    config=st.dictionaries(
        keys=st.sampled_from(["endpoint", "region", "product_code", "web_acl_name"]),
        values=st.text(min_size=0, max_size=24),
        max_size=4,
    ),
    brand=st.one_of(st.none(), st.text(min_size=1, max_size=16)),
    domain=st.sampled_from(["content", "marketplace", "telemetry", "access"]),
    adapter=st.sampled_from(["aws_waf", "cloudwatch", "internal_dynamo", "wordpress"]),
    new_secret=st.text(min_size=1, max_size=40).map(lambda s: _SECRET_PREFIX + "ROT-" + s),
)
def test_secret_never_persisted_or_returned(
    secret: str,
    config: Dict[str, str],
    brand: Optional[str],
    domain: str,
    adapter: str,
    new_secret: str,
) -> None:
    """Feature: integration-adapters, Property 4: A secret value never appears in a persisted
    record or any API response. Validates: Requirements 6.4, 7.2.

    Drives POST /connections (activate with a secret), PUT /connections/{id} (rotate the secret),
    and GET /connections (list) against in-memory fakes, then asserts the generated secret value
    never appears in any persisted store item nor in any serialized API response body.
    """
    store = FakeConnectionsStore()
    fake_sm = FakeSecretsManagerClient()

    # Use an explicit MonkeyPatch context (NOT the function-scoped fixture) so the handler seams
    # are patched and reliably undone for EACH Hypothesis-generated example, with fresh fakes.
    with pytest.MonkeyPatch.context() as mp:
        # Inject fakes into the handler's module-level seams so no AWS is touched.
        mp.setattr(connections_route, "_store", lambda: store)
        mp.setattr(connections_route, "_secretsmanager_client", lambda: fake_sm)

        responses: List[Dict[str, Any]] = []

        # ── POST /connections — activate a Connection carrying the write-only secret. ──
        create_event = _cognito_event(
            {
                "domain": domain,
                "adapter": adapter,
                "brand": brand,
                "config": config,
                "secret": secret,
            }
        )
        create_resp = connections_route.route(create_event, "POST", "/connections")
        responses.append(create_resp)
        assert create_resp["statusCode"] == 201
        created = json.loads(create_resp["body"])["connection"]
        connection_id = created["connection_id"]

        # ── PUT /connections/{id} — rotate the secret (a second secret value must also not leak). ──
        update_event = _cognito_event({"secret": new_secret})
        update_resp = connections_route.route(update_event, "PUT", f"/connections/{connection_id}")
        responses.append(update_resp)
        assert update_resp["statusCode"] == 200

        # ── GET /connections — list active Connections (response serializes the full record). ──
        list_resp = connections_route.route(_cognito_event(), "GET", "/connections")
        responses.append(list_resp)
        assert list_resp["statusCode"] == 200

    # (a) No persisted store item has any field equal to either secret value — only secret_ref.
    for item in store.persisted_items():
        assert "secret" not in item  # Connection has no secret-value field at all
        for value in _iter_field_values(item):
            assert value != secret, f"persisted item leaked the activation secret: {item}"
            assert value != new_secret, f"persisted item leaked the rotated secret: {item}"
        # The reference that IS held is the Secrets Manager ARN, not a plaintext value.
        assert item.get("secret_ref", "").startswith("arn:aws:secretsmanager:")

    # (b) No serialized API response body contains either secret value.
    for resp in responses:
        body = resp["body"]  # already a JSON string (create_success_response(json.dumps(...)))
        assert secret not in body, "an API response body leaked the activation secret"
        assert new_secret not in body, "an API response body leaked the rotated secret"

    # Sanity: the secret really did reach the (fake) Secrets Manager vault — the test is meaningful.
    assert secret in fake_sm.vault.values() or new_secret in fake_sm.vault.values()
