"""Canonical raw usage-event sink — ``cm-usage-events`` (license-lifecycle-usage-events,
GitLab #3, task 9.4, Requirements 8.3, 8.4).

This is the adapter that completes the ``iab-access-logs`` -> ``cm-usage-events`` **fold**: the
:class:`~integrations.telemetry.port.UsageSinkPort` adapter that appends the canonical
:class:`~shared.utils.models.UsageEvent` to the one raw store, written **exactly once**
(Requirement 8.3). ``cm-analytics`` stays a *derived*, stream-fed view owned by #16 — nothing raw
is written into it from here (Requirement 8.4).

Why a NEW adapter rather than repointing ``CloudWatchUsageSink``
---------------------------------------------------------------
``telemetry/cloudwatch.py`` is where the legacy raw write lives today: its ``emit()`` delegates to
``access_logger.log_access_attempt(...)``, which puts a row into ``iab-access-logs`` *and*
publishes the ``IAB/AccessLogger`` CloudWatch metrics. Repointing that ``emit()`` at
``cm-usage-events`` was considered and **rejected**:

* #35 documents that adapter as a *thin delegation* whose wrapped module is "neither relocated nor
  rewritten", and ``refactoring-safety.md`` forbids rewriting a working module without explicit
  authorization. Changing its ``emit()`` target is exactly that rewrite, and it would break the
  pre-existing, passing
  ``tests/integration_adapters/test_adapter_wrapping_delegation.py::test_cloudwatch_sink_emit_delegates_to_access_logger``
  — a test whose failure would mean *this* change is wrong, not that the test is stale.
* The two concerns are genuinely different. The CloudWatch **metric** publish is operational
  observability (``AccessAttempts`` / ``SuccessfulAccess`` / ``Revenue`` gauges, dashboards,
  alarms); it is not raw billable truth and Requirement 8.3 does not forbid it. What 8.3 forbids
  is a *parallel raw store*. So the metric sink survives independently and only the **raw** write
  is re-homed here.
* ``access_logger`` and ``iab-access-logs`` still have live non-choke-point consumers (the deployed
  ``iab-access-logger`` Lambda, ``services/analytics_service.get_access_logs`` behind
  ``/analytics/access-logs``, which filters by ``endpoint`` — a dimension this table's key schema
  deliberately does not index). Gutting the writer would silently break that read. Those modules
  are left working and carry deprecation notes instead (``refactoring-safety.md``: mark as
  deprecated, do not delete).

"Exactly once" is structural, not conventional
----------------------------------------------
:meth:`Registry.resolve` returns **one** active Connection per ``(domain, brand)``. Since
``services/usage_emitter.py`` hands each event to exactly one resolved sink, at most one raw write
happens per emitted event no matter which telemetry adapter is configured. This adapter makes the
*canonical* choice available; the legacy ``cloudwatch`` / ``dynamo_analytics`` entries stay in the
catalog as non-canonical, deprecated-for-raw-truth options (see their module docstrings).

Item shape — task 9.1's contract verbatim
-----------------------------------------
``PK = PARTNER#<partner_id>#<YYYY-MM-DD>`` and ``SK = <accessed_at>#<request_id>``. The day bucket
bounds partition growth and spreads the append hot spot; the SK is unique per event (ISO-8601
instant plus request id), so the append is a single **unconditional** ``put_item`` that never
collides and never needs a read-modify-write.

The ``UsageEvent`` fields are ALSO stored as plain top-level attributes, not only folded into the
composite keys, so an event stays readable without parsing ``PK``/``SK`` — task 9.1's comment
states this explicitly, and it is what lets #16's stream consumer aggregate ``price_charged`` /
``currency`` / ``content_type`` straight out of the ``NEW_IMAGE`` record.

Two values ride as **sidecar instance attributes** on the event rather than as dataclass fields
(#35 owns the model and Requirement 8.9 forbids redefining it), so they are read through the
documented accessors, never with a bare ``getattr``:

* ``source`` via :func:`services.usage_emitter.event_source` — the server-observed vs.
  partner-self-reported marker (Requirement 9.7), persisted so a self-report can never be
  mistaken for a server-observed billable unit downstream.
* ``ttl`` via :func:`services.usage_emitter.event_ttl` — the epoch second the table expires the
  raw item. The attribute is named ``ttl`` because that is both the TTL attribute task 9.1
  configured and the name the legacy ``access_logger`` writer already used, so the fold keeps one
  name end to end. ``None`` (an event that did not come through the choke point) is written
  **without** a ``ttl`` attribute rather than with a guessed one — silently inventing a retention
  window for an event of unknown provenance is worse than leaving it un-expired and visible.

Append-only — anything else fails at runtime
--------------------------------------------
The task 9.1 IAM grant is ``dynamodb:PutItem`` + ``dynamodb:BatchWriteItem`` and **nothing else**:
no ``GetItem``, ``Query``, ``Scan``, ``UpdateItem``, ``DeleteItem`` — and no ``DescribeTable``.
Code here that needs a read does not merely violate the design, it is denied in the deployed
environment. Two consequences:

* :meth:`DynamoUsageEventsSink.emit` is exactly one ``put_item`` and no read.
* :meth:`DynamoUsageEventsSink.test` / :meth:`DynamoUsageEventsSink.status` **cannot** probe the
  table the way the sibling telemetry adapters do (``Table.load()`` describe, ``list_metrics``).
  A ``Table.load()`` here would report a *false negative* — ``AccessDenied`` on a perfectly
  healthy append-only sink. They therefore report a truthful, fail-informative result based on
  what is actually verifiable without a read (that the table name resolved), and say plainly that
  reachability is unverifiable under an append-only grant. Emitting a probe event to prove
  reachability is not an option: ``test()`` must write no event (#35 Requirements 6.5, 7.3), and a
  synthetic event in the raw billable store would be fabricated billing data.

Adapter-owned client scope: ``dynamodb`` is general ``boto3``, not one of the four adapter-owned
clients (``wafv2`` / ``marketplaceentitlementservice`` / ``meteringmarketplace`` /
``marketplace-catalog``) the ``tests/import_guard/test_adapter_owned_clients.py`` guardrail scopes.
It is nonetheless constructed **only inside this adapter** and lazily, so importing this module
performs no AWS work.

Absolute imports rooted at the Canonical_Import_Root ``source/lambda/``; no ``sys.path``
manipulation and no ``try/except ImportError`` shim. The two sidecar accessors are read through a
deliberate **function-level** import (:func:`_sidecar_accessors`) — see that function for why: a
top-level import would close a real cycle (registry -> this adapter -> usage_emitter -> registry)
and would also point an ``integrations/`` -> ``services/`` dependency the wrong way.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from shared.utils.models import HealthStatus, TestResult, UsageEvent

logger = logging.getLogger(__name__)


def _sidecar_accessors() -> tuple:
    """Return the choke point's ``(event_source, event_ttl)`` sidecar accessors.

    Imported at **call** time rather than at module top, deliberately, for two reasons:

    1. **It would be a genuine import cycle.** ``integrations/registry.py`` imports this adapter to
       build its catalog, and ``services/usage_emitter.py`` imports ``Registry`` to resolve a sink.
       A top-level ``from services.usage_emitter import ...`` here closes the loop
       (registry -> this adapter -> usage_emitter -> registry) and fails at import with a partially
       initialized module.
    2. **It keeps the layering honest.** ``services/`` is the core and ``integrations/`` the outer
       ring; an adapter holding a module-level dependency on a service module points the arrow the
       wrong way. Reading the two sidecars at the moment they are needed keeps this file's
       import-time surface to ``boto3`` + the shared models.

    The alternative — reimplementing ``getattr(event, "source")`` / ``getattr(event, "ttl")`` here —
    was rejected: the sidecar mechanism is documented as a *single* seam (``usage_emitter``'s module
    docstring), and a second copy of the fallback rules would be free to drift. In particular
    ``event_source`` fails towards ``self_reported`` for an unrecognized marker so a bad value can
    never inflate billing; a local reimplementation could silently lose that.

    This is the same deliberate, commented function-level import ``registry._default_secret_resolver``
    already uses. Still an absolute import rooted at the Canonical_Import_Root ``source/lambda/`` —
    no ``sys.path`` manipulation and no ``try/except ImportError`` shim.
    """
    from services.usage_emitter import event_source, event_ttl

    return event_source, event_ttl

#: Env var the CDK wires with the raw-event table name (task 9.1).
USAGE_EVENTS_TABLE_NAME_ENV = "USAGE_EVENTS_TABLE_NAME"

#: In-code fallback, matching the deployed table name exactly (task 9.1 / steering §2).
DEFAULT_USAGE_EVENTS_TABLE_NAME = "cm-usage-events"

#: ``PK`` prefix — the partner-and-day bucket (``PARTNER#<partner_id>#<YYYY-MM-DD>``).
PARTNER_PK_PREFIX = "PARTNER#"

#: Separator joining the two ``SK`` components (``<accessed_at>#<request_id>``).
KEY_SEPARATOR = "#"


def _day_bucket(accessed_at: str) -> str:
    """Return the ``YYYY-MM-DD`` day bucket for an ISO-8601 ``accessed_at``.

    The bucket is taken from the event's own access instant so the item lands in the partition the
    billing run queries for that day, rather than in "the day the write happened" — the two differ
    for an event emitted either side of midnight UTC.

    An unparseable or empty ``accessed_at`` falls back to the current UTC day: a raw billable event
    is never dropped over a malformed timestamp, and a wrong-bucket event is still recoverable from
    its top-level ``accessed_at`` attribute, whereas an unwritten event is gone.
    """
    candidate = str(accessed_at or "")[:10]
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        fallback = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logger.warning(
            "dynamo_usage_events: accessed_at=%r has no parseable YYYY-MM-DD day; bucketing under %s",
            accessed_at,
            fallback,
        )
        return fallback
    return candidate


def usage_event_pk(partner_id: str, accessed_at: str) -> str:
    """Return the partition key: ``PARTNER#<partner_id>#<YYYY-MM-DD>`` (task 9.1)."""
    return f"{PARTNER_PK_PREFIX}{partner_id}{KEY_SEPARATOR}{_day_bucket(accessed_at)}"


