"""Partners domain service: the single home for partner data access and key provisioning.

``cm-partners`` is keyed on the partner (``PARTNER#<partner_id>`` / ``META``) with one
``KEY#<api_key_id>`` item per API Gateway key bound to it. API Gateway is the system of record
for the key *value*; this service only ever stores the key id and the usage plan it is attached
to. Partner resolution at request time lives in ``shared.auth.authentication_functions``; this
module owns the control-plane side: reading, updating and provisioning.

Public surface (spec: native-apikey-auth-migration, Req 5):

- :meth:`PartnersService.get_partners` — reader for ``GET /partners`` and the
  ``/analytics/partners`` alias.
- :meth:`PartnersService.get_partner` — one partner with its credential items.
- :meth:`PartnersService.update_partner` — metadata writer for ``PUT /partners/{id}``.
- :meth:`PartnersService.provision_key` / :meth:`disable_keys` / :meth:`delete_keys` — the
  provisioning primitives shared by the lifecycle API and the seed script.
- :func:`validate_partner_fields` — the single validation rule for tier, status and
  ``allowed_endpoints``.

Domain errors are typed (:class:`PartnerNotFoundError`, :class:`NoPartnerFieldsError`,
:class:`PartnerValidationError`) so the thin route maps them to 404 / 400 responses.
"""

import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS
from shared.utils.models import (
    PARTNER_IDENTITY_SOURCES,
    PARTNER_KEY_SK_PREFIX,
    PARTNER_META_SK,
    PARTNER_PRICING_TIERS,
    PARTNER_STATUSES,
    Partner,
    PartnerCredential,
    partner_key_sk,
    partner_pk,
)

logger = logging.getLogger(__name__)

# Fields a caller may set through PUT /partners/{id}. Quota is a tier property (Q4), so there is
# no per-partner quota field; status transitions a caller may request are active/suspended (Q9).
UPDATABLE_FIELDS = ("partner_name", "partner_type", "contact_name", "contact_email", "pricing_tier", "allowed_endpoints", "status")
UPDATABLE_STATUSES = ("active", "suspended")


class PartnerNotFoundError(Exception):
    """No partner matches the id (route -> 404)."""


class NoPartnerFieldsError(Exception):
    """The update carries no updatable field (route -> 400)."""


class PartnerValidationError(Exception):
    """A supplied field has an invalid value (route -> 400)."""


class PartnerConflictError(Exception):
    """The operation is not allowed in the partner's current state (route -> 409)."""


CREATE_FIELDS = UPDATABLE_FIELDS[:-1] + ("identity_source", "marketplace_type", "marketplace_customer_id")
_PARTNER_ID_ALLOWED = re.compile(r"[^a-z0-9]+")


def _new_partner_id(partner_name: str) -> str:
    """``ptr_<slug>_<6 hex>``: readable in the console and in API Gateway key tags, unique enough."""
    slug = _PARTNER_ID_ALLOWED.sub("_", partner_name.lower()).strip("_")[:24] or "partner"
    return f"ptr_{slug}_{uuid.uuid4().hex[:6]}"


def _quota_window(period: str, now: Optional[datetime] = None) -> tuple:
    """(period_start, period_end) ISO dates of the current quota period (``DAY`` / ``WEEK`` / ``MONTH``).

    API Gateway resets a usage plan's quota on UTC calendar boundaries; usage is reported for the
    same window so ``used`` + ``remaining`` add up to the plan's limit.
    """
    now = (now or datetime.now(timezone.utc)).date()
    if period == "DAY":
        return now.isoformat(), now.isoformat()
    if period == "WEEK":
        start = now - timedelta(days=now.weekday())
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    start = now.replace(day=1)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.isoformat(), (end - timedelta(days=1)).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


USAGE_PLAN_NAME_PREFIX = os.environ.get("USAGE_PLAN_NAME_PREFIX", "cm-partner-")
_usage_plan_cache: Dict[str, str] = {}


