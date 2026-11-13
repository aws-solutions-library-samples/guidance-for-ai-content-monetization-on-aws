"""Adapter catalog + active-Connection resolution (integration-adapters, GitLab #35, task 6.1).

The Registry is the adapter-discovery + connection-selection mechanism (design.md
"integrations/registry.py"). It has two jobs:

* :meth:`Registry.list_adapters` — the **catalog** of built-in adapters grouped by the four
  domains (content / marketplace / telemetry / access). Built-in adapters are compiled into
  the code and available in **every** deployment (Requirement 6.1), so the catalog is static.
* :meth:`Registry.resolve` — return the **active Connection** (a configured adapter instance)
  for a ``(domain, brand)``, read from the persisted ``cm-connections`` config + its Secrets
  Manager reference, or ``None`` when no active Connection exists — an adapter with no active
  Connection is simply not wired (Requirement 6.6, mirrors #29's flag / #25 capability
  detection). Multi-brand: N active connections of the same adapter keyed by ``brand``/tenant,
  resolved **per brand** (Requirement 6.3a).

Fail-closed resolution (Requirement 6.6): if a Connection's Secrets Manager reference cannot
be read, the adapter is treated as **not wired** (``resolve`` returns ``None``) rather than
falling back to a default or a plaintext credential.

Decoupling from the persistence store (task 10.1 not yet built)
---------------------------------------------------------------
The ``cm-connections`` table + its item shape are owned by task 10.1, which has **not** run.
To keep this task independently testable (task 6.2 property test, task 6.3 catalog test) and
to let 10.1 plug in the real store without rewriting the Registry, this module depends only on
two **injectable seams**, never on a concrete DynamoDB table:

* ``ConnectionStore`` — a thin structural interface (:class:`typing.Protocol`) exposing
  :meth:`list_active`; the Registry reads persisted connection config through it. An in-memory
  fake satisfies it for unit/property tests now; task 10.1's real ``cm-connections``-backed
  store satisfies the same Protocol later (dependency inversion — the Registry stays put).
* ``SecretResolver`` — a ``Callable[[str], str]`` that resolves a Secrets Manager reference to
  its secret value. Injectable so unit tests need no AWS; the default lazily uses
  ``secretsmanager`` (general ``boto3``, not adapter-owned / not guardrail-scoped). The secret
  value is passed to the adapter factory and **never** stored on the Registry or returned.

A ``ConnectionRecord`` is likewise a structural :class:`typing.Protocol` (the attributes the
Registry reads), not a concrete class — 10.1's persisted dataclass need only expose those
attributes to be resolvable here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from shared.utils.models import AdapterDescriptor, HealthStatus, TestResult

from integrations.access.aws_waf import AwsWafAccessPolicyAdapter
from integrations.marketplace.internal_dynamo import InternalDynamoLicenseResolver
from integrations.telemetry.cloudwatch import CloudWatchUsageSink
from integrations.telemetry.dynamo_analytics import DynamoAnalyticsUsageSink
from integrations.telemetry.dynamo_usage_events import DynamoUsageEventsSink

logger = logging.getLogger(__name__)

# ── Domain constants (the four integration domains, design "grouped by domain") ──
DOMAIN_CONTENT = "content"
DOMAIN_MARKETPLACE = "marketplace"
DOMAIN_TELEMETRY = "telemetry"
DOMAIN_ACCESS = "access"

DOMAINS = (DOMAIN_CONTENT, DOMAIN_MARKETPLACE, DOMAIN_TELEMETRY, DOMAIN_ACCESS)


# ─────────────────────────────────────────────────────────────────────────────
# Injectable seams (decoupled from task 10.1's cm-connections persistence store)
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ConnectionRecord(Protocol):
    """Structural view of a persisted ``cm-connections`` record the Registry reads.

    Task 10.1's concrete dataclass (fields per design "Connection config") satisfies this
    Protocol by exposing these attributes; the Registry never imports that dataclass, so it
    is not coupled to the persistence layer. Only the attributes the Registry needs are
    declared here — the secret value is *not* among them (it lives in Secrets Manager and is
    read through the :class:`SecretResolver`, never off the record; Requirement 6.4/7.2).
    """

    domain: str
    adapter: str
    brand: Optional[str]
    config: Dict[str, Any]
    secret_ref: str
    active: bool


class ConnectionStore(Protocol):
    """Read seam over the persisted connection config (task 10.1 provides the real one).

    The Registry reads persisted connection config **only** through this interface, so it is
    testable with an in-memory fake now and pluggable with the real ``cm-connections``-backed
    store later without any change to the Registry (dependency inversion, Requirement 6.6).
    """

    def list_active(self, domain: str, brand: Optional[str] = None) -> List[ConnectionRecord]:
        """Return the active connection records for ``(domain, brand)``.

        Implementations SHALL return only records with ``active`` true, matching the given
        ``domain`` and (per-brand resolution, Requirement 6.3a) the given ``brand`` — where
        ``brand is None`` selects the single-brand / brand-agnostic connection.
        """
        ...


# A SecretResolver maps a Secrets Manager reference (ARN/name) to the secret value.
SecretResolver = Callable[[str], str]

# An adapter factory builds a configured adapter instance from a connection's non-secret
# config plus the resolved secret value.
AdapterFactory = Callable[[Dict[str, Any], str], object]


class _WordPressContentConnectionPlaceholder:
    """Benign, registry-internal stand-in for the content→wordpress catalog entry.

    CATALOG-ONLY advertisement of the existing WordPress connector re-homed as a content-domain
    Connection in task 11.1. It exists so the WordPress content connector is *activatable* in the
    Connections UI (the content domain no longer renders "No adapters"), but it does **NOT** commit
    the ratified ``ContentSourcePort`` — that interface stays **Proposed** and is deferred to #14
    (transport unsettled: S3-export-JSON vs API-pull vs webhook). This placeholder is a stopgap
    until #14 builds the real content-ingestion adapter and ratifies the port.

    It is a pure placeholder: it constructs **no** adapter-owned client (no ``wafv2`` / marketplace
    clients) and no ``boto3`` at all. It exposes only the two shared operational shapes
    (``test()``/``status()``) so the Connections page can call them uniformly; both report success
    because the connection *record* exists — the actual ingest adapter lands with #14.
    """

    _MESSAGE = "WordPress content connection configured; content ingestion adapter lands with #14"

    def __init__(self, config: Dict[str, Any], secret: str) -> None:
        # Non-secret config is retained for parity with the real adapters' factory signature;
        # the resolved secret is intentionally not stored (nothing to authenticate against yet).
        self._config = config

    def test(self) -> TestResult:
        """Safe, read-only connectivity check — benign success (no real backend to reach yet)."""
        return TestResult(ok=True, message=self._MESSAGE)

    def status(self) -> HealthStatus:
        """Ongoing health of the active Connection — benign healthy (record exists; adapter is #14)."""
        return HealthStatus(healthy=True, detail=self._MESSAGE)


@dataclass(frozen=True)
class _BuiltinAdapter:
    """A built-in adapter catalog entry: its domain/name and how to construct it."""

    domain: str
    adapter: str
    factory: AdapterFactory


# ─────────────────────────────────────────────────────────────────────────────
# Built-in adapter catalog — available in EVERY deployment (Requirement 6.1)
#
# Grouped by the four domains. `content` advertises the WordPress connector (re-homed as a
# content-domain Connection in task 11.1) as a CATALOG-ONLY descriptor so it is activatable in
# the Connections UI — but it commits no ratified ContentSourcePort adapter (Proposed only —
# deferred to #14, Requirement 2.8): its factory returns a benign registry-internal placeholder
# (`_WordPressContentConnectionPlaceholder`), a stopgap until #14 builds the real content adapter.
#
# Each factory takes (config, secret) and returns a configured adapter instance. The
# wrapped adapters resolve their own backends from config/env (config-not-literals,
# Requirement 5.4); the secret is available to factories that need it (none of the
# current internal adapters require a plaintext secret — cm-licenses / WAF / CloudWatch
# use IAM, not a stored credential — but the seam passes it uniformly so a future
# credentialed adapter, e.g. WordPress, plugs in without a signature change).
# ─────────────────────────────────────────────────────────────────────────────

_BUILTIN_ADAPTERS: tuple[_BuiltinAdapter, ...] = (
    # content→wordpress: CATALOG-ONLY advertisement of the existing WordPress connector re-homed
    # as a content-domain Connection in task 11.1, so it is activatable in the Connections UI. It
    # does NOT commit the ratified ContentSourcePort (still Proposed, deferred to #14) — the
    # factory returns a benign in-module placeholder (no adapter-owned client, no boto3), a stopgap
    # until #14 builds the real content-ingestion adapter and ratifies the port.
    _BuiltinAdapter(
        domain=DOMAIN_CONTENT,
        adapter="wordpress",
        factory=lambda config, secret: _WordPressContentConnectionPlaceholder(config, secret),
    ),
    _BuiltinAdapter(
        domain=DOMAIN_ACCESS,
        adapter="aws_waf",
        factory=lambda config, secret: AwsWafAccessPolicyAdapter(
            web_acl_name=config.get("web_acl_name"),
            web_acl_scope=config.get("web_acl_scope", "REGIONAL"),
            bot_rules_table_name=config.get("bot_rules_table_name"),
        ),
    ),
    # telemetry->dynamo_usage_events: the CANONICAL raw usage-event sink (#3 task 9.4,
    # Requirements 8.3/8.4). Appends the canonical UsageEvent to cm-usage-events -- the ONE raw
    # store, written exactly once. Because resolve() returns a single active Connection per
    # (domain, brand) and the usage_emitter choke point hands each event to exactly one sink,
    # selecting this adapter is what makes raw truth land in cm-usage-events and nowhere else.
    # The two entries below remain in the catalog (they have live non-raw value -- see their
    # module docstrings) but are NOT the canonical raw store: cloudwatch writes the legacy
    # iab-access-logs row plus operational CloudWatch metrics, and dynamo_analytics writes into
    # cm-analytics, which Requirement 8.4 makes a *derived* view owned by #16.
    _BuiltinAdapter(
        domain=DOMAIN_TELEMETRY,
        adapter="dynamo_usage_events",
        factory=lambda config, secret: DynamoUsageEventsSink(table_name=config.get("table_name")),
    ),
    _BuiltinAdapter(
        domain=DOMAIN_TELEMETRY,
        adapter="cloudwatch",
        factory=lambda config, secret: CloudWatchUsageSink(),
    ),
    _BuiltinAdapter(
        domain=DOMAIN_TELEMETRY,
        adapter="dynamo_analytics",
        factory=lambda config, secret: DynamoAnalyticsUsageSink(table_name=config.get("table_name")),
    ),
    _BuiltinAdapter(
        domain=DOMAIN_MARKETPLACE,
        adapter="internal_dynamo",
        factory=lambda config, secret: InternalDynamoLicenseResolver(config=config),
    ),
)

# Index for O(1) factory lookup on resolve.
_BUILTIN_BY_KEY: Dict[tuple[str, str], _BuiltinAdapter] = {
    (b.domain, b.adapter): b for b in _BUILTIN_ADAPTERS
}


def _default_secret_resolver(secret_ref: str) -> str:
    """Resolve a Secrets Manager reference to its secret value (lazy default).

    Kept lazy so importing the Registry (and the unit/property tests that inject their own
    resolver) needs no ``boto3``/AWS. ``secretsmanager`` is general ``boto3`` — not an
    adapter-owned client and outside the task-14 guardrail scope.
    """
    import boto3  # local import: no AWS dependency at module import time

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_ref)
    return response.get("SecretString", "")