def usage_event_sk(accessed_at: str, request_id: Optional[str]) -> str:
    """Return the sort key: ``<accessed_at>#<request_id>`` (task 9.1).

    ``request_id`` is optional on the :class:`UsageEvent`, but the SK's uniqueness is what makes
    the append collision-free without a read. When it is absent a random UUID4 suffix stands in, so
    two events stamped at the same instant for the same partner cannot overwrite one another. The
    substitute appears only in the key; the top-level ``request_id`` attribute stays absent, so a
    reader can tell a real request id from a generated uniqueness token.
    """
    suffix = str(request_id) if request_id else uuid.uuid4().hex
    return f"{accessed_at}{KEY_SEPARATOR}{suffix}"


def usage_event_to_item(event: UsageEvent) -> Dict[str, Any]:
    """Serialize a :class:`UsageEvent` to its ``cm-usage-events`` item.

    The composite keys are derived from the event's own ``partner_id`` / ``accessed_at`` /
    ``request_id`` — never caller-supplied — so an item cannot be written under a key that
    disagrees with its contents. Every ``UsageEvent`` field is additionally written as a plain
    top-level attribute (task 9.1: events stay readable without parsing composite keys), and the
    two sidecar values (``source``, ``ttl``) are read through the choke point's documented
    accessors.

    ``price_charged`` is converted to :class:`~decimal.Decimal` because the DynamoDB resource
    rejects ``float``; ``Decimal(str(...))`` is used rather than ``Decimal(float)`` so the stored
    amount is the decimal the caller meant and not a binary-float artifact — this is money.

    ``None``-valued optional fields are omitted rather than persisted as DynamoDB NULLs, matching
    the ``shared/utils/models.py`` and ``entitlements_store`` convention.
    """
    accessed_at = str(event.accessed_at or "")
    event_source, event_ttl = _sidecar_accessors()

    item: Dict[str, Any] = {
        "PK": usage_event_pk(event.partner_id, accessed_at),
        "SK": usage_event_sk(accessed_at, event.request_id),
        # ── UsageEvent fields as plain top-level attributes (task 9.1) ──
        "partner_id": event.partner_id,
        "content_id": event.content_id,
        "accessed_at": accessed_at,
        # The four denormalized values-at-time-of-access (Requirement 8.2).
        "price_charged": Decimal(str(event.price_charged or 0)),
        "currency": event.currency,
        "license_id": event.license_id,
        "content_type": event.content_type,
        # ── sidecar marker stamped by the choke point (Requirement 9.7) ──
        "source": event_source(event),
    }

    if event.request_id is not None:
        item["request_id"] = event.request_id
    if event.declared_use is not None:
        item["declared_use"] = int(event.declared_use)

    ttl = event_ttl(event)
    if ttl is not None:
        item["ttl"] = ttl

    return item


