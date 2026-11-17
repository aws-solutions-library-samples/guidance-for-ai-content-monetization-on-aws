"""Marketplace-domain ports (integration-adapters, GitLab #35, task 3.3).

Defines the ratified marketplace ports as **four separate** ``Protocol``s — never a
single collapsed ``MarketplacePort`` — mirroring AWS's own three-service split
(Entitlement Service, Metering Service, Catalog API) plus the outbound listing push
(Requirement 5.1):

  * :class:`LicenseResolverPort`      — PRIMARY integration; resolves/validates
    entitlements at the content-serving gate (Path B). Backed by ``cm-licenses`` today
    and AWS ``GetEntitlements`` (License Manager future) behind the same interface.
  * :class:`BillingSinkPort`          — SECONDARY (usage-priced only); batch-shaped,
    idempotent metering (the AWS ``BatchMeterUsage`` contract).
  * :class:`MarketplaceOnboardingPort` — onboarding-time customer resolution (not
    hot-path); the AWS ``ResolveCustomer`` shape.
  * :class:`MarketplaceListingPort`   — outbound, **one-way** listing projection; async,
    review-gated (NOT idempotent-converge).

Every ratified port also exposes the common operational surface (Requirements 2.9, 6.8):
  * ``test()``   — a safe, read-only connectivity/auth check the Connections page runs to
    validate a config before/while activating a Connection (no real domain operation).
  * ``status()`` — the ongoing health of an already-active Connection.

Per the hexagonal dependency direction (Requirement 1.2), this port depends on **nothing
external** — only the Canonical_Model types in ``shared.utils.models`` and stdlib typing.
The port-import-direction guard (``tests/import_guard/test_port_import_direction.py``)
enforces that no ``boto3``/vendor SDK or cross-domain module is imported here. The
adapter-owned marketplace clients (``marketplaceentitlementservice``, ``meteringmarketplace``,
``marketplace-catalog``) appear only in the concrete adapters (``marketplace/*.py``, task 5.3).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.utils.models import (
    ChangeRequest,
    CustomerRegistration,
    Entitlement,
    HealthStatus,
    ListingDefinition,
    ListingStatus,
    ReportResult,
    TestResult,
    UsageEvent,
)


@runtime_checkable
class LicenseResolverPort(Protocol):
    """Resolve and validate a consumer's entitlement (the PRIMARY marketplace integration).

    Abstracts the licensing authority so callers do not know whether the answer came
    from ``cm-licenses``, AWS ``GetEntitlements``, or (future) AWS License Manager
    (Requirements 2.4, 5.2). Used at the content-serving gate (Path B).
    """

    def resolve(self, consumer_ref: str) -> Entitlement:
        """Resolve a consumer reference to its :class:`Entitlement` (partner-holds-license).

        :param consumer_ref: the caller/consumer identifier (e.g. partner key,
            customer identifier) whose entitlement is being resolved.
        :returns: the server-side :class:`Entitlement` authorization record.
        """
        ...

    def validate(self, entitlement: Entitlement, declared_use: int) -> bool:
        """Validate a declared CoMP use against the entitlement's allowed uses.

        :param entitlement: the resolved :class:`Entitlement`.
        :param declared_use: the CoMP function code the caller declared.
        :returns: ``True`` iff ``declared_use`` is permitted by ``entitlement``.
        """
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check (e.g. a benign ``GetEntitlements``)."""
        ...

    def status(self) -> HealthStatus:
        """Ongoing health of an active Connection (distinct from ``test()``)."""
        ...


@runtime_checkable
class BillingSinkPort(Protocol):
    """Meter usage to a marketplace billing sink (SECONDARY; usage-priced products only).

    Batch-shaped and idempotent by design — the AWS ``BatchMeterUsage`` contract is a
    set call, one product per call — NOT a per-event ``report(one_event)``
    (Requirement 2.5). The concrete adapter (owned by #3) additionally honours the
    per-agreement dimension-membership check (``GetAgreementTerms``).
    """

    def report(self, batch: list[UsageEvent], *, idempotency_key: str) -> ReportResult:
        """Report a batch of usage events for metering (batch + idempotent).

        :param batch: the usage events to meter.
        :param idempotency_key: a key making retries safe — reporting the same batch
            under the same key does not double-meter.
        :returns: a :class:`ReportResult` describing accepted/rejected counts.
        """
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check (no real metering)."""
        ...

    def status(self) -> HealthStatus:
        """Ongoing health of an active Connection (distinct from ``test()``)."""
        ...


@runtime_checkable
class MarketplaceOnboardingPort(Protocol):
    """Resolve a marketplace customer at onboarding time (NOT hot-path).

    Models the AWS ``ResolveCustomer`` shape: exchange a registration token for the
    customer/account/product/license identifiers (Requirement 2.6).
    """

    def resolve_customer(self, registration_token: str) -> CustomerRegistration:
        """Resolve a registration token to a :class:`CustomerRegistration`.

        :param registration_token: the marketplace-supplied onboarding token.
        :returns: the resolved ``{customer_id, aws_account_id, product_code,
            license_arn}`` registration.
        """
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check (no real onboarding)."""
        ...

    def status(self) -> HealthStatus:
        """Ongoing health of an active Connection (distinct from ``test()``)."""
        ...


@runtime_checkable
class MarketplaceListingPort(Protocol):
    """Project an authored marketplace listing outbound (outbound, one-way).

    ``push`` is async and review-gated (NOT idempotent-converge): it returns a
    :class:`ChangeRequest` modeling per-marketplace pending/approved/rejected state
    (Requirement 2.7).

    One-way projection (Requirement 2.7): this port MUST NOT pull marketplace state as
    authoring input, and MUST NOT silently mutate live agreement pricing. Authoring
    lives in our source of truth; the marketplace is a downstream target.
    """

    def push(self, listing_definition: ListingDefinition) -> ChangeRequest:
        """Push an authored listing to the marketplace as a review-gated change request.

        :param listing_definition: the authored, SSOT-side listing definition.
        :returns: a :class:`ChangeRequest` (async, review-gated — not idempotent-converge).
        """
        ...

    def get_listing_status(self, marketplace: str) -> ListingStatus:
        """Return the per-marketplace listing state (pending | approved | rejected).

        :param marketplace: the marketplace whose listing state is queried.
        :returns: the :class:`ListingStatus` for that marketplace.
        """
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check (no listing change)."""
        ...

    def status(self) -> HealthStatus:
        """Ongoing health of an active Connection (distinct from ``test()``)."""
        ...
