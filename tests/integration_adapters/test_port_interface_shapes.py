"""Interface-shape unit tests for the ratified ports (integration-adapters, GitLab #35, task 3.5).

Asserts the *shape* of the ratified port interfaces committed by tasks 3.1–3.4 — the declared
method set of each port, the one-way-projection foreclosure on ``AccessPolicyPort``, the
marketplace three/four-separate-Protocols split, and the deliberate absence of a ratified
``ContentSourcePort``. These are EXAMPLE-class checks (design "Testing Strategy" → interface-shape):
they do not vary meaningfully with generated input, so they are plain unit assertions rather than
property tests. Placement is the gated unit tier (``tests/integration_adapters/``,
``-m "not integration"``).

Requirements validated:
  * 2.1 — each port exposes only the methods its domain needs (exact declared method set).
  * 2.2 — ``AccessPolicyPort`` is one-way: it has ``push``/``get_metrics``/``detect_drift`` and
    LACKS ``pull_rules``/``write_back`` (the two-way-sync class of bug cannot be written here).
  * 2.8 — no ratified ``ContentSourcePort`` is committed (the content port is Proposed only).
  * 5.1 — the marketplace domain keeps four SEPARATE Protocols; there is NO collapsed
    single ``MarketplacePort`` symbol.

Introspection note: the ratified ports reference the model type ``TestResult`` in their
signatures. ``TestResult`` is a plain dataclass whose name matches pytest's ``Test*`` collection
glob, so importing it into a test module's namespace triggers a benign ``PytestCollectionWarning``
(pytest tries to collect it, finds an ``__init__``, and skips it). The existing
``tests/integration_adapters/test_model_convention_and_scope.py`` already imports ``TestResult``
and carries that one benign warning. To avoid *adding a second* such warning, this module does NOT
import ``TestResult`` (or any ``*Result``/``Test*``-named model) into its namespace; it inspects the
ports by introspection only (importing the port Protocols, which are not ``Test*``-named).
"""

from __future__ import annotations

from typing import Protocol

from integrations.access.port import AccessPolicyPort
from integrations.marketplace.port import (
    BillingSinkPort,
    LicenseResolverPort,
    MarketplaceListingPort,
    MarketplaceOnboardingPort,
)
from integrations.telemetry.port import UsageSinkPort

# Names that are always present on a typing.Protocol subclass via Protocol/Generic/object and are
# not part of the port's declared domain surface — excluded when computing a port's method set.
_PROTOCOL_INFRA_BASES = {"Protocol", "Generic", "object"}


def _declared_members(proto: type) -> set[str]:
    """Return a Protocol's own declared public members (its domain method surface).

    Walks the Protocol's MRO and collects names defined on the Protocol classes themselves,
    excluding the ``Protocol``/``Generic``/``object`` infrastructure classes and all dunder /
    private (leading-underscore) names. For the ratified ports these members are all methods, so
    the result is the port's exact declared method set (Requirement 2.1).
    """
    members: set[str] = set()
    for klass in proto.__mro__:
        if klass.__name__ in _PROTOCOL_INFRA_BASES:
            continue
        for name in vars(klass):
            if name.startswith("_"):
                continue
            members.add(name)
    return members


# --- Requirement 2.1 / 2.2 / 2.9 / 6.8: exact declared method set per ratified port -------------

def test_access_policy_port_exact_method_set():
    """AccessPolicyPort declares EXACTLY push/get_metrics/detect_drift/test/status (Req 2.1, 2.2)."""
    assert _declared_members(AccessPolicyPort) == {
        "push",
        "get_metrics",
        "detect_drift",
        "test",
        "status",
    }


def test_usage_sink_port_exact_method_set():
    """UsageSinkPort declares EXACTLY emit/test/status — a single choke point (Req 2.3, 2.9, 6.8)."""
    assert _declared_members(UsageSinkPort) == {"emit", "test", "status"}


def test_license_resolver_port_exact_method_set():
    """LicenseResolverPort declares EXACTLY resolve/validate/test/status (Req 2.4, 2.9, 6.8)."""
    assert _declared_members(LicenseResolverPort) == {"resolve", "validate", "test", "status"}


