"""Unit tests for the canonical raw usage-event sink (license-lifecycle-usage-events, GitLab #3,
task 9.4, Requirements 8.3, 8.4).

EXAMPLE-class tests (plain pytest with an in-memory recording fake, no live AWS) for
``integrations/telemetry/dynamo_usage_events.py`` — the adapter that completes the
``iab-access-logs`` -> ``cm-usage-events`` fold so raw truth is written **exactly once**.

What is pinned here:

* the item carries task 9.1's key schema — ``PK = PARTNER#<partner_id>#<YYYY-MM-DD>`` and
  ``SK = <accessed_at>#<request_id>``;
* the ``ttl`` attribute from the choke point's ``event_ttl()`` sidecar is persisted, and an event
  with no TTL is written **without** a fabricated one;
* the ``source`` marker from ``event_source()`` is carried, so a self-report stays distinguishable
  from a server-observed unit (Requirement 9.7);
* every ``UsageEvent`` field is present as a plain **top-level** attribute (task 9.1: events stay
  readable without parsing composite keys);
* the append is **exactly one** ``put_item`` — and **no read operation** of any kind, matching the
  append-only IAM grant (``PutItem`` + ``BatchWriteItem`` only, no ``GetItem`` / ``Query`` /
  ``Scan`` / ``UpdateItem`` / ``DeleteItem`` / ``DescribeTable``);
* the adapter implements ``UsageSinkPort``, and ``test()`` writes nothing;
* ``cm-analytics`` is not touched (Requirement 8.4 — nothing raw is written into the derived view).

Placement: gated unit tier (``tests/integration_adapters/``, ``-m "not integration"``),
deterministic. Following the sibling modules' convention, no ``Test*``-named symbol is imported and
helper fakes are named ``Recording*`` so pytest emits no ``PytestCollectionWarning``; adapter
``test()`` results are inspected by attribute.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

import pytest

from integrations.telemetry.dynamo_usage_events import (
    DEFAULT_USAGE_EVENTS_TABLE_NAME,
    USAGE_EVENTS_TABLE_NAME_ENV,
    DynamoUsageEventsSink,
    usage_event_to_item,
)
from integrations.telemetry.port import UsageSinkPort
from services.usage_emitter import SOURCE_ATTR, SOURCE_OBSERVED, SOURCE_SELF_REPORTED, TTL_ATTR
from shared.utils.models import UsageEvent

#: Every DynamoDB table operation that reads. The append-only grant permits none of them, so a
#: call to any of these would fail at runtime in the deployed environment.
READ_OPERATIONS = ("get_item", "query", "scan", "batch_get_item", "load", "update_item", "delete_item")


class RecordingTable:
    """In-memory spy for a DynamoDB ``Table`` recording every operation invoked on it.

    Any read operation raises rather than returning a value: the deployed grant denies reads, so a
    test that silently tolerated one would pass locally and fail in production. Attribute access
    for an unknown operation also raises, so a future read shape cannot slip through unnoticed.
    """

    def __init__(self) -> None:
        self.put_items: List[Dict[str, Any]] = []
        self.calls: List[str] = []

    def put_item(self, Item: Dict[str, Any]) -> Dict[str, Any]:  # noqa: N803 (AWS casing)
        self.calls.append("put_item")
        self.put_items.append(Item)
        return {}

    def __getattr__(self, name: str) -> Any:
        if name in READ_OPERATIONS:
            raise AssertionError(
                f"the raw usage-event sink performed a read operation ({name}) — the task 9.1 "
                "IAM grant is PutItem/BatchWriteItem only, so this is denied at runtime"
            )
        raise AttributeError(name)


def _sink() -> tuple[DynamoUsageEventsSink, RecordingTable]:
    """Build the real adapter with its table resource replaced by a recording fake."""
    sink = DynamoUsageEventsSink(table_name="fake-usage-events")
    table = RecordingTable()
    sink._table = table
    return sink, table


def _event(**overrides: Any) -> UsageEvent:
    """A canonical event as the choke point would build it, sidecars included."""
    event = UsageEvent(
        content_id=overrides.pop("content_id", "content-1"),
        partner_id=overrides.pop("partner_id", "partner-1"),
        accessed_at=overrides.pop("accessed_at", "2026-03-14T09:15:30.123456+00:00"),
        price_charged=overrides.pop("price_charged", 0.05),
        currency=overrides.pop("currency", "USD"),
        license_id=overrides.pop("license_id", "lic-1"),
        content_type=overrides.pop("content_type", "url_retrieval"),
        request_id=overrides.pop("request_id", "req-abc"),
        declared_use=overrides.pop("declared_use", 3),
    )
    # Sidecars the choke point stamps (not dataclass fields — Requirement 8.9).
    setattr(event, SOURCE_ATTR, overrides.pop("source", SOURCE_OBSERVED))
    ttl = overrides.pop("ttl", 1_800_000_000)
    if ttl is not None:
        setattr(event, TTL_ATTR, ttl)
    assert not overrides, f"unexpected overrides: {sorted(overrides)}"
    return event


# ================================================================================================
# Key schema (task 9.1's contract)
# ================================================================================================


def test_pk_is_partner_and_day_bucketed():
    """PK = PARTNER#<partner_id>#<YYYY-MM-DD>, the day taken from the event's own instant."""
    item = usage_event_to_item(_event())
    assert item["PK"] == "PARTNER#partner-1#2026-03-14"