def usage_plan_id_for(tier: str, apigateway_client=None) -> str:
    """Resolve the API Gateway usage plan backing a pricing tier (design D2).

    ``USAGE_PLAN_ID_<TIER>`` wins when set (tests, local overrides). Otherwise the plan named
    ``<USAGE_PLAN_NAME_PREFIX><tier>`` is looked up once per container: the stack cannot export
    plan ids into the Lambda environment because plan -> stage -> API -> Lambda would be a
    CloudFormation cycle, so the name is the contract between CDK and the service.
    """
    if tier not in PARTNER_PRICING_TIERS:
        raise PartnerValidationError(f"Unknown pricing tier '{tier}'")
    override = os.environ.get(f"USAGE_PLAN_ID_{tier.upper()}")
    if override:
        return override
    if tier in _usage_plan_cache:
        return _usage_plan_cache[tier]
    client = apigateway_client or boto3.client("apigateway")
    wanted = f"{USAGE_PLAN_NAME_PREFIX}{tier}"
    for page in client.get_paginator("get_usage_plans").paginate():
        for plan in page.get("items", []):
            if plan.get("name") == wanted:
                _usage_plan_cache[tier] = plan["id"]
                return plan["id"]
    raise PartnerValidationError(f"No usage plan configured for pricing tier '{tier}'")


def validate_partner_fields(fields: Dict[str, Any], *, statuses: Iterable[str] = UPDATABLE_STATUSES) -> None:
    if "identity_source" in fields and fields["identity_source"] not in PARTNER_IDENTITY_SOURCES:
        raise PartnerValidationError(
            f"identity_source must be one of {', '.join(PARTNER_IDENTITY_SOURCES)}; got '{fields['identity_source']}'"
        )
    if "partner_name" in fields and not (isinstance(fields["partner_name"], str) and 0 < len(fields["partner_name"].strip()) <= 200):
        raise PartnerValidationError("partner_name must be a non-empty string of at most 200 characters")
    """Validate the writable partner fields present in ``fields``; raise on the first problem."""
    if "pricing_tier" in fields and fields["pricing_tier"] not in PARTNER_PRICING_TIERS:
        raise PartnerValidationError(
            f"pricing_tier must be one of {', '.join(PARTNER_PRICING_TIERS)}; got '{fields['pricing_tier']}'"
        )
    if "status" in fields and fields["status"] not in statuses:
        raise PartnerValidationError(f"status must be one of {', '.join(statuses)}; got '{fields['status']}'")
    if "allowed_endpoints" in fields:
        endpoints = fields["allowed_endpoints"]
        if not isinstance(endpoints, list) or not all(isinstance(e, str) for e in endpoints):
            raise PartnerValidationError("allowed_endpoints must be a list of operation ids")
        unknown = sorted(set(endpoints) - KEY_GATED_OPERATION_IDS)
        if unknown:
            raise PartnerValidationError(
                f"allowed_endpoints contains unknown or public operations {unknown}; "
                f"allowed values are {sorted(KEY_GATED_OPERATION_IDS)}"
            )
    if "partner_name" in fields and not (isinstance(fields["partner_name"], str) and fields["partner_name"].strip()):
        raise PartnerValidationError("partner_name must be a non-empty string")


