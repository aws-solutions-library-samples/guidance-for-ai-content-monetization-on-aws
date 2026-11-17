"""Internal ``cm-licenses`` marketplace adapter (integration-adapters, GitLab #35, task 5.3).

The **first internal marketplace adapter**: it implements the ratified marketplace port(s)
by *delegating* to the existing stabilized services rather than rewriting them
(``refactoring-safety`` — the only change to a wrapped service is dependency inversion,
Requirement 3.4). Per the design's "First internal adapter:
``marketplace/internal_dynamo.py`` wraps ``license_service`` and ``pricing_manager``"
(Requirement 3.3):

  * :class:`InternalDynamoLicenseResolver` implements :class:`LicenseResolverPort` — the
    **PRIMARY** marketplace integration (Requirement 5.2) — backed by ``cm-licenses``
    through :mod:`services.license_service`. This is the *internal* (direct-partner)
    backend behind the same interface that AWS ``GetEntitlements`` / License Manager sit
    behind in the AWS adapters.

This adapter is **INTERNAL**: it reaches ``cm-licenses`` via the wrapped
``license_service`` (which uses the ``dynamodb`` resource — legitimate, pervasive, and
explicitly *not* in the task-14 guardrail scope). It MUST NOT construct the adapter-owned
AWS marketplace clients (``marketplaceentitlementservice``, ``meteringmarketplace``,
``marketplace-catalog``) — those live only in the AWS marketplace adapters
(``aws_entitlement`` / ``aws_metering`` / ``aws_catalog``), owned by the vertical (#3/#15)
and out of scope here. The task-14 conformance guardrail scopes to those specific clients.

Config-not-literals (Requirement 5.4): the ``ProductCode`` / entitlement region / dimension
mapping are read from injected config (the portal-visible MP-listing config mirror, analysis
§9.8) with environment-variable fallback — never hardcoded literals in this file. For the
internal ``cm-licenses`` backend these identifiers are informational (no AWS metering happens
here); they are surfaced on the resolved :class:`Entitlement` when present so the interface
is uniform with the AWS-backed adapters.

Scope: this task implements only the internal ``LicenseResolverPort`` backend. The
``BillingSinkPort`` (→ ``BatchMeterUsage``), ``MarketplaceOnboardingPort`` (→
``ResolveCustomer``), and ``MarketplaceListingPort`` (→ Catalog API) require AWS Marketplace
APIs not available to the internal DynamoDB backend and are owned by the AWS adapters
(#3/#15) — they are intentionally not implemented here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping, Optional

from shared.utils.models import Entitlement, HealthStatus, TestResult

logger = logging.getLogger(__name__)


class InternalDynamoLicenseResolver:
    """``LicenseResolverPort`` backed by ``cm-licenses`` via :mod:`services.license_service`.

    Implements the ratified :class:`~integrations.marketplace.port.LicenseResolverPort`
    contract (``resolve`` / ``validate`` / ``test`` / ``status``) for the internal
    direct-partner backend, delegating all ``cm-licenses`` access to the existing
    ``license_service`` module (single source of truth for the Licenses_Table).

    :param config: optional non-secret config for this connection (the portal-visible
        MP-listing config mirror, analysis §9.8). Recognized keys — all optional and read
        from config first, then environment, never hardcoded (Requirement 5.4):
        ``product_code``, ``region`` (entitlement region), and ``dimension_mapping``
        (a mapping of dimension name -> CoMP/pricing identifier). For the internal
        ``cm-licenses`` backend these are informational and surfaced on the resolved
        :class:`Entitlement`; no AWS metering is performed here.
    """

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self._config: Mapping[str, Any] = config or {}

    # ── Config accessors (config first, then env — no hardcoded literals; Req 5.4) ──

    def _config_value(self, config_key: str, env_key: str) -> Optional[str]:
        """Resolve a config value from the injected config, falling back to the environment.

        Returns ``None`` when neither is set — the adapter never substitutes a hardcoded
        literal for the product code / region / dimension mapping (Requirement 5.4).
        """
        value = self._config.get(config_key)
        if value is None:
            value = os.environ.get(env_key)
        return value

    @property
    def _product_code(self) -> Optional[str]:
        """The marketplace ``ProductCode`` from config/env (informational for this backend)."""
        return self._config_value("product_code", "MARKETPLACE_PRODUCT_CODE")

    @property
    def _region(self) -> Optional[str]:
        """The entitlement region from config/env (informational for this backend)."""
        return self._config_value("region", "MARKETPLACE_ENTITLEMENT_REGION")

    @property
    def _dimension_mapping(self) -> Mapping[str, Any]:
        """The dimension mapping from config/env (empty when unset — never a hardcoded default)."""
        mapping = self._config.get("dimension_mapping")
        if mapping is None:
            raw = os.environ.get("MARKETPLACE_DIMENSION_MAPPING")
            if raw:
                try:
                    mapping = json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("MARKETPLACE_DIMENSION_MAPPING is not valid JSON; ignoring")
                    mapping = None
        return mapping or {}

    # ── LicenseResolverPort ──

    def resolve(self, consumer_ref: str) -> Entitlement:
        """Resolve a consumer reference to its :class:`Entitlement` via ``cm-licenses``.

        Delegates to :func:`services.license_service.get_license` (the single ``cm-licenses``
        access point). For the internal direct-partner backend the ``consumer_ref`` is the
        license identifier the partner holds; the resolved license's ``allowed_uses`` become
        the entitlement's permitted CoMP function codes. The marketplace ``product_code`` /
        region come from config (Requirement 5.4) and are surfaced on the entitlement when
        present so the shape matches the AWS-backed adapters.

        :param consumer_ref: the consumer identifier — here, the ``license_id`` the partner
            holds in ``cm-licenses``.
        :returns: the server-side :class:`Entitlement` authorization record.
        :raises LookupError: if no active license is found for ``consumer_ref``.
        """
        # license_service is the single cm-licenses home; it returns an HTTP-shaped dict.
        from services import license_service

        response = license_service.get_license(consumer_ref)
        status_code = response.get("statusCode")
        if status_code != 200:
            raise LookupError(f"No active entitlement for consumer_ref '{consumer_ref}' (status {status_code})")

        license_doc = json.loads(response["body"])
        allowed_uses = [int(u) for u in license_doc.get("allowed_uses", [])]

        return Entitlement(
            partner_id=consumer_ref,
            license_id=license_doc.get("license_id", consumer_ref),
            allowed_uses=allowed_uses,
            active=license_doc.get("status", "active") == "active",
            product_code=self._product_code,
            customer_identifier=None,
            license_arn=None,
        )

    def validate(self, entitlement: Entitlement, declared_use: int) -> bool:
        """Validate a declared CoMP use against the entitlement's allowed uses.

        Pure, in-memory check over the already-resolved :class:`Entitlement` (no I/O): an
        inactive entitlement is never valid, otherwise ``declared_use`` must be one of the
        entitlement's allowed CoMP function codes.

        :param entitlement: the resolved :class:`Entitlement`.
        :param declared_use: the CoMP function code the caller declared.
        :returns: ``True`` iff ``declared_use`` is permitted by ``entitlement``.
        """
        if not entitlement.active:
            return False
        return int(declared_use) in {int(u) for u in entitlement.allowed_uses}

    # ── Common operational surface (Requirements 2.9, 6.8, 7.3) ──

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check against ``cm-licenses``.

        Performs only a benign read (``list_licenses``) through ``license_service`` — no
        license is created, updated, or deleted (Requirement 7.3). Fail-informative: a
        failure returns ``TestResult(ok=False, ...)`` rather than raising past the caller
        (Requirement 6.5).
        """
        from services import license_service

        try:
            response = license_service.list_licenses({"limit": "1"}, username=None)
            status_code = response.get("statusCode")
            if status_code == 200:
                return TestResult(ok=True, message="cm-licenses reachable")
            return TestResult(ok=False, message=f"cm-licenses returned status {status_code}")
        except Exception as exc:  # noqa: BLE001 — surface diagnostic, never raise past test()
            return TestResult(ok=False, message=f"cm-licenses connectivity check failed: {exc}")

    def status(self) -> HealthStatus:
        """Ongoing health of the active internal ``cm-licenses`` connection.

        Reuses the same safe read as :meth:`test`; distinct method per the common port
        surface (``status()`` = ongoing health of an active Connection).
        """
        result = self.test()
        return HealthStatus(healthy=result.ok, detail=result.message)