def test_sk_is_accessed_at_then_request_id():
    """SK = <accessed_at>#<request_id> — unique per event, so the append never collides."""
    item = usage_event_to_item(_event())
    assert item["SK"] == "2026-03-14T09:15:30.123456+00:00#req-abc"


def test_sk_stays_unique_when_request_id_is_absent():
    """With no request_id the SK still differs per event, and no fake request_id is persisted."""
    first = usage_event_to_item(_event(request_id=None))
    second = usage_event_to_item(_event(request_id=None))

    assert first["SK"] != second["SK"], "two events at the same instant would overwrite each other"
    assert first["SK"].startswith("2026-03-14T09:15:30.123456+00:00#")
    # The uniqueness token lives only in the key — a reader can tell it from a real request id.
    assert "request_id" not in first


def test_day_bucket_follows_the_access_instant_not_the_write_time():
    """An event stamped on a different UTC day lands in that day's partition, not today's."""
    item = usage_event_to_item(_event(accessed_at="2025-12-31T23:59:59+00:00"))
    assert item["PK"] == "PARTNER#partner-1#2025-12-31"


def test_unparseable_accessed_at_still_produces_a_writable_item():
    """A malformed timestamp degrades to the current day rather than dropping a billable event."""
    item = usage_event_to_item(_event(accessed_at="not-a-timestamp"))
    assert item["PK"].startswith("PARTNER#partner-1#")
    assert len(item["PK"].rsplit("#", 1)[1]) == len("2026-03-14")


# ================================================================================================
# TTL + source marker (the two sidecars read via the documented accessors)
# ================================================================================================


def test_ttl_attribute_is_persisted_from_the_sidecar():
    """Req 8.3 — the raw store is TTL'd on the 'ttl' attribute task 9.1 configured."""
    item = usage_event_to_item(_event(ttl=1_800_000_000))
    assert item["ttl"] == 1_800_000_000


def test_no_ttl_attribute_when_the_event_carries_none():
    """An event that did not come through the choke point gets no invented retention window."""
    item = usage_event_to_item(_event(ttl=None))
    assert "ttl" not in item


def test_observed_source_marker_is_carried():
    item = usage_event_to_item(_event(source=SOURCE_OBSERVED))
    assert item["source"] == SOURCE_OBSERVED


def test_self_reported_source_marker_is_carried():
    """Req 9.7 — a self-report stays distinguishable from a server-observed billable unit."""
    item = usage_event_to_item(_event(source=SOURCE_SELF_REPORTED))
    assert item["source"] == SOURCE_SELF_REPORTED


# ================================================================================================
# Top-level attributes (task 9.1: readable without parsing composite keys)
# ================================================================================================


def test_all_usage_event_fields_are_plain_top_level_attributes():
    """Every UsageEvent field is stored flat, not only folded into PK/SK (task 9.1)."""
    item = usage_event_to_item(_event())

    assert item["partner_id"] == "partner-1"
    assert item["content_id"] == "content-1"
    assert item["accessed_at"] == "2026-03-14T09:15:30.123456+00:00"
    assert item["currency"] == "USD"
    assert item["license_id"] == "lic-1"
    assert item["content_type"] == "url_retrieval"
    assert item["request_id"] == "req-abc"
    assert item["declared_use"] == 3