class PartnersService:
    """Single home for partner DynamoDB access and API Gateway key provisioning."""

    def __init__(self, apigateway_client=None):
        self._apigw = apigateway_client

    # ── infrastructure accessors ─────────────────────────────────────────────────────────

    def _partners_table(self):
        """Return the partner DynamoDB table resource (``PARTNER_TABLE_NAME``)."""
        dynamodb = boto3.resource("dynamodb")
        return dynamodb.Table(os.environ.get("PARTNER_TABLE_NAME", "cm-partners"))

    def _apigateway(self):
        if self._apigw is None:
            self._apigw = boto3.client("apigateway")
        return self._apigw

    # ── reads ────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_partner(meta: Dict[str, Any], active_key_count: int = 0) -> Dict[str, Any]:
        """Shape a ``META`` item as the API ``Partner`` schema."""
        allowed = meta.get("allowed_endpoints") or []
        if isinstance(allowed, str):
            allowed = [allowed]
        return {
            "partner_id": meta.get("partner_id"),
            "partner_name": meta.get("partner_name", "Unknown Partner"),
            "partner_type": meta.get("partner_type"),
            "contact_name": meta.get("contact_name"),
            "contact_email": meta.get("contact_email"),
            "pricing_tier": meta.get("pricing_tier", "basic"),
            "status": meta.get("status", "active"),
            "allowed_endpoints": list(allowed),
            "identity_source": meta.get("identity_source", "direct"),
            "marketplace_type": meta.get("marketplace_type"),
            "marketplace_customer_id": meta.get("marketplace_customer_id"),
            "active_key_count": active_key_count,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
        }

    @staticmethod
    def _to_key(item: Dict[str, Any]) -> Dict[str, Any]:
        """Shape a ``KEY#`` item as the API ``PartnerKey`` schema (never a key value)."""
        return {
            "key_id": item.get("api_key_id"),
            "status": item.get("status", "active"),
            "usage_plan_id": item.get("usage_plan_id"),
            "created_at": item.get("created_at"),
            "disabled_at": item.get("disabled_at"),
        }

    def _scan_all(self, table, **kwargs) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
            items.extend(response.get("Items", []))
        return items

    def get_partners(self, publisher_id: str) -> Dict[str, Any]:
        """Return the ``PartnersListResponse`` payload for ``GET /partners``.

        One scan over the (small) table; ``META`` items become partners and ``KEY#`` items are
        counted into ``active_key_count``. A read failure propagates so the route answers 500:
        there is no silent demo fallback.
        """
        table = self._partners_table()
        items = self._scan_all(table)

        active_keys: Dict[str, int] = {}
        metas: List[Dict[str, Any]] = []
        for item in items:
            sk = item.get("SK", "")
            if sk == PARTNER_META_SK:
                metas.append(item)
            elif sk.startswith(PARTNER_KEY_SK_PREFIX) and item.get("status") == "active":
                active_keys[item.get("partner_id")] = active_keys.get(item.get("partner_id"), 0) + 1

        partners = [self._to_partner(meta, active_keys.get(meta.get("partner_id"), 0)) for meta in metas]
        partners.sort(key=lambda p: (p.get("partner_name") or "", p.get("partner_id") or ""))

        return {
            "total_partners": len(partners),
            "active_partners": sum(1 for p in partners if p["status"] == "active"),
            "partners": partners,
            "publisher_id": publisher_id,
            "timestamp": _now(),
        }

    def _get_meta(self, table, partner_id: str) -> Dict[str, Any]:
        item = table.get_item(Key={"PK": partner_pk(partner_id), "SK": PARTNER_META_SK}).get("Item")
        if not item:
            raise PartnerNotFoundError(partner_id)
        return item

    def _get_credentials(self, table, partner_id: str) -> List[Dict[str, Any]]:
        response = table.query(
            KeyConditionExpression=Key("PK").eq(partner_pk(partner_id)) & Key("SK").begins_with(PARTNER_KEY_SK_PREFIX)
        )
        return response.get("Items", [])

    def get_partner(self, partner_id: str) -> Dict[str, Any]:
        """Return the ``PartnerDetail`` payload: partner, credentials, tier limits and current usage.

        Limits come from the tier's usage plan (``GetUsagePlan``) and consumption from
        ``GetUsage`` over the plan's current quota period, per active key (Q4). Both are best-effort:
        a gateway read failure leaves ``tier_limits`` / ``usage`` null rather than failing the read.
        """
        table = self._partners_table()
        meta = self._get_meta(table, partner_id)
        credentials = self._get_credentials(table, partner_id)
        detail = self._to_partner(meta, sum(1 for c in credentials if c.get("status") == "active"))
        detail["keys"] = [self._to_key(c) for c in sorted(credentials, key=lambda c: c.get("created_at") or "")]
        detail["tier_limits"] = self._tier_limits(meta.get("pricing_tier", "basic"))
        if detail["tier_limits"] is not None:
            for key in detail["keys"]:
                key["usage"] = self._key_usage(key, detail["tier_limits"]) if key["status"] == "active" else None
        return detail

    def _tier_limits(self, pricing_tier: str) -> Optional[Dict[str, Any]]:
        try:
            plan = self._apigateway().get_usage_plan(usagePlanId=usage_plan_id_for(pricing_tier, self._apigateway()))
        except Exception as exc:  # noqa: BLE001 - informational enrichment only
            logger.warning(f"Could not read usage plan for tier {pricing_tier}: {exc}")
            return None
        quota, throttle = plan.get("quota") or {}, plan.get("throttle") or {}
        return {
            "quota_limit": int(quota.get("limit", 0)),
            "quota_period": quota.get("period", "MONTH"),
            "rate_limit": float(throttle.get("rateLimit", 0)),
            "burst_limit": int(throttle.get("burstLimit", 0)),
        }

    def _key_usage(self, key: Dict[str, Any], limits: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        quota_limit = int(limits.get("quota_limit", 0))
        start, end = _quota_window(limits.get("quota_period", "MONTH"))
        try:
            usage = self._apigateway().get_usage(usagePlanId=key["usage_plan_id"], keyId=key["key_id"], startDate=start, endDate=end)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not read usage for key {key['key_id']}: {exc}")
            return None
        # items: {keyId: [[used, remaining], ...]} one pair per day within the window.
        days = (usage.get("items") or {}).get(key["key_id"]) or []
        used = sum(int(day[0]) for day in days if day)
        return {"period_start": start, "period_end": end, "used": used, "remaining": max(quota_limit - used, 0)}

    # ── writes ───────────────────────────────────────────────────────────────────────────

    def update_partner(self, partner_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a partner's metadata and return the updated ``Partner`` payload.

        Raises :class:`PartnerNotFoundError`, :class:`NoPartnerFieldsError` or
        :class:`PartnerValidationError` for the route to map. Unknown fields (including the
        retired ``api_quota``) are ignored.
        """
        table = self._partners_table()
        meta = self._get_meta(table, partner_id)
        if meta.get("status") == "deleted":
            raise PartnerNotFoundError(partner_id)

        updates = {k: request_data[k] for k in UPDATABLE_FIELDS if k in request_data}
        if not updates:
            raise NoPartnerFieldsError(partner_id)
        validate_partner_fields(updates)

        # Placeholder prefixes avoid the ``#n0`` / ``:v0`` names boto3 generates for the condition.
        names = {f"#fld{i}": field for i, field in enumerate(updates)}
        values = {f":val{i}": value for i, value in enumerate(updates.values())}
        assignments = [f"#fld{i} = :val{i}" for i in range(len(updates))]
        names["#updated"] = "updated_at"
        values[":updated"] = _now()
        assignments.append("#updated = :updated")

        response = table.update_item(
            Key={"PK": partner_pk(partner_id), "SK": PARTNER_META_SK},
            UpdateExpression="SET " + ", ".join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression=Attr("SK").eq(PARTNER_META_SK),
            ReturnValues="ALL_NEW",
        )
        updated = response["Attributes"]

        # Side effects on the partner's keys (Q9 / Req 5.4): a tier move re-binds every key to the
        # new tier's usage plan; suspending disables every key, reactivating re-enables them.
        if "pricing_tier" in updates and updates["pricing_tier"] != meta.get("pricing_tier"):
            self._rebind_keys(partner_id, updates["pricing_tier"])
        if updates.get("status") == "suspended" and meta.get("status") != "suspended":
            self.disable_keys(partner_id)
        elif updates.get("status") == "active" and meta.get("status") == "suspended":
            self.enable_keys(partner_id)

        active = sum(1 for c in self._get_credentials(table, partner_id) if c.get("status") == "active")
        return self._to_partner(updated, active)

    def _rebind_keys(self, partner_id: str, pricing_tier: str) -> None:
        table = self._partners_table()
        credentials = self._get_credentials(table, partner_id)
        if not credentials:  # nothing to move; the next provisioned key picks up the new tier
            return
        apigw = self._apigateway()
        new_plan = usage_plan_id_for(pricing_tier, apigw)
        for credential in credentials:
            key_id, old_plan = credential.get("api_key_id"), credential.get("usage_plan_id")
            if old_plan == new_plan:
                continue
            try:  # the old binding may already be gone; the new binding is what matters
                apigw.delete_usage_plan_key(usagePlanId=old_plan, keyId=key_id)
            except apigw.exceptions.NotFoundException:
                pass
            apigw.create_usage_plan_key(usagePlanId=new_plan, keyId=key_id, keyType="API_KEY")
            table.update_item(
                Key={"PK": partner_pk(partner_id), "SK": partner_key_sk(key_id)},
                UpdateExpression="SET usage_plan_id = :p",
                ExpressionAttributeValues={":p": new_plan},
            )

    # ── lifecycle (Req 5.1-5.6, D5) ──────────────────────────────────────────────────────

    def create_partner(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a partner and, for a ``direct`` identity, its first API key.

        Returns the ``PartnerKeyCreatedResponse`` shape: ``partner``, ``key`` (``None`` for a
        marketplace partner, which authenticates through the marketplace) and ``key_value`` —
        shown once, never stored. The META item is written first so a key-provisioning failure
        leaves a partner that :meth:`reconcile_keys` or a later :meth:`rotate_key` can complete.
        """
        fields = {k: request_data[k] for k in CREATE_FIELDS if k in request_data}
        missing = [k for k in ("partner_name", "pricing_tier") if not fields.get(k)]
        if missing:
            raise PartnerValidationError(f"missing required field(s): {', '.join(missing)}")
        validate_partner_fields(fields)
        if fields.get("identity_source", "direct") == "marketplace" and not fields.get("marketplace_type"):
            raise PartnerValidationError("marketplace_type is required when identity_source is 'marketplace'")

        now = _now()
        partner = Partner(partner_id=_new_partner_id(fields["partner_name"]), created_at=now, updated_at=now, **fields)
        table = self._partners_table()
        table.put_item(Item=partner.to_item(), ConditionExpression=Attr("PK").not_exists())

        key: Optional[Dict[str, Any]] = None
        key_value: Optional[str] = None
        if partner.identity_source == "direct":
            key = self.provision_key(partner.partner_id, partner.pricing_tier)
            key_value = key.pop("key_value")
        logger.info(f"Created partner {partner.partner_id} ({partner.identity_source}, {partner.pricing_tier})")
        return {"partner": self._to_partner(partner.to_item(), 1 if key else 0), "key": key, "key_value": key_value}

    def delete_partner(self, partner_id: str) -> Dict[str, Any]:
        """Soft-delete the META item and remove every key (gateway, plan binding, ``KEY#``) (Q9)."""
        table = self._partners_table()
        meta = self._get_meta(table, partner_id)
        if meta.get("status") == "deleted":
            raise PartnerNotFoundError(partner_id)
        self.delete_keys(partner_id)
        response = table.update_item(
            Key={"PK": partner_pk(partner_id), "SK": PARTNER_META_SK},
            UpdateExpression="SET #st = :deleted, deleted_at = :at, updated_at = :at",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":deleted": "deleted", ":at": _now()},
            ReturnValues="ALL_NEW",
        )
        logger.info(f"Deleted partner {partner_id}")
        return self._to_partner(response["Attributes"], 0)

    def rotate_key(self, partner_id: str) -> Dict[str, Any]:
        """Provision a new key on the partner's tier and disable the previous active keys (D5).

        The old keys stay disabled (not deleted) so an in-flight caller fails closed and the
        operator can retire them once the new value is in the partner's hands.
        """
        table = self._partners_table()
        meta = self._get_meta(table, partner_id)
        if meta.get("status") == "deleted":
            raise PartnerConflictError(f"partner {partner_id} is deleted")
        if meta.get("identity_source", "direct") != "direct":
            raise PartnerConflictError(f"partner {partner_id} has no direct credentials (marketplace identity)")
        new_key = self.provision_key(partner_id, meta.get("pricing_tier", "basic"))
        key_value = new_key.pop("key_value")
        self.disable_keys(partner_id, except_key_id=new_key["key_id"], reason="rotated")
        # A key already parked by a suspension is rotated out too: reactivation must not revive it.
        for credential in self._get_credentials(table, partner_id):
            if credential.get("api_key_id") != new_key["key_id"] and credential.get("disabled_reason", "suspended") != "rotated":
                table.update_item(
                    Key={"PK": partner_pk(partner_id), "SK": partner_key_sk(credential["api_key_id"])},
                    UpdateExpression="SET disabled_reason = :why",
                    ExpressionAttributeValues={":why": "rotated"},
                )
        if meta.get("status") == "suspended":  # the new key is parked until the partner is reactivated
            self.disable_keys(partner_id, reason="suspended")
            new_key["status"] = "disabled"
        active = sum(1 for c in self._get_credentials(table, partner_id) if c.get("status") == "active")
        return {"partner": self._to_partner(meta, active), "key": new_key, "key_value": key_value}

    def retire_key(self, partner_id: str, key_id: str) -> None:
        """Permanently delete one key. Refused (409) while it is the partner's only active key (D5)."""
        table = self._partners_table()
        meta = self._get_meta(table, partner_id)
        credentials = self._get_credentials(table, partner_id)
        target = next((c for c in credentials if c.get("api_key_id") == key_id), None)
        if target is None:
            raise PartnerNotFoundError(f"{partner_id}/{key_id}")
        active_ids = [c["api_key_id"] for c in credentials if c.get("status") == "active"]
        if active_ids == [key_id] and meta.get("status") != "deleted":
            raise PartnerConflictError(f"key {key_id} is the only active key of partner {partner_id}; rotate first")
        self.delete_keys(partner_id, key_ids=[key_id])

    def reconcile_keys(self, partner_id: str) -> Dict[str, List[str]]:
        """Repair a create/rotate that failed between ``create_api_key`` and the ``KEY#`` write (4.4).

        Keys API Gateway holds for the partner (by tag) but the table does not know are deleted
        from the gateway — their values were never delivered, so they are unusable orphans.
        Returns ``{"orphaned_deleted": [...], "recorded": [...]}``.
        """
        table = self._partners_table()
        apigw = self._apigateway()
        recorded = {c.get("api_key_id") for c in self._get_credentials(table, partner_id)}
        orphans: List[str] = []
        for key in self.list_gateway_keys_for_partner(partner_id):
            if key["id"] in recorded:
                continue
            for plan in apigw.get_usage_plans(keyId=key["id"]).get("items", []):
                try:
                    apigw.delete_usage_plan_key(usagePlanId=plan["id"], keyId=key["id"])
                except apigw.exceptions.NotFoundException:
                    pass
            apigw.delete_api_key(apiKey=key["id"])
            orphans.append(key["id"])
        if orphans:
            logger.warning(f"Reconciled partner {partner_id}: deleted {len(orphans)} orphaned gateway key(s)")
        return {"orphaned_deleted": orphans, "recorded": sorted(recorded)}

    # ── key provisioning primitives (Req 5) ──────────────────────────────────────────────

    def provision_key(self, partner_id: str, pricing_tier: str) -> Dict[str, Any]:
        """Create an API Gateway key for ``partner_id`` on the tier's usage plan and record it.

        Order: create the key, attach it to the plan, then write the ``KEY#`` item. Returns the
        ``PartnerKey`` shape plus ``key_value`` — the only moment the value is available; callers
        must not persist it. The key is tagged with the partner id so a failed run can be
        reconciled by listing keys.
        """
        validate_partner_fields({"pricing_tier": pricing_tier})
        plan_id = usage_plan_id_for(pricing_tier, self._apigateway())
        apigw = self._apigateway()

        created = apigw.create_api_key(
            name=f"{partner_id}-{int(time.time())}",
            description=f"Partner API key for {partner_id}",
            enabled=True,
            tags={"partner_id": partner_id, "pricing_tier": pricing_tier},
        )
        key_id = created["id"]
        apigw.create_usage_plan_key(usagePlanId=plan_id, keyId=key_id, keyType="API_KEY")

        credential = PartnerCredential(partner_id=partner_id, api_key_id=key_id, usage_plan_id=plan_id, created_at=_now())
        self._partners_table().put_item(Item=credential.to_item())
        logger.info(f"Provisioned API key {key_id} for partner {partner_id} on plan {plan_id}")

        result = self._to_key(credential.to_item())
        result["key_value"] = created["value"]
        return result

    def disable_keys(self, partner_id: str, *, except_key_id: Optional[str] = None, reason: str = "suspended") -> List[str]:
        """Disable every active key of ``partner_id`` at API Gateway and in the table.

        Reversible: the key and its plan binding stay. ``reason`` is recorded on the item so
        :meth:`enable_keys` can tell a key parked by a suspension (restored on reactivation)
        from one rotated out (stays disabled until retired). Returns the disabled key ids.
        """
        table = self._partners_table()
        apigw = self._apigateway()
        disabled: List[str] = []
        for credential in self._get_credentials(table, partner_id):
            key_id = credential.get("api_key_id")
            if credential.get("status") != "active" or key_id == except_key_id:
                continue
            apigw.update_api_key(apiKey=key_id, patchOperations=[{"op": "replace", "path": "/enabled", "value": "false"}])
            table.update_item(
                Key={"PK": partner_pk(partner_id), "SK": partner_key_sk(key_id)},
                UpdateExpression="SET #st = :disabled, disabled_at = :at, disabled_reason = :why",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":disabled": "disabled", ":at": _now(), ":why": reason},
            )
            disabled.append(key_id)
        return disabled

    def enable_keys(self, partner_id: str) -> List[str]:
        """Re-enable the keys a suspension disabled (the inverse of :meth:`disable_keys`).

        Keys disabled for any other reason (rotation) are left alone: reactivating a partner
        must not resurrect a credential the operator deliberately replaced.
        """
        table = self._partners_table()
        apigw = self._apigateway()
        enabled: List[str] = []
        for credential in self._get_credentials(table, partner_id):
            key_id = credential.get("api_key_id")
            if credential.get("status") != "disabled" or credential.get("disabled_reason", "suspended") != "suspended":
                continue
            apigw.update_api_key(apiKey=key_id, patchOperations=[{"op": "replace", "path": "/enabled", "value": "true"}])
            table.update_item(
                Key={"PK": partner_pk(partner_id), "SK": partner_key_sk(key_id)},
                UpdateExpression="SET #st = :active REMOVE disabled_at, disabled_reason",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":active": "active"},
            )
            enabled.append(key_id)
        return enabled

    def delete_keys(self, partner_id: str, *, key_ids: Optional[Iterable[str]] = None) -> List[str]:
        """Delete keys of ``partner_id`` (all, or the given ids) from the plan, API Gateway and the table.

        Irreversible. Tolerates a key already gone from API Gateway so a partial earlier run can
        be completed. Returns the deleted key ids.
        """
        table = self._partners_table()
        apigw = self._apigateway()
        wanted = set(key_ids) if key_ids is not None else None
        deleted: List[str] = []
        for credential in self._get_credentials(table, partner_id):
            key_id = credential.get("api_key_id")
            if wanted is not None and key_id not in wanted:
                continue
            not_found = apigw.exceptions.NotFoundException
            try:
                apigw.delete_usage_plan_key(usagePlanId=credential.get("usage_plan_id"), keyId=key_id)
            except not_found:
                pass
            try:
                apigw.delete_api_key(apiKey=key_id)
            except not_found:
                pass
            table.delete_item(Key={"PK": partner_pk(partner_id), "SK": partner_key_sk(key_id)})
            deleted.append(key_id)
        return deleted

    def list_gateway_keys_for_partner(self, partner_id: str) -> List[Dict[str, Any]]:
        """Keys API Gateway holds for ``partner_id`` (by tag), for reconciling a failed provision."""
        apigw = self._apigateway()
        keys: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {"includeValues": False}
        while True:
            page = apigw.get_api_keys(**kwargs)
            for key in page.get("items", []):
                if (key.get("tags") or {}).get("partner_id") == partner_id:
                    keys.append(key)
            if not page.get("position"):
                return keys
            kwargs["position"] = page["position"]
