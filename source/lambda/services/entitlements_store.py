"""Entitlement persistence store (license-lifecycle-usage-events, GitLab #3, tasks 8.1 / 8.2).

This is the ``services/*`` home for all Entitlement DynamoDB access. An Entitlement is the
server-side "this partner holds that license" authorization link that backs the CoMP license
check — internal, **below the wire**: it is not a CoMP wire object and is deliberately NOT added
to ``openapi-spec.yaml`` (Requirement 7.3).

Key design — co-located partner child, not a join table (Requirement 7.1, design.md A5)
---------------------------------------------------------------------------------------
Entitlements are child items of the partner in the shared core table (``cm-core``), sharing the
partner's partition::

    PK = PARTNER#<partner_id>   SK = META                      -> partner account record
    PK = PARTNER#<partner_id>   SK = KEY#<hash>                -> credential child (rotation)
    PK = PARTNER#<partner_id>   SK = ENTITLEMENT#<license_id>  -> entitlement child  <- here

                one query(PK = PARTNER#<partner_id>) returns all of it

A standalone entitlements join table would force an in-Lambda partner -> entitlement -> license
join on the request path — the "relational model dressed as NoSQL" anti-pattern the #33 entity
model exists to remove. Co-locating makes the authorization read a single item-collection
``query`` (:meth:`EntitlementsStore.query_partner_collection`, Requirement 7.2).

``allowed_uses`` is denormalized **onto the entitlement** so the gate decision needs only the
partner item collection — ``cm-licenses`` is NOT read on the hot path. License stays a separate
versioned table (Requirement 7.4) because it is a reusable terms document referenced by many
entitlements; this store never reads it.

Reuse of the #35 type (Requirement 8.9)
---------------------------------------
The persisted record is the existing :class:`~shared.utils.models.Entitlement` dataclass, landed
by #35 as type-only with the note "Record population is owned by #3/#15". It is reused **as-is**
— not redefined, not modified. Because #35 deliberately gave these types no ``to_item()``
("persistence is added by the vertical that owns it"), the serialization seam lives here:
:func:`entitlement_to_item` / :func:`entitlement_from_item`.

Table name resolution follows repo convention (``connections_store``, ``license_service``,
``partners_service``): the CDK wires ``CORE_TABLE_NAME`` and the store reads it from the
environment, defaulting to ``cm-core`` and referencing the table **by name** (no cross-stack
CloudFormation export).

Absolute imports rooted at the Canonical_Import_Root ``source/lambda/`` (no ``sys.path``
manipulation), matching the other ``services/*`` modules.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from shared.utils.models import Entitlement

logger = logging.getLogger(__name__)

DEFAULT_CORE_TABLE_NAME = "cm-core"

#: Key prefixes for the ``cm-core`` overloaded-key convention (#33).
PARTNER_PK_PREFIX = "PARTNER#"
ENTITLEMENT_SK_PREFIX = "ENTITLEMENT#"
PARTNER_META_SK = "META"


def partner_pk(partner_id: str) -> str:
    """Return the item-collection partition key for a partner: ``PARTNER#<partner_id>``."""
    return f"{PARTNER_PK_PREFIX}{partner_id}"


def entitlement_sk(license_id: str) -> str:
    """Return the entitlement child sort key: ``ENTITLEMENT#<license_id>``."""
    return f"{ENTITLEMENT_SK_PREFIX}{license_id}"


def entitlement_keys(partner_id: str, license_id: str) -> Dict[str, str]:
    """Return the full primary key of one entitlement child item."""
    return {"PK": partner_pk(partner_id), "SK": entitlement_sk(license_id)}


