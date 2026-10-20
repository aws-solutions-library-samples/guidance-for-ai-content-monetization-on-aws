"""Registry catalog unit test (integration-adapters, GitLab #35, task 6.3).

EXAMPLE-class unit test (plain pytest, not a property test) for
:meth:`integrations.registry.Registry.list_adapters` — the built-in adapter catalog grouped by
the four integration domains (design "integrations/registry.py"; Requirement 6.1):

  * ``list_adapters()`` returns a dict with **all four** domain keys — content, marketplace,
    telemetry, access. ``content`` advertises the WordPress connector as a catalog-only
    descriptor (content→wordpress) so it is activatable in the UI, while the ratified
    ``ContentSourcePort`` remains Proposed-only (deferred to #14; Requirement 2.8 / 6.3b).
  * The built-in adapters are grouped under the **correct** domain: access → ``aws_waf``;
    telemetry → ``cloudwatch`` + ``dynamo_analytics``; marketplace → ``internal_dynamo``;
    content → ``wordpress`` (a catalog-only advertisement of the existing WordPress connector
    re-homed as a content-domain Connection in task 11.1, activatable in the UI — while the
    ratified ``ContentSourcePort`` remains Proposed, deferred to #14).
  * Each entry is an :class:`shared.utils.models.AdapterDescriptor` with ``available=True``
    (built-in in every deployment, Requirement 6.1) and the correct ``domain``/``adapter``.
  * With no store wired, ``has_active_connection`` is ``False`` for every entry (an adapter with
    no active Connection is simply not wired; Requirement 6.6).

**Validates: Requirements 6.1**

No AWS: ``list_adapters()`` reads only the static built-in catalog and constructs no adapter (no
factory is invoked, no ``boto3`` client is built), so a default ``Registry()`` with no store /
no secret resolver is safe and deterministic. Gated unit tier
(``tests/integration_adapters/``, ``-m "not integration"``). ``registry.py`` is not modified.

Distinct filename (``test_registry_catalog.py``) — does not overwrite the parallel property test
``test_registry_resolve_unique_connection_property.py`` (task 6.2).

Collection-warning note: this module imports no ``TestResult`` (or any ``Test*``-named symbol)
and names no helper ``Test*``, so pytest emits no ``PytestCollectionWarning``.
"""

from __future__ import annotations

from shared.utils.models import AdapterDescriptor

from integrations.registry import (
    DOMAIN_ACCESS,
    DOMAIN_CONTENT,
    DOMAIN_MARKETPLACE,
    DOMAIN_TELEMETRY,
    Registry,
)

# The built-in catalog the design/registry commits to (registry._BUILTIN_ADAPTERS), expressed as
# the expected {domain: {adapter names}} grouping — asserted independently of the module's own
# internals so this test pins the *contract*, not the implementation detail.
EXPECTED_CATALOG: dict[str, set[str]] = {
    DOMAIN_CONTENT: {"wordpress"},  # catalog-only advertisement (task 11.1); port still Proposed (#14)
    DOMAIN_MARKETPLACE: {"internal_dynamo"},
    DOMAIN_TELEMETRY: {"cloudwatch", "dynamo_analytics"},
    DOMAIN_ACCESS: {"aws_waf"},
}


def _catalog() -> dict[str, list[AdapterDescriptor]]:
    """A catalog from a default Registry (no store / no secret resolver wired)."""
    return Registry().list_adapters()


def test_list_adapters_returns_all_four_domain_keys():
    """The catalog dict has exactly the four domain keys, each present even when empty (Req 6.1)."""
    catalog = _catalog()
    assert set(catalog.keys()) == {
        DOMAIN_CONTENT,
        DOMAIN_MARKETPLACE,
        DOMAIN_TELEMETRY,
        DOMAIN_ACCESS,
    }


def test_content_domain_has_wordpress():
    """content advertises the wordpress catalog descriptor (task 11.1) — activatable in the UI.

    The entry is a catalog-only advertisement of the existing WordPress connector; the ratified
    ``ContentSourcePort`` remains Proposed-only (deferred to #14, Requirement 2.8).
    """
    catalog = _catalog()
    assert {e.adapter for e in catalog[DOMAIN_CONTENT]} == {"wordpress"}


def test_builtin_adapters_grouped_under_correct_domains():
    """Each built-in adapter appears under its correct domain, and nowhere else (Req 6.1)."""
    catalog = _catalog()
    actual = {
        domain: {entry.adapter for entry in entries} for domain, entries in catalog.items()
    }
    assert actual == EXPECTED_CATALOG


def test_access_domain_has_aws_waf():
    """access → aws_waf."""
    catalog = _catalog()
    assert {e.adapter for e in catalog[DOMAIN_ACCESS]} == {"aws_waf"}


def test_telemetry_domain_has_cloudwatch_and_dynamo_analytics():
    """telemetry → cloudwatch + dynamo_analytics."""
    catalog = _catalog()
    assert {e.adapter for e in catalog[DOMAIN_TELEMETRY]} == {"cloudwatch", "dynamo_analytics"}


def test_marketplace_domain_has_internal_dynamo():
    """marketplace → internal_dynamo."""
    catalog = _catalog()
    assert {e.adapter for e in catalog[DOMAIN_MARKETPLACE]} == {"internal_dynamo"}


def test_every_entry_is_an_available_adapter_descriptor():
    """Every catalog entry is an AdapterDescriptor with available=True and matching domain (Req 6.1).

    Built-in adapters are available in every deployment, so ``available`` is always ``True`` and
    each entry's ``domain`` matches the key it is filed under.
    """
    catalog = _catalog()
    for domain, entries in catalog.items():
        for entry in entries:
            assert isinstance(entry, AdapterDescriptor)
            assert entry.available is True
            assert entry.domain == domain
            assert isinstance(entry.adapter, str) and entry.adapter


def test_no_active_connection_when_no_store_wired():
    """With no store wired, has_active_connection is False for every entry (Requirement 6.6)."""
    catalog = _catalog()
    for entries in catalog.values():
        for entry in entries:
            assert entry.has_active_connection is False
