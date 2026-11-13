"""Connection config persistence store (integration-adapters, GitLab #35, task 10.1).

This is the ``services/*`` home for **all** ``cm-connections`` DynamoDB access — the single
source of truth for the connection-management capability this spec owns (design.md
"Connection config (persisted by this spec — ``cm-connections``)"). It provides:

* :meth:`ConnectionsStore.list_active` — the read seam the Registry
  (``integrations/registry.py``) depends on. Its signature matches the registry's structural
  ``ConnectionStore`` Protocol exactly (``list_active(domain, brand=None) -> list[Connection]``),
  and the :class:`~shared.utils.models.Connection` records it returns expose the attributes the
  registry's ``ConnectionRecord`` Protocol reads (``domain``, ``adapter``, ``brand``, ``config``,
  ``secret_ref``, ``active``) — so this store plugs into the Registry with no rewrite
  (dependency inversion, Requirement 6.6).
* the CRUD the Connections handler (task 9.1) needs: :meth:`get`, :meth:`put`, :meth:`update`,
  :meth:`delete`, :meth:`list`.

Table name resolution follows repo convention (``license_service``, ``analytics_collector``,
``pricing_manager``): the CDK wires ``CONNECTIONS_TABLE_NAME`` and the store reads it from the
environment, defaulting to ``cm-connections`` and referencing the table **by name** (no
cross-stack CloudFormation export — ``project-standards`` / architecture steering).

Secret-confidentiality invariant (Requirements 6.4, 7.2 — enforced by construction)
-----------------------------------------------------------------------------------
The persisted record is a :class:`~shared.utils.models.Connection`, which has **no field for a
secret value** — only ``secret_ref`` (a Secrets Manager ARN/name). Writes go through
:meth:`Connection.to_item`, so a plaintext credential cannot be persisted, and reads reconstruct
a ``Connection`` (again, no secret-value field), so a read can never return a secret value. The
store never touches Secrets Manager; resolving ``secret_ref`` to a value is the Registry's job
(via its injected ``SecretResolver``), keeping the secret off both the record and this store.

Absolute imports rooted at the Canonical_Import_Root ``source/lambda/`` (no ``sys.path``
manipulation), matching the other ``services/*`` modules.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr
from shared.utils.models import Connection

logger = logging.getLogger(__name__)

DEFAULT_CONNECTIONS_TABLE_NAME = "cm-connections"

# Only these attributes are accepted onto a persisted Connection record. A secret VALUE is not
# among them (there is no such field on Connection) — the invariant is enforced by construction.
_ALLOWED_UPDATE_FIELDS = frozenset(
    {"domain", "adapter", "brand", "config", "secret_ref", "active"}
)


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string (``created_at`` / ``updated_at``)."""
    return datetime.now(timezone.utc).isoformat()


def _record_from_item(item: Dict[str, Any]) -> Connection:
    """Reconstruct a :class:`Connection` from a raw DynamoDB item.

    Only the known Connection fields are read; any stray attributes are ignored. There is no
    secret-value field on :class:`Connection`, so a reconstructed record can never carry a
    plaintext secret (Requirements 6.4, 7.2).
    """
    return Connection(
        connection_id=item["connection_id"],
        domain=item.get("domain", ""),
        adapter=item.get("adapter", ""),
        brand=item.get("brand"),  # None for single-brand deployments (Req 6.3a)
        config=dict(item.get("config", {})),
        secret_ref=item.get("secret_ref", ""),
        active=bool(item.get("active", False)),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
    )


class ConnectionNotFoundError(Exception):
    """Raised by :meth:`ConnectionsStore.update` / :meth:`delete` when no record matches the id.

    The thin Connections handler (task 9.1) maps this to a 404 response.
    """