class Registry:
    """Adapter catalog + active-Connection resolution per ``(domain, brand)``.

    :param store: the connection-config read seam. Injected so this task is testable with an
        in-memory fake and task 10.1 can plug in the real ``cm-connections`` store without
        rewriting the Registry. When omitted, :meth:`resolve` has no persisted connections to
        read and always returns ``None`` (no store wired yet) — :meth:`list_adapters` still
        returns the full built-in catalog.
    :param secret_resolver: resolves a Secrets Manager reference to its value. Injected so unit
        tests need no AWS; defaults to a lazy ``secretsmanager`` client.
    """

    def __init__(
        self,
        store: Optional[ConnectionStore] = None,
        secret_resolver: Optional[SecretResolver] = None,
    ) -> None:
        self._store = store
        self._secret_resolver: SecretResolver = secret_resolver or _default_secret_resolver

    def list_adapters(self) -> Dict[str, List[AdapterDescriptor]]:
        """Return the built-in adapter catalog grouped by domain (Requirement 6.1).

        Every domain key is present, so the Connections page can render all four domains. The
        ``content`` domain advertises the WordPress connector as a catalog-only descriptor
        (content→wordpress) — activatable in the UI — even though the ratified
        ``ContentSourcePort`` remains Proposed (deferred to #14).
        ``has_active_connection`` reflects whether the injected store currently has an active
        connection for that adapter (across brands); it is ``False`` when no store is wired.
        """
        catalog: Dict[str, List[AdapterDescriptor]] = {domain: [] for domain in DOMAINS}
        for builtin in _BUILTIN_ADAPTERS:
            catalog[builtin.domain].append(
                AdapterDescriptor(
                    domain=builtin.domain,
                    adapter=builtin.adapter,
                    available=True,  # built-in: available in every deployment
                    has_active_connection=self._has_active_connection(builtin.domain, builtin.adapter),
                )
            )
        return catalog

    def resolve(self, domain: str, brand: Optional[str] = None) -> Optional[object]:
        """Return the active Connection (configured adapter instance) for ``(domain, brand)``.

        Reads the active connection records for ``(domain, brand)`` from the store, resolves the
        Secrets Manager reference, and constructs the configured built-in adapter. Returns
        ``None`` when:

        * no store is wired, or
        * no active connection matches ``(domain, brand)`` (adapter not wired, Requirement 6.6),
        * the record names an unknown built-in adapter, or
        * **fail-closed:** the Secrets Manager reference cannot be read (Requirement 6.6) — the
          adapter is treated as not wired rather than falling back to a default or plaintext.

        Per-brand resolution (Requirement 6.3a): ``brand`` selects among multiple active
        connections of the same adapter keyed by brand/tenant; ``brand=None`` selects the
        single-brand / brand-agnostic connection.
        """
        if self._store is None:
            return None

        records = [
            record
            for record in self._store.list_active(domain, brand)
            if record.active and record.domain == domain and record.brand == brand
        ]
        if not records:
            return None
        if len(records) > 1:
            # Per (domain, brand) there should be exactly one active connection; if the store
            # returns more, that is a store-integrity issue. Resolve deterministically (first)
            # and surface the anomaly rather than guessing silently.
            logger.warning(
                "Multiple active connections for domain=%s brand=%s; using the first",
                domain,
                brand,
            )
        record = records[0]

        builtin = _BUILTIN_BY_KEY.get((record.domain, record.adapter))
        if builtin is None:
            logger.warning(
                "Active connection names unknown built-in adapter domain=%s adapter=%s; not wired",
                record.domain,
                record.adapter,
            )
            return None

        # A Connection with no secret_ref needs no credential, so there is nothing to resolve.
        # `dynamo_usage_events`, `cloudwatch`, `dynamo_analytics` and `aws_waf` all authenticate
        # with the execution role's IAM, and `connections.py` stores `secret_ref = ""` for them.
        # Calling the resolver anyway means `get_secret_value(SecretId="")`, which always fails —
        # so a credential-less adapter could never resolve, and the canonical usage sink was
        # unreachable no matter how it was configured (GitLab #58).
        #
        # This is NOT a weakening of Requirement 6.6. Fail-closed applies to a secret that is
        # DECLARED but unreadable: that is a misconfiguration, and guessing past it would run an
        # adapter with partial config. An absent secret_ref is a Connection declaring it needs no
        # credential, which is a different statement.
        secret = ""
        if record.secret_ref:
            # Fail closed: a DECLARED but unreadable secret means NOT wired (Requirement 6.6).
            try:
                secret = self._secret_resolver(record.secret_ref)
            except Exception as exc:  # noqa: BLE001 — fail-closed, never fall back to a default/plaintext
                logger.warning(
                    "Secret ref could not be read for domain=%s adapter=%s brand=%s; failing closed (not wired): %s",
                    record.domain,
                    record.adapter,
                    record.brand,
                    exc,
                )
                return None

        return builtin.factory(dict(record.config), secret)

    # ── internals ──

    def _has_active_connection(self, domain: str, adapter: str) -> bool:
        """Whether the wired store has any active connection for ``(domain, adapter)`` (any brand)."""
        if self._store is None:
            return False
        try:
            records = self._store.list_active(domain)
        except Exception as exc:  # noqa: BLE001 — catalog flag is best-effort, never fail the catalog
            logger.warning("Store list_active failed for domain=%s while building catalog: %s", domain, exc)
            return False
        return any(record.active and record.domain == domain and record.adapter == adapter for record in records)