def entitlement_to_item(entitlement: Entitlement) -> Dict[str, Any]:
    """Serialize an :class:`Entitlement` to a ``cm-core`` child item.

    The #35 dataclass carries no ``to_item()`` by design (persistence belongs to the owning
    vertical), so this is that seam. The overloaded keys are derived from the record's own
    ``partner_id`` / ``license_id`` — they are never caller-supplied, so a record cannot be
    written under a key that disagrees with its contents.

    ``None`` values are dropped rather than persisted as DynamoDB NULLs, matching the shared
    ``_to_dynamo`` convention in ``shared/utils/models.py``. ``allowed_uses`` is stored as a list
    of DynamoDB numbers and is denormalized here on purpose (Requirement 7.2).
    """
    item: Dict[str, Any] = {
        **entitlement_keys(entitlement.partner_id, entitlement.license_id),
        "entity_type": "ENTITLEMENT",
        "partner_id": entitlement.partner_id,
        "license_id": entitlement.license_id,
        # Denormalized onto the entitlement so the gate never reads cm-licenses (Req 7.2).
        "allowed_uses": [int(code) for code in entitlement.allowed_uses],
        "active": bool(entitlement.active),
    }

    # Denormalized AWS Marketplace keys, present only when marketplace-sourced.
    for field_name in ("product_code", "customer_identifier", "license_arn"):
        value = getattr(entitlement, field_name)
        if value is not None:
            item[field_name] = value

    return item


def entitlement_from_item(item: Dict[str, Any]) -> Entitlement:
    """Reconstruct an :class:`Entitlement` from a raw ``cm-core`` item.

    Only the known Entitlement fields are read; stray attributes are ignored. ``allowed_uses``
    comes back from DynamoDB as :class:`~decimal.Decimal` values and is normalized to ``int`` so
    callers compare CoMP function codes against plain ints.
    """
    return Entitlement(
        partner_id=item.get("partner_id", ""),
        license_id=item.get("license_id", ""),
        allowed_uses=[int(code) for code in item.get("allowed_uses", [])],
        active=bool(item.get("active", False)),
        product_code=item.get("product_code"),
        customer_identifier=item.get("customer_identifier"),
        license_arn=item.get("license_arn"),
    )


class EntitlementNotFoundError(Exception):
    """Raised by :meth:`EntitlementsStore.delete` when no entitlement matches the key."""