class DynamoUsageEventsSink:
    """``UsageSinkPort`` adapter appending raw usage events to ``cm-usage-events``.

    The canonical raw-truth sink (Requirement 8.3). ``emit()`` is a single append; there is no
    read path on this adapter at all, matching the append-only IAM grant task 9.1 created.
    """

    def __init__(self, table_name: Optional[str] = None, region_name: Optional[str] = None) -> None:
        """Wire the adapter to the raw usage-event table.

        :param table_name: override the table name; defaults to the
            ``USAGE_EVENTS_TABLE_NAME`` env var, then ``cm-usage-events``. Referenced **by name**
            — no cross-stack CloudFormation export (steering §2).
        :param region_name: optional explicit region; defaults to the ambient boto3 region.
        """
        self._table_name = table_name or os.environ.get(
            USAGE_EVENTS_TABLE_NAME_ENV, DEFAULT_USAGE_EVENTS_TABLE_NAME
        )
        self._region_name = region_name
        self._table: Any = None

    @property
    def table_name(self) -> str:
        """The resolved raw usage-event table name this sink appends to."""
        return self._table_name

    def _get_table(self) -> Any:
        """Return the DynamoDB table resource, created lazily and cached on the instance.

        Lazy so importing/constructing the adapter (e.g. building the Registry catalog) performs
        no AWS work.
        """
        if self._table is None:
            if self._region_name:
                dynamodb = boto3.resource("dynamodb", region_name=self._region_name)
            else:
                dynamodb = boto3.resource("dynamodb")
            self._table = dynamodb.Table(self._table_name)
        return self._table

    def emit(self, event: UsageEvent) -> None:
        """Append one raw usage event to ``cm-usage-events`` — exactly one ``put_item``.

        Unconditional: no ``ConditionExpression``, because the SK is unique per event, and no
        read-back, because the grant permits none. Failures are raised to the caller; the
        never-raises guarantee (Requirement 8.6) belongs to the choke point
        (``services/usage_emitter.py``), which catches sink exceptions — an adapter that swallowed
        them would make a telemetry outage invisible in the logs as well as harmless.
        """
        self._get_table().put_item(Item=usage_event_to_item(event))

    def test(self) -> TestResult:
        """Connectivity/auth check that emits no event — and performs no read.

        Unlike the sibling telemetry adapters, this sink **cannot** describe its backing table:
        the append-only grant (task 9.1) excludes ``DescribeTable``, so a ``Table.load()`` probe
        would return ``AccessDenied`` for a perfectly healthy sink — a false negative worse than
        no probe. Writing a synthetic event to prove reachability is also ruled out: ``test()``
        must emit nothing (#35 Requirements 6.5, 7.3), and a fabricated row in the raw billable
        store is fabricated billing data.

        So this reports exactly what is verifiable without a read — that the table name resolved —
        and says plainly that reachability is unverified. Fail-informative, never fail-silent.
        """
        if not self._table_name:
            return TestResult(ok=False, message="No usage-event table name resolved")
        return TestResult(
            ok=True,
            message=(
                f"Append-only raw usage-event sink configured: {self._table_name} "
                "(reachability not probed — the append-only grant permits no read/describe)"
            ),
        )

    def status(self) -> HealthStatus:
        """Ongoing health of the active telemetry Connection — see :meth:`test` for why no probe."""
        if not self._table_name:
            return HealthStatus(healthy=False, detail="No usage-event table name resolved")
        return HealthStatus(
            healthy=True,
            detail=(
                f"Append-only raw usage-event sink configured: {self._table_name} "
                "(reachability not probed — the append-only grant permits no read/describe)"
            ),
        )


__all__ = [
    "DEFAULT_USAGE_EVENTS_TABLE_NAME",
    "KEY_SEPARATOR",
    "PARTNER_PK_PREFIX",
    "USAGE_EVENTS_TABLE_NAME_ENV",
    "DynamoUsageEventsSink",
    "usage_event_pk",
    "usage_event_sk",
    "usage_event_to_item",
]