def test_billing_sink_port_exact_method_set():
    """BillingSinkPort declares EXACTLY report/test/status (Req 2.5, 2.9, 6.8)."""
    assert _declared_members(BillingSinkPort) == {"report", "test", "status"}


def test_marketplace_onboarding_port_exact_method_set():
    """MarketplaceOnboardingPort declares EXACTLY resolve_customer/test/status (Req 2.6, 2.9, 6.8)."""
    assert _declared_members(MarketplaceOnboardingPort) == {"resolve_customer", "test", "status"}


def test_marketplace_listing_port_exact_method_set():
    """MarketplaceListingPort declares EXACTLY push/get_listing_status/test/status (Req 2.7)."""
    assert _declared_members(MarketplaceListingPort) == {
        "push",
        "get_listing_status",
        "test",
        "status",
    }


# --- Requirement 2.2: one-way projection — AccessPolicyPort LACKS the write-back surface ---------

def test_access_policy_port_lacks_write_back_surface():
    """AccessPolicyPort has no pull_rules/write_back (the one-way-projection foreclosure, Req 2.2).

    The load-bearing rule from the WAF two-way-sync bug: the port must not carry any method that
    reads the target's live rule-state as authoring input or writes it back into the source of
    truth. Asserting the *absence* of these method names makes the two-way-sync class of bug
    unwritable against the port.
    """
    members = _declared_members(AccessPolicyPort)
    forbidden = {"pull_rules", "write_back"}
    present = members & forbidden
    assert not present, f"AccessPolicyPort must not declare one-way-projection-violating methods: {present}"
    # Also assert none of the forbidden names are attributes on the Protocol at all.
    for name in forbidden:
        assert not hasattr(AccessPolicyPort, name), (
            f"AccessPolicyPort must not expose '{name}' (one-way projection, Req 2.2)"
        )


# --- Requirement 5.1: marketplace domain is FOUR separate Protocols, no collapsed MarketplacePort -

def test_marketplace_domain_has_separate_protocols():
    """The marketplace domain keeps four SEPARATE port Protocols (Req 5.1).

    Mirrors AWS's own service split (Entitlement / Metering / Catalog) plus the outbound listing
    push — collapsing them into one interface would be the wrong abstraction.
    """
    ports = [
        LicenseResolverPort,
        BillingSinkPort,
        MarketplaceOnboardingPort,
        MarketplaceListingPort,
    ]
    # They are four distinct Protocol types (no aliasing to a single object).
    assert len({id(p) for p in ports}) == 4
    for p in ports:
        assert issubclass(p, Protocol), f"{p.__name__} must be a typing.Protocol"


def test_no_collapsed_marketplace_port_symbol():
    """There is NO single collapsed ``MarketplacePort`` symbol in the marketplace port module (Req 5.1)."""
    from integrations.marketplace import port as marketplace_port

    assert not hasattr(marketplace_port, "MarketplacePort"), (
        "the marketplace domain must expose four separate ports, not a collapsed 'MarketplacePort'"
    )


# --- Requirement 2.8: no ratified ContentSourcePort committed ------------------------------------

def test_no_ratified_content_source_port_committed():
    """Importing the content port module exposes NO ratified ``ContentSourcePort`` (Req 2.8).

    ``ContentSourcePort`` is a Proposed_Port only (transport unsettled — S3-export-JSON vs
    API-pull vs webhook), deferred to #14. This spec must commit no interface for it: the module
    must not expose a ``ContentSourcePort`` class/Protocol (the proposed shape lives in prose /
    commented sketch only).
    """
    from integrations.content import port as content_port

    assert not hasattr(content_port, "ContentSourcePort"), (
        "integrations.content.port must NOT expose a ratified ContentSourcePort "
        "(it is Proposed only, deferred to #14 — Req 2.8)"
    )
    # No public Protocol/class is committed in the content port module at all.
    public_classes = [
        name
        for name, obj in vars(content_port).items()
        if not name.startswith("_") and isinstance(obj, type)
    ]
    assert not public_classes, (
        f"integrations.content.port must commit no public port class yet (Proposed only): {public_classes}"
    )