class EntitlementsStore:
    """Single home for Entitlement read/write access in the shared core table.

    The table name is read from ``CORE_TABLE_NAME`` (CDK-wired), defaulting to ``cm-core`` and
    referenced by name (no cross-stack export). The table resource is created lazily on first use
    so importing this module performs no AWS work.
    """

    def __init__(self, table_name: Optional[str] = None, region_name: Optional[str] = None) -> None:
        """Initialize the store.

        :param table_name: override the table name; defaults to the ``CORE_TABLE_NAME`` env var,
            then ``cm-core``.
        :param region_name: optional explicit region; defaults to the ambient boto3 region.
        """
        self._table_name = table_name or os.environ.get("CORE_TABLE_NAME", DEFAULT_CORE_TABLE_NAME)
        self._region_name = region_name
        self._table: Any = None

    @property
    def table_name(self) -> str:
        """The resolved ``cm-core`` table name this store reads and writes."""
        return self._table_name

    def _get_table(self) -> Any:
        """Return the DynamoDB table resource, created lazily and cached on the instance."""
        if self._table is None:
            if self._region_name:
                dynamodb = boto3.resource("dynamodb", region_name=self._region_name)
            else:
                dynamodb = boto3.resource("dynamodb")
            self._table = dynamodb.Table(self._table_name)
        return self._table

    # ── writes (task 8.1) ──

    def put(self, entitlement: Entitlement) -> Entitlement:
        """Persist an entitlement as a partner child item and return it.

        Writes to ``PK = PARTNER#<partner_id>`` / ``SK = ENTITLEMENT#<license_id>`` — the same
        partition as the partner record, so the read in
        :meth:`query_partner_collection` stays a single query.
        """
        try:
            self._get_table().put_item(Item=entitlement_to_item(entitlement))
        except Exception:
            logger.exception(
                "Failed to persist entitlement partner_id=%s license_id=%s",
                entitlement.partner_id,
                entitlement.license_id,
            )
            raise
        return entitlement

    def delete(self, partner_id: str, license_id: str) -> None:
        """Delete one entitlement child item.

        Raises :class:`EntitlementNotFoundError` when no such item exists, so a caller can report
        a miss rather than a silent success.
        """
        try:
            response = self._get_table().delete_item(
                Key=entitlement_keys(partner_id, license_id),
                ReturnValues="ALL_OLD",
            )
        except Exception:
            logger.exception(
                "Failed to delete entitlement partner_id=%s license_id=%s", partner_id, license_id
            )
            raise

        if not response.get("Attributes"):
            raise EntitlementNotFoundError(
                f"Entitlement for partner {partner_id} / license {license_id} not found"
            )

    # ── reads (task 8.2) ──

    def get(self, partner_id: str, license_id: str) -> Optional[Entitlement]:
        """Return one entitlement, or ``None`` when the partner does not hold that license.

        A direct ``get_item`` on the child key. Use :meth:`query_partner_collection` (or
        :meth:`list_for_partner`) when more than one entitlement is needed — that is one query
        rather than N point reads.
        """
        try:
            response = self._get_table().get_item(Key=entitlement_keys(partner_id, license_id))
        except Exception:
            logger.exception(
                "Failed to read entitlement partner_id=%s license_id=%s", partner_id, license_id
            )
            raise

        item = response.get("Item")
        return entitlement_from_item(item) if item else None

    def query_partner_collection(
        self, partner_id: str
    ) -> Tuple[Optional[Dict[str, Any]], List[Entitlement]]:
        """Return ``(partner_item, entitlements)`` for a partner in a **single** ``query``.

        This is the Requirement 7.2 read: one item-collection ``query`` on
        ``PK = PARTNER#<partner_id>`` returns the partner ``META`` item and its ``ENTITLEMENT#``
        children together, so resolving a partner's entitlements needs no in-Lambda
        partner -> entitlement -> license join on the request path.

        Because ``allowed_uses`` is denormalized onto each entitlement, the caller has everything
        the authorization decision needs from this one read — **no ``cm-licenses`` access happens
        on the hot path** (Requirement 7.4: the license terms document stays in its own versioned
        table, read only on cold/authoring paths).

        Returns the raw partner item (``None`` when the partner has no ``META`` record yet) and
        the reconstructed entitlement children. Items of other subtypes in the collection (e.g.
        ``KEY#<hash>`` credential children) are ignored here — they belong to the partner
        vertical.
        """
        items = self._query_collection(partner_id)

        partner_item: Optional[Dict[str, Any]] = None
        entitlements: List[Entitlement] = []
        for item in items:
            sort_key = str(item.get("SK", ""))
            if sort_key == PARTNER_META_SK:
                partner_item = item
            elif sort_key.startswith(ENTITLEMENT_SK_PREFIX):
                entitlements.append(entitlement_from_item(item))

        return partner_item, entitlements

    def list_for_partner(self, partner_id: str, active_only: bool = False) -> List[Entitlement]:
        """Return a partner's entitlements, optionally only the active ones.

        A narrowed single ``query`` — the ``ENTITLEMENT#`` sort-key prefix is pushed into the key
        condition, so DynamoDB returns only the entitlement children and the partner record is not
        read. Use :meth:`query_partner_collection` when the partner item is needed too (still one
        query).
        """
        try:
            items = self._query(
                Key("PK").eq(partner_pk(partner_id))
                & Key("SK").begins_with(ENTITLEMENT_SK_PREFIX)
            )
        except Exception:
            logger.exception("Failed to query entitlements for partner_id=%s", partner_id)
            raise

        entitlements = [entitlement_from_item(item) for item in items]
        if active_only:
            entitlements = [e for e in entitlements if e.active]
        return entitlements

    # ── internals ──

    def _query_collection(self, partner_id: str) -> List[Dict[str, Any]]:
        """Run the single item-collection query on ``PK = PARTNER#<partner_id>``."""
        try:
            return self._query(Key("PK").eq(partner_pk(partner_id)))
        except Exception:
            logger.exception("Failed to query partner item collection partner_id=%s", partner_id)
            raise

    def _query(self, key_condition: Any) -> List[Dict[str, Any]]:
        """Paginated ``query`` returning all matching raw items.

        Pagination is a continuation of the *same* query (``ExclusiveStartKey``), not a second
        access pattern — the read stays one item-collection query per the design.
        """
        table = self._get_table()
        items: List[Dict[str, Any]] = []
        query_kwargs: Dict[str, Any] = {"KeyConditionExpression": key_condition}

        while True:
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key

        return items


__all__ = [
    "DEFAULT_CORE_TABLE_NAME",
    "ENTITLEMENT_SK_PREFIX",
    "PARTNER_META_SK",
    "PARTNER_PK_PREFIX",
    "EntitlementNotFoundError",
    "EntitlementsStore",
    "entitlement_from_item",
    "entitlement_keys",
    "entitlement_sk",
    "entitlement_to_item",
    "partner_pk",
]