def test_price_charged_is_a_decimal_not_a_float():
    """DynamoDB rejects float; Decimal(str(...)) keeps the amount the caller meant (this is money)."""
    item = usage_event_to_item(_event(price_charged=0.05))
    assert isinstance(item["price_charged"], Decimal)
    assert item["price_charged"] == Decimal("0.05")


def test_optional_fields_absent_rather_than_null():
    """None-valued optionals are omitted, matching the shared models / entitlements_store convention."""
    item = usage_event_to_item(_event(declared_use=None))
    assert "declared_use" not in item


# ================================================================================================
# The write itself — exactly one put_item, no read
# ================================================================================================


def test_emit_performs_exactly_one_put_item():
    """Req 8.3 — raw truth is written exactly once per event."""
    sink, table = _sink()

    sink.emit(_event())

    assert table.calls == ["put_item"]
    assert len(table.put_items) == 1


def test_emit_performs_no_read_operation():
    """The append-only grant permits no read; RecordingTable raises if one is attempted."""
    sink, table = _sink()

    sink.emit(_event())

    assert not [call for call in table.calls if call in READ_OPERATIONS]


def test_emit_writes_the_serialized_item():
    """The put carries the same item the serializer builds — no second shaping step."""
    sink, table = _sink()
    event = _event()

    sink.emit(event)

    assert table.put_items[0] == usage_event_to_item(event)


def test_emit_uses_no_condition_expression():
    """The append is unconditional: the SK is unique, so no read-modify-write is needed."""
    sink, table = _sink()

    sink.emit(_event())

    # put_item accepts only Item — a ConditionExpression kwarg would be a TypeError, and a
    # conditional put would also require a read the grant does not permit.
    assert set(table.put_items[0]) >= {"PK", "SK"}


def test_emit_propagates_sink_failure_to_the_choke_point():
    """The never-raises guarantee (Req 8.6) belongs to the emitter, not the adapter.

    An adapter that swallowed the error would make a telemetry outage invisible in the logs as
    well as harmless.
    """
    sink, _table = _sink()

    class FailingTable:
        def put_item(self, **_kwargs: Any) -> None:
            raise RuntimeError("throttled")

    sink._table = FailingTable()

    with pytest.raises(RuntimeError):
        sink.emit(_event())


# ================================================================================================
# Port conformance, table-name resolution, and the 8.4 boundary
# ================================================================================================


def test_sink_implements_usage_sink_port():
    sink, _table = _sink()
    assert isinstance(sink, UsageSinkPort)


def test_table_name_comes_from_env_with_deployed_fallback(monkeypatch):
    """Name from USAGE_EVENTS_TABLE_NAME, falling back to the deployed literal (steering §2)."""
    monkeypatch.setenv(USAGE_EVENTS_TABLE_NAME_ENV, "cm-usage-events-override")
    assert DynamoUsageEventsSink().table_name == "cm-usage-events-override"

    monkeypatch.delenv(USAGE_EVENTS_TABLE_NAME_ENV, raising=False)
    assert DynamoUsageEventsSink().table_name == DEFAULT_USAGE_EVENTS_TABLE_NAME


def test_test_writes_no_event_and_performs_no_read():
    """test() must emit nothing (#35 Req 6.5/7.3) and cannot describe the table (no grant)."""
    sink, table = _sink()

    result = sink.test()

    assert result.ok is True
    assert table.calls == []
    assert table.put_items == []


def test_status_writes_no_event_and_performs_no_read():
    sink, table = _sink()

    status = sink.status()

    assert status.healthy is True
    assert table.calls == []
    assert table.put_items == []


def test_sink_never_touches_the_derived_analytics_view():
    """Req 8.4 — cm-analytics is stream-derived (#16); nothing raw is written into it from here."""
    sink, table = _sink()

    sink.emit(_event())

    assert sink.table_name == "fake-usage-events"
    item = table.put_items[0]
    # The canonical raw item carries no cm-analytics metric shape (metric_date / metric_type).
    assert "metric_date" not in item
    assert "metric_type" not in item


def test_canonical_sink_is_registered_in_the_telemetry_catalog():
    """The Registry offers the canonical raw sink, so a Connection can select it (Req 8.3)."""
    from integrations.registry import DOMAIN_TELEMETRY, Registry

    catalog = Registry().list_adapters()
    assert "dynamo_usage_events" in {entry.adapter for entry in catalog[DOMAIN_TELEMETRY]}