class ConnectionsStore:
    """Single home for ``cm-connections`` DynamoDB read/write access.

    Satisfies the registry's ``ConnectionStore`` Protocol (via :meth:`list_active`) and provides
    the CRUD the Connections handler needs. The table name is read from
    ``CONNECTIONS_TABLE_NAME`` (CDK-wired), defaulting to ``cm-connections`` and referenced by
    name (no cross-stack export).
    """

    def __init__(self, table_name: Optional[str] = None, region_name: Optional[str] = None) -> None:
        """Initialize the store.

        :param table_name: override the table name; defaults to ``CONNECTIONS_TABLE_NAME`` env
            var, then ``cm-connections``. Referenced by name (``from_table_name`` semantics), never
            a cross-stack export.
        :param region_name: optional explicit region; defaults to the ambient boto3 region.
        """
        self._table_name = table_name or os.environ.get(
            "CONNECTIONS_TABLE_NAME", DEFAULT_CONNECTIONS_TABLE_NAME
        )
        if region_name:
            self._dynamodb = boto3.resource("dynamodb", region_name=region_name)
        else:
            self._dynamodb = boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(self._table_name)

    # ── Registry read seam (ConnectionStore Protocol) ──

    def list_active(self, domain: str, brand: Optional[str] = None) -> List[Connection]:
        """Return the active connection records for ``(domain, brand)`` (registry read seam).

        Returns only records with ``active`` true, matching ``domain`` and — per-brand resolution
        (Requirement 6.3a) — the given ``brand`` (where ``brand is None`` selects the single-brand
        / brand-agnostic connection). Signature matches the registry's ``ConnectionStore``
        Protocol so the Registry plugs in unchanged.
        """
        filter_expr = Attr("active").eq(True) & Attr("domain").eq(domain)
        if brand is None:
            # Match records with no brand attribute or an explicit null brand (single-brand).
            filter_expr = filter_expr & (
                Attr("brand").not_exists() | Attr("brand").eq(None)
            )
        else:
            filter_expr = filter_expr & Attr("brand").eq(brand)

        items = self._scan(filter_expr)
        return [_record_from_item(item) for item in items]

    # ── CRUD used by the Connections handler (task 9.1) ──

    def get(self, connection_id: str) -> Optional[Connection]:
        """Return the Connection for ``connection_id``, or ``None`` if it does not exist.

        Never returns a secret value — the reconstructed :class:`Connection` has only
        ``secret_ref`` (Requirement 7.2).
        """
        response = self._table.get_item(Key={"connection_id": connection_id})
        item = response.get("Item")
        return _record_from_item(item) if item else None

    def put(self, connection: Connection) -> Connection:
        """Persist a new (or fully-replacing) Connection record and return it.

        Stamps ``created_at`` / ``updated_at`` (ISO 8601) when absent. Persists via
        :meth:`Connection.to_item` (floats → ``Decimal``, ``None`` dropped) — because
        :class:`Connection` has no secret-value field, no plaintext credential can be written
        (Requirements 6.4, 7.2).
        """
        now = _now_iso()
        if not connection.created_at:
            connection.created_at = now
        connection.updated_at = now
        self._table.put_item(Item=connection.to_item())
        return connection

    def update(self, connection_id: str, fields: Dict[str, Any]) -> Connection:
        """Apply a partial update to an existing Connection and return the updated record.

        Only non-secret-value fields in :data:`_ALLOWED_UPDATE_FIELDS` are applied (``domain``,
        ``adapter``, ``brand``, ``config``, ``secret_ref``, ``active``). A secret VALUE cannot be
        set — there is no such field. ``updated_at`` is refreshed. Raises
        :class:`ConnectionNotFoundError` when the record does not exist.
        """
        existing = self.get(connection_id)
        if existing is None:
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")

        for key, value in fields.items():
            if key in _ALLOWED_UPDATE_FIELDS:
                setattr(existing, key, value)
            else:
                logger.debug("Ignoring non-updatable field on connection update: %s", key)

        existing.updated_at = _now_iso()
        self._table.put_item(Item=existing.to_item())
        return existing

    def delete(self, connection_id: str) -> None:
        """Delete the Connection for ``connection_id``.

        Raises :class:`ConnectionNotFoundError` when the record does not exist so the handler can
        return a 404 rather than a silent success.
        """
        response = self._table.delete_item(
            Key={"connection_id": connection_id},
            ReturnValues="ALL_OLD",
        )
        if not response.get("Attributes"):
            raise ConnectionNotFoundError(f"Connection {connection_id} not found")

    def list(self, domain: Optional[str] = None) -> List[Connection]:
        """Return all Connection records, optionally filtered by ``domain``.

        Reads never return secret values — each reconstructed :class:`Connection` carries only
        ``secret_ref`` (Requirement 7.2).
        """
        filter_expr = Attr("domain").eq(domain) if domain is not None else None
        items = self._scan(filter_expr)
        return [_record_from_item(item) for item in items]

    # ── internals ──

    def _scan(self, filter_expr: Any = None) -> List[Dict[str, Any]]:
        """Paginated scan of the connections table, returning all matching raw items."""
        items: List[Dict[str, Any]] = []
        scan_kwargs: Dict[str, Any] = {}
        if filter_expr is not None:
            scan_kwargs["FilterExpression"] = filter_expr

        while True:
            response = self._table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        return items
