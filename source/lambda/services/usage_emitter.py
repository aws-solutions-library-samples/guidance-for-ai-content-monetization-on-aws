"""The single usage-event `emit()` choke point (license-lifecycle-usage-events, GitLab #3,
tasks 9.2 / 9.3).

This module is the **one** place in the codebase a :class:`~shared.utils.models.UsageEvent` is
constructed and handed to a telemetry sink (Requirement 8.1, design.md B2). Every billable
content-access unit — Path B, Path C, CoMP discovery, standard discovery, and a partner
self-report at `/log` — flows through :func:`emit_usage_event`. (Discovery is served by
`/query`; the former separate search endpoint was consolidated into it, so there is one
discovery surface, not two.) A static guardrail
(``tests/import_guard/test_usage_event_choke_point.py``) asserts no other ``source/lambda`` module
constructs a ``UsageEvent``, so the choke point cannot be bypassed by a later edit.

What this module reuses as-is (Requirement 8.9)
----------------------------------------------
Everything except the population itself already landed with #35 as interface-and-type only, which
explicitly assigned the write path here:

* :class:`~shared.utils.models.UsageEvent` — the canonical event dataclass. Reused **unchanged**;
  this module does not redefine, subclass, or extend it (see "Carrying ``source`` and ``ttl``").
* :class:`~integrations.telemetry.port.UsageSinkPort` — the outbound port. This module depends on
  the **port**, never on a concrete sink: no ``CloudWatchUsageSink`` / ``DynamoAnalyticsUsageSink``
  import appears here. (``integrations.registry`` imports the concrete adapters because a catalog
  must name them; that is the Registry's business, not a dependency of this module's logic.)
* :class:`~integrations.registry.Registry` — active-Connection resolution per ``(domain, brand)``.

What this module adds: population + emit semantics.

Denormalization at emit (Requirement 8.2)
-----------------------------------------
``price_charged``, ``currency``, ``license_id`` and ``content_type`` are stamped onto the immutable
event **at emit time** — values-at-time-of-access. Billing and analytics therefore never join back
to the mutable ``cm-pricing-rules`` / ``cm-licenses`` rows, and a later price or licence edit cannot
retroactively rewrite what a past access cost. The caller passes the values it already has in hand
from the request it just served; this module performs **no** lookup of its own, so the choke point
adds no read to the request path.

Carrying ``source`` and ``ttl`` without touching the #35 dataclass
------------------------------------------------------------------
Two values belong to the *emit* rather than to the event's declared shape:

* ``source`` (Requirement 9.7) — ``"observed"`` for a server-observed billable unit,
  ``"self_reported"`` for a partner's own account of its usage received at ``/log``. The
  ``UsageEvent`` dataclass has **no** ``source`` field and Requirement 8.9 forbids redefining the
  model, so the marker is attached to the event *instance* as a sidecar attribute
  (:data:`SOURCE_ATTR`) and read back through :func:`event_source`.
* ``ttl`` — the epoch second at which ``cm-usage-events`` expires the raw item, derived from
  ``USAGE_EVENTS_TTL_DAYS`` (default :data:`DEFAULT_USAGE_EVENTS_TTL_DAYS`). Task 9.1 created the
  table with TTL attribute ``ttl`` and its comment states "set at emit from USAGE_EVENTS_TTL_DAYS",
  so the value is computed here and carried the same way (:data:`TTL_ATTR`, read via
  :func:`event_ttl`) for the ``cm-usage-events`` writer task 9.4 adds.

**Tradeoff, stated plainly:** a sidecar instance attribute is not part of the declared dataclass
contract, so ``dataclasses.fields()`` / ``asdict()`` do not see it — a consumer MUST use
:func:`event_source` / :func:`event_ttl` rather than assuming the field exists. The two rejected
alternatives were worse: adding the fields to the dataclass violates Requirement 8.9 (#35 owns the
model and this spec "SHALL add population and call sites only"), and overloading an existing field
(e.g. prefixing ``content_type``) would corrupt a denormalized value-at-time-of-access that billing
reads. The marker is assigned from the trusted server side — never copied out of a request body —
so a caller cannot claim its self-report was server-observed (Requirement 9.7). An unrecognized
value degrades to ``"self_reported"``, the *less*-trusted classification, so a bad marker can never
inflate billing.

Never raises to the caller (Requirement 8.6 — load-bearing)
-----------------------------------------------------------
:func:`emit_usage_event` returns ``None`` on every path. A sink failure, a missing Connection, a
malformed event, or a sink that is not a usage sink at all all degrade to a structured log. Losing
telemetry is an acceptable outcome; failing a content response because telemetry is down is not.
The only exceptions that escape are ``BaseException``-not-``Exception`` (``SystemExit``,
``KeyboardInterrupt``, ``GeneratorExit``) — interpreter/control-flow signals, never a telemetry
outage. Swallowing those would make a runtime shutdown undetectable, so the exclusion is deliberate
and narrow; every ordinary error, including exotic third-party exception types, is swallowed.

"Asynchronous and off the hot path" — precisely what that means here
-------------------------------------------------------------------
Lambda freezes the execution environment once the handler returns, so a background thread spawned
during an invocation is not guaranteed to run. This module therefore does **not** pretend to
fire-and-forget on a thread. What it actually guarantees:

1. **Fire-and-forget from the caller's perspective.** Total failure isolation: the caller gets
   ``None`` whatever happens, so emission can never turn into a failed content response.
2. **Exactly one handoff attempt, no retry, no backoff loop.** The emitter cannot amplify a slow
   sink into unbounded request latency; worst case is one sink call.
3. **No work of its own on the request path.** No DynamoDB read, no ``cm-licenses`` /
   ``cm-pricing-rules`` join, no network call outside the single sink handoff. The denormalized
   values arrive as arguments.
4. **Called after the response payload is assembled**, so no emit work sits between the data read
   and the response. That ordering is a call-site property, enforced by tasks 10.1–10.4.
5. **The durable async boundary is the stream, not a thread.** The raw append into
   ``cm-usage-events`` is a single unconditional ``PutItem`` (no read-modify-write); derivation into
   ``cm-analytics`` happens off-invocation from that table's DynamoDB stream and is owned by #16.

Honest limit: the sink handoff itself is an in-process call, so a sink that blocks still blocks for
the duration of that one call. Bounding it belongs to the sink/transport (client timeouts), not to
a thread this runtime cannot keep alive.

Explicitly out of scope
-----------------------
* **WAF (Requirement 8.5).** This module takes no WAF input and has no WAF-gated branch. Whether a
  request passed a WAF rule is irrelevant to whether it is metered. There is no parameter, env var,
  or code path here by which a WAF outcome can change emission.
* **A second counter (Requirement 8.8).** This is the only billable-event source across #3/#16/#32.
  The existing ``analytics_collector.record_search_query()`` / ``record_api_access()`` calls stay
  where they are as *operational* metrics and are not routed through here (that call-site decision
  is task 10.3's).
* **Call sites.** No caller is wired here — tasks 10.1–10.4 own that. The ``304`` billing exemption
  stays a control-flow guarantee at the call site (the early return in
  ``services/content_service.py``), deliberately **not** a conditional inside this module, so it
  cannot be defeated by a later edit here.

Absolute imports rooted at the Canonical_Import_Root ``source/lambda/``, declared at module top
level: no ``sys.path`` manipulation and no ``try/except ImportError`` shim.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from integrations.registry import DOMAIN_TELEMETRY, Registry
from integrations.telemetry.port import UsageSinkPort
from shared.utils.models import UsageEvent

from services.connections_store import ConnectionsStore

logger = logging.getLogger(__name__)

#: The integration domain this choke point resolves its sink from.
TELEMETRY_DOMAIN = DOMAIN_TELEMETRY

# ── `source` marker (Requirement 9.7) ────────────────────────────────────────────────────────
#
# A server-observed billable unit vs. a partner's own account of its usage. Assigned by the
# server from the authenticated plane, never read from a request body.

#: A billable unit the server observed while serving the access.
SOURCE_OBSERVED = "observed"

#: A unit the partner reported about itself at ``POST /log``. Matches the literal
#: ``partner_api/handler.py`` already uses (``LOG_SOURCE_SELF_REPORTED``) and the
#: ``LogResponse.source`` single-value enum in ``openapi-spec.yaml``.
SOURCE_SELF_REPORTED = "self_reported"

#: The only recognized markers. Anything else degrades to :data:`SOURCE_SELF_REPORTED` (the
#: less-trusted classification), so an unrecognized marker cannot inflate billing.
VALID_SOURCES: frozenset = frozenset({SOURCE_OBSERVED, SOURCE_SELF_REPORTED})

#: Sidecar attribute name carrying the ``source`` marker on the event instance (see the module
#: docstring — the #35 ``UsageEvent`` dataclass is reused unchanged, Requirement 8.9).
SOURCE_ATTR = "source"

#: Sidecar attribute name carrying the ``cm-usage-events`` TTL epoch on the event instance. The
#: table's TTL attribute is named ``ttl`` (task 9.1), which is also the attribute name the legacy
#: ``iab-access-logs`` writer uses, so the task 9.4 fold keeps one name end to end.
TTL_ATTR = "ttl"

# ── TTL (task 9.1's contract: "set at emit from USAGE_EVENTS_TTL_DAYS") ──────────────────────

#: Env var the CDK wires with the raw-event retention window.
USAGE_EVENTS_TTL_DAYS_ENV = "USAGE_EVENTS_TTL_DAYS"

#: Fallback retention when the env var is absent or unusable. 90 days matches the CDK default and
#: the retention ``access_logger.LOG_TTL_DAYS`` already applies, so the task 9.4 fold of
#: ``iab-access-logs`` into ``cm-usage-events`` does not change retention.
DEFAULT_USAGE_EVENTS_TTL_DAYS = 90

#: Default currency when a caller has none to stamp.
DEFAULT_CURRENCY = "USD"


# ─────────────────────────────────────────────────────────────────────────────
# Sidecar accessors (the documented way to read what emit stamped)
# ─────────────────────────────────────────────────────────────────────────────


def event_source(event: UsageEvent) -> str:
    """Return the ``source`` marker the choke point stamped on ``event``.

    Falls back to :data:`SOURCE_OBSERVED` for an event that did not come through
    :func:`emit_usage_event` (nothing in ``source/lambda`` should construct one, but a test fixture
    legitimately may). Use this rather than ``getattr(event, "source")`` so the sidecar mechanism
    stays a single, documented seam.
    """
    value = getattr(event, SOURCE_ATTR, SOURCE_OBSERVED)
    return value if value in VALID_SOURCES else SOURCE_SELF_REPORTED


def event_ttl(event: UsageEvent) -> Optional[int]:
    """Return the ``cm-usage-events`` TTL epoch second stamped on ``event``, or ``None``.

    ``None`` means the event did not come through the choke point, so the caller must decide its
    own retention rather than silently persisting a raw event with no expiry.
    """
    value = getattr(event, TTL_ATTR, None)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


# ─────────────────────────────────────────────────────────────────────────────
# Internals — normalization, TTL, sink resolution
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """The access instant, stamped at emit (ISO 8601, UTC)."""
    return datetime.now(timezone.utc).isoformat()


def _ttl_days() -> int:
    """Read the retention window from the environment, falling back to the default.

    A missing, non-numeric, or non-positive value degrades to
    :data:`DEFAULT_USAGE_EVENTS_TTL_DAYS` with a warning — a misconfigured env var must not make
    raw events immortal, nor expire them immediately.
    """
    raw = os.environ.get(USAGE_EVENTS_TTL_DAYS_ENV)
    if raw is None:
        return DEFAULT_USAGE_EVENTS_TTL_DAYS
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "usage_emitter: %s=%r is not an integer; using default %s days",
            USAGE_EVENTS_TTL_DAYS_ENV,
            raw,
            DEFAULT_USAGE_EVENTS_TTL_DAYS,
        )
        return DEFAULT_USAGE_EVENTS_TTL_DAYS
    if days <= 0:
        logger.warning(
            "usage_emitter: %s=%r is not positive; using default %s days",
            USAGE_EVENTS_TTL_DAYS_ENV,
            raw,
            DEFAULT_USAGE_EVENTS_TTL_DAYS,
        )
        return DEFAULT_USAGE_EVENTS_TTL_DAYS
    return days


def _ttl_epoch(now: Optional[datetime] = None) -> int:
    """Return the epoch second at which ``cm-usage-events`` should expire this raw event."""
    base = now or datetime.now(timezone.utc)
    return int((base + timedelta(days=_ttl_days())).timestamp())


def _normalize_source(source: Any) -> str:
    """Coerce a ``source`` argument to a recognized marker, failing towards *less* trust.

    An unrecognized value becomes :data:`SOURCE_SELF_REPORTED` rather than
    :data:`SOURCE_OBSERVED`, so a caller bug can only ever *under*-trust a unit — it can never
    promote an unverified claim into a server-observed billable unit (Requirement 9.7).
    """
    if source in VALID_SOURCES:
        return str(source)
    logger.warning(
        "usage_emitter: unrecognized source=%r; recording as %s (never as %s)",
        source,
        SOURCE_SELF_REPORTED,
        SOURCE_OBSERVED,
    )
    return SOURCE_SELF_REPORTED


def _normalize_declared_use(declared_use: Any) -> Optional[int]:
    """Coerce the declared CoMP function code to an ``int``, or ``None``.

    ``bool`` is rejected despite being an ``int`` subclass — ``True`` is not CoMP function code 1.
    A non-integer declaration is dropped with a warning rather than guessed at: the event stays
    emittable (billing is not lost) but records no false claim about what was declared.
    """
    if declared_use is None:
        return None
    if isinstance(declared_use, bool) or not isinstance(declared_use, int):
        logger.warning("usage_emitter: dropping non-integer declared_use=%r", declared_use)
        return None
    return int(declared_use)


def _normalize_price(price_charged: Any) -> float:
    """Coerce the charged price to a ``float``, defaulting to ``0.0``.

    An unusable value becomes ``0.0`` with a warning: a free access is the safe reading, and the
    alternative — raising — would fail a content response over a telemetry detail (Requirement 8.6).
    """
    if price_charged is None:
        return 0.0
    try:
        return float(price_charged)
    except (TypeError, ValueError):
        logger.warning("usage_emitter: price_charged=%r is not numeric; recording 0.0", price_charged)
        return 0.0


#: Process-wide Registry used when a caller injects none. Built on first use so importing this
#: module performs no AWS work. It caches connection-resolution plumbing, never an event.
_DEFAULT_REGISTRY: Optional[Registry] = None


def _default_registry() -> Registry:
    """Return the shared :class:`Registry`, wired to the real ``cm-connections`` store.

    ``Registry.resolve`` needs a ``ConnectionStore``; a bare ``Registry()`` has nothing to read and
    always resolves to ``None`` (not wired). The store is constructed here rather than at import
    time so importing this module touches no AWS.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = Registry(store=ConnectionsStore())
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """Drop the shared Registry so the next emit rebuilds it (test/teardown helper)."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def _resolve_sink(brand: Optional[str], registry: Optional[Registry]) -> Optional[UsageSinkPort]:
    """Resolve the active telemetry :class:`UsageSinkPort` for ``brand``, or ``None``.

    Returns ``None`` — never raises — when:

    * no active telemetry Connection exists for ``(telemetry, brand)``. Per #35's semantics an
      adapter with no active Connection is **simply not wired**; the caller's request is unaffected
      and the event is reported in a structured log instead of being silently dropped.
    * the ``cm-connections`` store or the Secrets Manager reference cannot be read (the Registry
      already fails closed on an unreadable secret), or the store itself cannot be constructed.
    * the resolved object is not a usage sink — a misconfigured Connection pointing the telemetry
      domain at some other adapter. Checked structurally against the runtime-checkable port, which
      is why this module depends on ``UsageSinkPort`` and on no concrete sink.
    """
    try:
        resolver = registry if registry is not None else _default_registry()
        sink = resolver.resolve(TELEMETRY_DOMAIN, brand)
    except Exception:  # noqa: BLE001 — resolution must never fail the caller (Req 8.6)
        logger.exception("usage_emitter: telemetry Connection resolution failed brand=%s", brand)
        return None

    if sink is None:
        return None

    if not isinstance(sink, UsageSinkPort):
        logger.warning(
            "usage_emitter: active telemetry Connection for brand=%s resolved %s, which is not a "
            "UsageSinkPort; treating as not wired",
            brand,
            type(sink).__name__,
        )
        return None

    return sink


def _log_event(level: int, message: str, event: UsageEvent, **extra: Any) -> None:
    """Emit one structured log line describing a usage event.

    Field-per-key rather than a prose blob so CloudWatch Logs Insights can query it. Carries no
    credential material: identifiers only (``partner_id`` / ``content_id`` / ``license_id``), never
    a partner key, licence key, or payment signature.
    """
    fields = {
        "event": "usage_event",
        "source": event_source(event),
        "partner_id": event.partner_id,
        "content_id": event.content_id,
        "content_type": event.content_type,
        "license_id": event.license_id,
        "price_charged": event.price_charged,
        "currency": event.currency,
        "declared_use": event.declared_use,
        "request_id": event.request_id,
        "accessed_at": event.accessed_at,
        "ttl": event_ttl(event),
        **extra,
    }
    logger.log(level, "usage_emitter: %s | %s", message, fields)


# ─────────────────────────────────────────────────────────────────────────────
# The choke point
# ─────────────────────────────────────────────────────────────────────────────


def emit_usage_event(
    *,
    partner_id: str,
    content_id: str,
    content_type: str = "",
    license_id: str = "",
    price_charged: Any = 0.0,
    currency: str = DEFAULT_CURRENCY,
    declared_use: Any = None,
    request_id: Optional[str] = None,
    source: str = SOURCE_OBSERVED,
    brand: Optional[str] = None,
    registry: Optional[Registry] = None,
    sink: Optional[UsageSinkPort] = None,
) -> None:
    """Record exactly one billable usage event. The ONE choke point. Never raises.

    Builds the canonical :class:`~shared.utils.models.UsageEvent` with the four denormalized
    values-at-time-of-access stamped **at emit** (Requirement 8.2), attaches the ``source`` marker
    and the ``cm-usage-events`` TTL, and hands the event to the :class:`UsageSinkPort` resolved for
    ``(telemetry, brand)``.

    :param partner_id: the authenticated partner the unit is billable to. Required — an event with
      no partner is unbillable, so a missing value degrades to a structured log and no emit.
    :param content_id: the content accessed. Required, same reasoning.
    :param content_type: **denormalized at emit** — what kind of access this was (e.g.
      ``url_retrieval``, ``comp_query``, ``search``).
    :param license_id: **denormalized at emit** — the licence that authorized this access, as
      validated for *this* request. Billing never re-reads ``cm-licenses`` to learn it.
    :param price_charged: **denormalized at emit** — what this access actually cost, including
      Path C's per-access charge. Billing never re-reads ``cm-pricing-rules`` to learn it.
    :param currency: **denormalized at emit** — the currency ``price_charged`` is expressed in.
    :param declared_use: the CoMP function code the caller declared (``0``–``5``), when the access
      came through a CoMP request.
    :param request_id: the API Gateway request id, for tracing one event back to one invocation.
    :param source: :data:`SOURCE_OBSERVED` (default) for a server-observed unit, or
      :data:`SOURCE_SELF_REPORTED` for a partner self-report received at ``/log``. Assign this from
      the authenticated plane — never from a request body.
    :param brand: tenant/brand selecting among multiple active telemetry Connections; ``None``
      selects the single-brand / brand-agnostic Connection.
    :param registry: Registry override (tests, or a caller isolating connection resolution).
    :param sink: sink override, bypassing resolution. Accepts anything satisfying the **port**;
      no concrete sink is imported or referenced by this module.
    :returns: ``None``, always — on success, on sink failure, on a missing Connection, and on a
      malformed event alike (Requirement 8.6).

    Takes no WAF input and branches on no WAF outcome (Requirement 8.5), and adds no second
    counter (Requirement 8.8). The ``304`` exemption is a call-site control-flow guarantee, not a
    condition here.
    """
    try:
        event = UsageEvent(
            content_id=str(content_id or ""),
            partner_id=str(partner_id or ""),
            accessed_at=_now_iso(),
            # ── the four denormalized values-at-time-of-access (Req 8.2) ──
            price_charged=_normalize_price(price_charged),
            currency=str(currency or DEFAULT_CURRENCY),
            license_id=str(license_id or ""),
            content_type=str(content_type or ""),
            # ── access context ──
            request_id=request_id,
            declared_use=_normalize_declared_use(declared_use),
        )
        # Sidecar values that belong to the emit, not to the #35 dataclass (see module docstring).
        setattr(event, SOURCE_ATTR, _normalize_source(source))
        setattr(event, TTL_ATTR, _ttl_epoch())

        if not event.partner_id or not event.content_id:
            _log_event(
                logging.WARNING,
                "not emitted: partner_id and content_id are both required",
                event,
                emitted=False,
                reason="incomplete_event",
            )
            return None

        resolved = sink if sink is not None else _resolve_sink(brand, registry)
        if resolved is None:
            # #35 not-wired semantics: no active telemetry Connection is not an error. The event
            # is reported in a structured log so a billable unit is never dropped unrecorded.
            _log_event(
                logging.INFO,
                "no active telemetry Connection — not wired",
                event,
                emitted=False,
                reason="telemetry_not_wired",
                brand=brand,
            )
            return None

        try:
            resolved.emit(event)
        except Exception:  # noqa: BLE001 — a telemetry outage must never fail the response
            # One attempt, no retry: the emitter cannot amplify a slow sink into request latency.
            logger.exception(
                "usage_emitter: telemetry sink %s failed; content response unaffected",
                type(resolved).__name__,
            )
            _log_event(
                logging.ERROR,
                "sink emit failed",
                event,
                emitted=False,
                reason="sink_error",
                sink=type(resolved).__name__,
            )
            return None

        _log_event(logging.INFO, "emitted", event, emitted=True, sink=type(resolved).__name__)
        return None

    except Exception:  # noqa: BLE001 — the outermost guarantee: emit() never raises (Req 8.6)
        # Reached only if building or logging the event itself fails (e.g. an argument whose repr
        # raises). Kept deliberately minimal so this handler cannot fail in turn.
        logger.exception("usage_emitter: usage event could not be recorded; response unaffected")
        return None


__all__ = [
    "DEFAULT_CURRENCY",
    "DEFAULT_USAGE_EVENTS_TTL_DAYS",
    "SOURCE_ATTR",
    "SOURCE_OBSERVED",
    "SOURCE_SELF_REPORTED",
    "TELEMETRY_DOMAIN",
    "TTL_ATTR",
    "USAGE_EVENTS_TTL_DAYS_ENV",
    "VALID_SOURCES",
    "emit_usage_event",
    "event_source",
    "event_ttl",
    "reset_default_registry",
]
