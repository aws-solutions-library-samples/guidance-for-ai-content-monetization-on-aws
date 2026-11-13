"""Call-site tests for the billable signal in ``partner_api/handler.py`` — the CoMP branches
(license-lifecycle-usage-events, GitLab #3, task 10.2 — Requirements 9.1, 9.3, 9.4, 9.5) **and** the
standard (non-CoMP) ``/query`` / ``/search`` paths (task 10.3 — Requirements 9.2, 8.1, 8.8).

**The bug these tests pin.** MR !7 added the CoMP ``/query`` branch, but that branch built the
Package and returned early, so it emitted no usage/metering event: the standard (non-CoMP) path
records ``analytics_collector.record_search_query()`` + ``record_api_access()``, the CoMP path
recorded neither, and a licensed CoMP access produced **no billable-unit signal** — the exact
metering primitive Part B exists to provide. ``/search`` was worse still: ``handle_comp_search_request``
was imported and never called, so a CoMP payload there fell through to the standard path and 400d.

The load-bearing assertions:

* a successful CoMP ``/query`` emits **exactly one** event carrying the validated ``license_id`` and
  the declared CoMP function code (Requirements 9.1, 9.5);
* a **failed license validation** emits nothing (Requirement 9.3);
* the **``PENDING_REVIEW``** branch emits nothing (Requirement 9.4) — the trap, because it comes back
  as a *successful* ``comp_response`` with ``error_msg is None``, so a naive "emit after success"
  would bill the caller for being told to go negotiate a licence;
* CoMP ``/search`` emits with its own ``content_type``, so the two surfaces do not collapse into one
  billable label;
* a successful **standard** ``/query`` / ``/search`` emits exactly one event through the **same**
  choke point (Requirements 9.2, 8.1), while its ``analytics_collector.record_search_query()`` /
  ``record_api_access()`` calls stay exactly as they were — operational metrics, not a second
  billable counter (Requirement 8.8). ``TestStandardPathEmitsThroughTheSameChokePoint`` is the
  inverted form of the class that pinned the opposite truth before task 10.3; see its docstring;
* a standard request that does not serve a result set — the missing-``query`` 400, a 500 — emits
  nothing.

Every non-billable exemption on every surface is a **control-flow / call-site** guarantee, exactly as
the ``304`` exemption is on the content path: on the CoMP path the ``error_msg`` return sits above the
emit and the ``_meta.license_validated`` gate sits inside ``_emit_comp_usage_event``; on the standard
path the emit is the last statement before the response, so every error exit is above it. None of them
is a condition inside ``services/usage_emitter.py``, so moving an emit above an exit, or dropping the
gate, makes these tests fail — which is the tripwire they exist to be.

The emitter contract itself (denormalization at emit, never-raises, not-wired degradation) is owned by
``tests/services/test_usage_emitter.py`` / ``test_usage_emitter_contract.py`` and is not re-litigated
here. ``services/comp_query_service.py`` is untouched by task 10.2 and its byte-for-byte behavior stays
pinned by ``tests/partner_api/test_comp_query_characterization.py``.

Every first-party import is absolute and rooted at the Canonical_Import_Root ``source/lambda/``.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from shared.utils.models import HealthStatus, UsageEvent
from shared.utils.models import TestResult as PortTestResult

from partner_api.handler import (
    USAGE_CONTENT_TYPE_COMP_QUERY,
    USAGE_CONTENT_TYPE_COMP_SEARCH,
    USAGE_CONTENT_TYPE_STANDARD_QUERY,
    USAGE_CONTENT_TYPE_STANDARD_SEARCH,
    handle_query_request,
)
from services import usage_emitter
from services.usage_emitter import SOURCE_OBSERVED, event_source

PARTNER_ID = "partner-comp"
LICENSE_ID = "LIC-EMIT-001"
PACKAGE_ID = "PKG-DEADBEEF"
REQUEST_ID = "apigw-request-7"


# ─────────────────────────────────────────────────────────────────────────────
# Fakes — a sink satisfying the PORT; no concrete sink is imported here
# ─────────────────────────────────────────────────────────────────────────────


class RecordingSink:
    """A ``UsageSinkPort``-shaped sink recording every event handed to it."""

    def __init__(self) -> None:
        self.events: List[UsageEvent] = []

    def emit(self, event: UsageEvent) -> None:
        self.events.append(event)

    def test(self) -> PortTestResult:
        return PortTestResult(ok=True, message="fake")

    def status(self) -> HealthStatus:
        return HealthStatus(healthy=True, detail="fake")


class RaisingSink(RecordingSink):
    """A sink whose ``emit`` always fails — a telemetry outage."""

    def emit(self, event: UsageEvent) -> None:
        raise RuntimeError("telemetry sink unavailable")


class FakeRegistry:
    """Registry stand-in resolving the telemetry domain to ``sink``."""

    def __init__(self, sink: Optional[Any]) -> None:
        self._sink = sink

    def resolve(self, domain: str, brand: Optional[str] = None) -> Optional[object]:
        return self._sink


# ─────────────────────────────────────────────────────────────────────────────
# Request / response builders
# ─────────────────────────────────────────────────────────────────────────────


def _comp_request(*, lid: str = LICENSE_ID, functions=(2, 3), name: str = "ChatBot-9000") -> Dict[str, Any]:
    """A CoMP v1.0 AISystem request payload."""
    return {
        "aisystem": {
            "name": name,
            "aisysuse": {"lid": lid, "aiauth": 1, "function": list(functions)},
        }
    }


def _real_package(*, package_id: str = PACKAGE_ID, license_id: str = LICENSE_ID) -> Dict[str, Any]:
    """A Package as ``build_comp_package_response()`` builds it after a validated licence.

    ``_meta.license_validated: True`` is the discriminator the call site gates on; only the real
    builder sets it.
    """
    return {
        "package": {
            "id": package_id,
            "title": "Content Package for ChatBot-9000",
            "licenseurl": f"https://api.publisher.com/licenses/{license_id}",
            "reporturl": "https://api.publisher.com/log",
            "scope": {"scope": 0, "ctype": [0], "text": [{"title": "A"}, {"title": "B"}, {"title": "C"}]},
            "retrieval": {"auth": 1, "endpoint": "https://api.publisher.com/search", "type": [2, 3]},
        },
        "_meta": {
            "request_id": "req_abc",
            "comp_version": "1.0",
            "total_results": 3,
            "license_validated": True,
        },
    }


def _pending_review_package() -> Dict[str, Any]:
    """The ``PENDING_REVIEW`` Package, copied from the shape the service hand-builds.

    Success-shaped (``error_msg is None``) but grants no content access: ``retrieval.auth == 0`` and
    no ``_meta.license_validated``.
    """
    return {
        "package": {
            "id": "PKG-PENDING",
            "title": "License Required",
            "licenseurl": "https://api.publisher.com/licenses",
            "reporturl": "https://api.publisher.com/log",
            "citation": 1,
            "scope": {"scope": 0, "max": 1, "ctype": [0]},
            "retrieval": {"auth": 0, "endpoint": "https://api.publisher.com/query", "type": [2]},
        },
        "_meta": {
            "message": "Please visit licenseurl to negotiate terms before accessing content",
            "status": "pending_license",
        },
    }


def _event(**overrides: Any) -> Dict[str, Any]:
    """An API Gateway event carrying a Host header and a requestContext request id."""
    base: Dict[str, Any] = {
        "headers": {"Host": "api.publisher.com"},
        "requestContext": {"requestId": REQUEST_ID, "domainName": "api.publisher.com"},
    }
    base.update(overrides)
    return base


def _unwrap(func):
    """Strip the ``@validate_response`` / ``@require_partner_or_cognito_auth`` decorators.

    Auth is exercised by ``test_comp_query_characterization.py``; these tests are about which call
    sites emit, so the authenticated context is injected directly.
    """
    inner = func
    while hasattr(inner, "__wrapped__"):
        inner = inner.__wrapped__
    return inner


@pytest.fixture(autouse=True)
def _isolate_default_registry():
    """Never let a test fall through to the real ``cm-connections``-backed Registry."""
    usage_emitter.reset_default_registry()
    yield
    usage_emitter.reset_default_registry()


@pytest.fixture
def wired(monkeypatch) -> RecordingSink:
    """Wire the emitter's default Registry to a recording sink."""
    sink = RecordingSink()
    monkeypatch.setattr(usage_emitter, "_default_registry", lambda: FakeRegistry(sink))
    return sink


def _call_query(request_data: Dict[str, Any], *, partner_id: str = PARTNER_ID, event=None) -> Dict[str, Any]:
    return _unwrap(handle_query_request)(
        {},
        request_data,
        _event() if event is None else event,
        authenticated_user={"partner_id": partner_id},
        auth_type="partner_key",
    )


#
# NOTE — there is no `_call_search`. Discovery was consolidated onto `/query`, so the duplicated
# search-surface tests this file originally carried have been merged into their `/query`
# equivalents rather than aliased: an alias would have asserted the same endpoint twice and,
# once the markers collapsed, asserted contradictory things about it. What survives from the
# search side is the coverage that was never really about the endpoint — "exactly one event",
# "nothing on an error path", "a sink failure does not fail the response" — which the `/query`
# tests below already pin. The retained-but-unused markers are covered by
# `TestRetiredSearchMarkers`.


# ===================================================================================================
# Requirements 9.1 / 9.5 — a successful CoMP /query emits exactly one event with the right values
# ===================================================================================================
class TestSuccessfulCompQueryEmitsExactlyOnce:
    """The bug: this branch returned the Package and emitted nothing at all."""

    def test_successful_comp_query_emits_exactly_one_event(self, wired):
        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            response = _call_query(_comp_request())

        assert response["statusCode"] == 200
        assert len(wired.events) == 1, (
            "a licensed CoMP /query produced no (or more than one) billable-unit signal — "
            "Requirement 9.1 requires exactly one usage event"
        )

    def test_one_event_per_package_not_per_text_asset(self, wired):
        """A Package advertising three text assets is still ONE billable unit (Requirement 9.1)."""
        package = _real_package()
        assert len(package["package"]["scope"]["text"]) == 3, "precondition: a multi-asset Package"

        with patch("partner_api.handler.handle_comp_query_request", return_value=(package, None)):
            _call_query(_comp_request())

        assert len(wired.events) == 1
        # The Package is the billable unit, so content_id is the Package id — not an asset id (a CoMP
        # text asset carries no content_id at all).
        assert wired.events[0].content_id == PACKAGE_ID

    def test_event_carries_validated_license_id_and_declared_function_code(self, wired):
        """Requirement 9.5 — the declared function code and the validated ``license_id``."""
        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            _call_query(_comp_request(functions=(3,)))

        event = wired.events[0]
        assert event.license_id == LICENSE_ID
        assert event.declared_use == 3
        assert event.partner_id == PARTNER_ID
        assert event.content_type == USAGE_CONTENT_TYPE_COMP_QUERY == "comp_query"
        assert event.price_charged == 0.0
        assert event.currency == "USD"
        assert event.request_id == REQUEST_ID
        # Server-observed, never self-reported: a CoMP access cannot be billed as a partner's claim.
        assert event_source(event) == SOURCE_OBSERVED

    def test_response_body_is_unchanged_by_the_emit(self, wired):
        """The Package the caller receives is exactly what the service built."""
        package = _real_package()
        with patch("partner_api.handler.handle_comp_query_request", return_value=(package, None)):
            response = _call_query(_comp_request())

        body = json.loads(response["body"])
        assert body["package"]["id"] == PACKAGE_ID
        assert body["_meta"]["license_validated"] is True

    def test_multiple_declared_function_codes_collapse_to_the_lowest(self, wired):
        """``declared_use`` is one int; a multi-code declaration must still yield exactly one event."""
        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            _call_query(_comp_request(functions=(3, 2, 5)))

        assert len(wired.events) == 1
        assert wired.events[0].declared_use == 2

    def test_declared_use_is_order_independent(self, wired):
        """``[3, 2]`` and ``[2, 3]`` are the same declaration, so they stamp the same value."""
        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            _call_query(_comp_request(functions=(3, 2)))
            _call_query(_comp_request(functions=(2, 3)))

        assert [e.declared_use for e in wired.events] == [2, 2]

    def test_absent_function_list_stamps_no_declared_use(self, wired):
        """Nothing declared -> ``None``, recording no false claim (and still exactly one event)."""
        request = {"aisystem": {"name": "X", "aisysuse": {"lid": LICENSE_ID, "aiauth": 1}}}
        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            _call_query(request)

        assert len(wired.events) == 1
        assert wired.events[0].declared_use is None

    def test_sink_failure_does_not_fail_the_comp_response(self, monkeypatch):
        """Requirement 8.6 at the call site: a telemetry outage costs telemetry, not the Package."""
        monkeypatch.setattr(usage_emitter, "_default_registry", lambda: FakeRegistry(RaisingSink()))

        with patch("partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)):
            response = _call_query(_comp_request())

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["package"]["id"] == PACKAGE_ID


# ===================================================================================================
# Requirement 9.3 — a failed license validation emits nothing
# ===================================================================================================
class TestFailedLicenseValidationEmitsNothing:
    """A denied CoMP access is a 400, and a 400 is not a billable unit."""

    def test_denied_function_emits_nothing(self, wired):
        error = (
            "Requested functions [4] (Indexing) not permitted under license LIC-EMIT-001. "
            "License allows: [2, 3] (Training, Inference/RAG)"
        )
        with patch("partner_api.handler.handle_comp_query_request", return_value=(None, error)):
            response = _call_query(_comp_request(functions=(2, 4)))

        assert response["statusCode"] == 400
        assert wired.events == [], (
            "a failed CoMP license validation emitted a billable usage event — Requirement 9.3. The "
            "`error_msg` early return MUST stay above the emit call; the exemption is a control-flow "
            "guarantee, not a condition inside the emitter."
        )

    def test_unknown_license_emits_nothing(self, wired):
        with patch(
            "partner_api.handler.handle_comp_query_request",
            return_value=(None, "License LIC-NOPE not found or inactive"),
        ):
            response = _call_query(_comp_request(lid="LIC-NOPE"))

        assert response["statusCode"] == 400
        assert wired.events == []

    def test_structurally_invalid_comp_request_emits_nothing(self, wired):
        with patch(
            "partner_api.handler.handle_comp_query_request",
            return_value=(None, "aisystem.name is required"),
        ):
            response = _call_query({"aisystem": {"aisysuse": {"lid": LICENSE_ID, "aiauth": 1}}})

        assert response["statusCode"] == 400
        assert wired.events == []

# ===================================================================================================
# Requirement 9.4 — the PENDING_REVIEW branch emits nothing (the trap)
# ===================================================================================================
class TestPendingReviewEmitsNothing:
    """``PENDING_REVIEW`` returns a *successful* ``comp_response``, so "emit after success" bills it."""

    def test_pending_review_query_emits_nothing(self, wired):
        with patch(
            "partner_api.handler.handle_comp_query_request",
            return_value=(_pending_review_package(), None),
        ):
            response = _call_query(_comp_request(lid="PENDING_REVIEW", functions=()))

        assert response["statusCode"] == 200, "precondition: PENDING_REVIEW is a SUCCESS response"
        assert wired.events == [], (
            "the PENDING_REVIEW branch emitted a billable usage event — Requirement 9.4. It returns "
            "`error_msg is None` with a Package granting no content access (retrieval.auth == 0), so "
            "`error_msg` alone cannot decide billability: the call site must gate on "
            "`_meta.license_validated`."
        )

    def test_a_success_response_without_license_validated_fails_closed(self, wired):
        """The gate is positive, so any non-validated success branch emits nothing by default."""
        with patch(
            "partner_api.handler.handle_comp_query_request",
            return_value=({"package": {"id": "PKG-FUTURE"}, "_meta": {"status": "something_new"}}, None),
        ):
            response = _call_query(_comp_request())

        assert response["statusCode"] == 200
        assert wired.events == []

    def test_license_validated_false_emits_nothing(self, wired):
        """Only ``True`` bills — an explicit ``False`` is a denial, not a billable access."""
        package = _real_package()
        package["_meta"]["license_validated"] = False

        with patch("partner_api.handler.handle_comp_query_request", return_value=(package, None)):
            _call_query(_comp_request())

        assert wired.events == []


# =============================================================================================
# Retired discovery markers — kept for pre-consolidation history, emitted by nothing
# =============================================================================================
class TestRetiredSearchMarkers:
    """The search-surface markers survive as vocabulary, not as a live billing label.

    This file originally exercised two discovery surfaces, each stamping its own
    ``content_type``: ``comp_search`` beside ``comp_query``, and ``search`` beside ``query``.
    main consolidated discovery onto ``/query``, so those two markers no longer have an
    emitting surface.

    They are deliberately NOT deleted. The raw usage-event store is immutable and append-only,
    so rows already written under ``comp_search`` / ``search`` cannot be re-labelled — dropping
    the constants would leave that history uninterpretable. What must hold instead is that they
    stay DISTINCT from the live markers (so a historical row is never confused with a current
    one) and that nothing emits them today.
    """

    def test_retired_markers_are_still_defined_and_distinct(self):
        # Distinct from each other and from the markers the consolidated surface stamps.
        assert USAGE_CONTENT_TYPE_COMP_SEARCH == "comp_search"
        assert USAGE_CONTENT_TYPE_STANDARD_SEARCH == "search"
        live = {USAGE_CONTENT_TYPE_COMP_QUERY, USAGE_CONTENT_TYPE_STANDARD_QUERY}
        retired = {USAGE_CONTENT_TYPE_COMP_SEARCH, USAGE_CONTENT_TYPE_STANDARD_SEARCH}
        assert not (live & retired), (
            "a retired marker collided with a live one — a historical row would be "
            "indistinguishable from a current access"
        )

    def test_comp_discovery_stamps_the_query_marker_not_the_retired_one(self, wired):
        """The consolidated CoMP surface emits ``comp_query``; ``comp_search`` is not reachable."""
        with patch(
            "partner_api.handler.handle_comp_query_request", return_value=(_real_package(), None)
        ):
            response = _call_query(_comp_request(functions=(5,)))

        assert response["statusCode"] == 200
        assert len(wired.events) == 1
        event = wired.events[0]
        assert event.content_type == USAGE_CONTENT_TYPE_COMP_QUERY
        assert event.content_type != USAGE_CONTENT_TYPE_COMP_SEARCH, (
            "discovery is one surface on main — the retired comp_search marker must not be "
            "stamped by the consolidated endpoint"
        )

    def test_standard_discovery_stamps_the_query_marker_not_the_retired_one(self, wired):
        """The consolidated standard surface emits ``query``; ``search`` is not reachable."""
        with patch("partner_api.handler.search_service") as search_service:
            search_service.execute_discovery.return_value = []
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        assert response["statusCode"] == 200
        assert len(wired.events) == 1
        assert wired.events[0].content_type == USAGE_CONTENT_TYPE_STANDARD_QUERY
        assert wired.events[0].content_type != USAGE_CONTENT_TYPE_STANDARD_SEARCH


# ===================================================================================================
# CoMP /search emits with its own content_type
# ===================================================================================================


# ===================================================================================================
# Requirements 9.2 / 8.1 / 8.8 — the standard path routes through the SAME choke point (task 10.3)
# ===================================================================================================
class TestStandardPathEmitsThroughTheSameChokePoint:
    """Task 10.3 inverted this class.

    Until 10.3 these assertions pinned the *opposite* truth — "the standard path emits nothing
    through the choke point" — because routing it there was explicitly 10.3's job and 10.2 had to be
    shown not to have done it early. 10.3 did it, so the assertions flipped: a successful standard
    ``/query`` / ``/search`` now emits exactly one event through the **same** choke point the CoMP
    branches use (Requirements 9.2, 8.1).

    What did **not** flip, and is still pinned below: the
    ``analytics_collector.record_search_query()`` / ``record_api_access()`` calls are unchanged and
    still made exactly once each. They are *operational* metrics. Requirement 8.8 forbids a second
    billable counter, so 10.3 added a route to the one choke point rather than a parallel signal —
    which is why this class asserts both things at once, and why dropping either half is a
    regression: losing the emit loses the billable unit, and losing the collector calls (or gaining a
    duplicate) is the second counter 8.8 forbids.
    """

    def test_standard_query_emits_exactly_one_event_through_the_choke_point(self, wired):
        with patch("partner_api.handler.search_content", return_value=[]):
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        assert response["statusCode"] == 200
        assert len(wired.events) == 1, (
            "a successful standard /query produced no (or more than one) billable-unit signal — "
            "Requirement 9.2 routes it through the SAME choke point as the CoMP path"
        )

    def test_standard_query_event_carries_the_standard_query_content_type(self, wired):
        with patch("partner_api.handler.search_content", return_value=[]):
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        event = wired.events[0]
        assert event.content_type == USAGE_CONTENT_TYPE_STANDARD_QUERY == "query"
        # The four standard/CoMP surfaces must not collapse into one billable label.
        assert event.content_type not in {
            USAGE_CONTENT_TYPE_COMP_QUERY,
            USAGE_CONTENT_TYPE_COMP_SEARCH,
            USAGE_CONTENT_TYPE_STANDARD_SEARCH,
        }
        assert event.partner_id == PARTNER_ID
        assert event.request_id == REQUEST_ID
        # No licence was presented and no price was taken on this surface — both are honest blanks,
        # not placeholders, and populating either would need a read the hot path must not do.
        assert event.license_id == ""
        assert event.price_charged == 0.0
        assert event.currency == "USD"
        # Not a CoMP request, so no function code was declared and none is claimed.
        assert event.declared_use is None
        # Server-observed, never self-reported.
        assert event_source(event) == SOURCE_OBSERVED

    def test_standard_query_event_content_id_is_the_query_id_the_caller_received(self, wired):
        """One access = one event, keyed on the id the response itself hands back."""
        with patch("partner_api.handler.search_content", return_value=[]):
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        query_id = json.loads(response["body"])["query_id"]
        assert query_id, "precondition: /query mints a query_id"
        assert wired.events[0].content_id == query_id

    def test_standard_query_emits_one_event_not_one_per_result(self, wired):
        """A multi-result response is still ONE billable access, not one unit per result."""
        results = [
            {"content_id": f"cnt_{i}", "title": f"T{i}", "content_type": "article"} for i in range(5)
        ]
        with patch("partner_api.handler.search_content", return_value=results):
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        assert len(json.loads(response["body"])["results"]) == 5, "precondition: a multi-result response"
        assert len(wired.events) == 1

    def test_standard_query_keeps_its_analytics_collector_calls_unchanged(self, wired):
        """Req 8.8 — the operational metrics stay, exactly once each, alongside the one emit."""
        with patch("partner_api.handler.search_content", return_value=[]):
            with patch("partner_api.handler.get_analytics_collector") as collector_factory:
                collector = collector_factory.return_value
                _call_query({"query": "renewable energy"})

        collector.record_search_query.assert_called_once()
        collector.record_api_access.assert_called_once()
        # ...and exactly one billable event, not one per collector call.
        assert len(wired.events) == 1

    def test_sink_failure_does_not_fail_the_standard_response(self, monkeypatch):
        """Req 8.6 at the standard call site: a telemetry outage costs telemetry, not the response."""
        monkeypatch.setattr(usage_emitter, "_default_registry", lambda: FakeRegistry(RaisingSink()))

        with patch("partner_api.handler.search_content", return_value=[]):
            with patch("partner_api.handler.get_analytics_collector"):
                response = _call_query({"query": "renewable energy"})

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "success"


# ===================================================================================================
# The standard path's non-billable exemptions — control-flow guarantees at the call site
# ===================================================================================================
class TestStandardPathExemptionsAreControlFlow:
    """Each exemption below works because the emit sits BELOW the exit, not because of a condition
    inside ``services/usage_emitter.py``.

    Same shape as the ``304`` exemption on the content path and the ``error_msg`` exemption on the
    CoMP path: move the emit above any of these returns and these tests fail. That is the tripwire
    they exist to be — an edit to the emitter cannot defeat them, and an edit to the call site that
    reorders them cannot pass silently.
    """

    def test_standard_query_missing_query_400s_and_emits_nothing(self, wired):
        response = _call_query({})

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "Query parameter is required"
        assert wired.events == [], (
            "a 400 /query emitted a billable usage event — the missing-`query` early return MUST "
            "stay above the emit call; the exemption is a control-flow guarantee, not a condition "
            "inside the emitter"
        )

    def test_standard_query_blank_query_emits_nothing(self, wired):
        """A whitespace-only query is the same 400, above the emit."""
        response = _call_query({"query": "   "})

        assert response["statusCode"] == 400
        assert wired.events == []

    def test_standard_query_500_emits_nothing(self, wired):
        """A failure anywhere above the emit becomes a 500 and bills nothing."""
        with patch("partner_api.handler.search_content", side_effect=RuntimeError("table gone")):
            response = _call_query({"query": "renewable energy"})

        assert response["statusCode"] == 500
        assert wired.events == []

    def test_the_emit_is_the_last_statement_before_each_standard_response(self):
        """Pin the ORDERING, so a future edit cannot quietly hoist the emit above an error exit.

        The assertions above would all still pass if the emit were moved above the
        ``analytics_collector`` block (it would simply emit earlier), but the exemptions are only
        structurally safe while the emit is the final statement on each path. This reads the source
        and asserts that position directly — a behavioral test cannot see statement order.
        """
        # One discovery surface on main — search was consolidated into /query, so this loop
        # pins the single consolidated handler rather than a pair.
        for handler, emit_marker in ((handle_query_request, "USAGE_CONTENT_TYPE_STANDARD_QUERY"),):
            source = inspect.getsource(_unwrap(handler))
            emit_at = source.index(f"_emit_standard_usage_event(")
            validation_at = source.index('"Query parameter is required"')
            assert validation_at < emit_at, (
                f"{handler.__name__}: the missing-`query` 400 must stay ABOVE the standard emit"
            )
            # The emit's own call must carry this surface's content_type, and the only thing after it
            # on the success path is the response return.
            tail = source[emit_at:]
            assert emit_marker in tail.split("return create_success_response")[0], (
                f"{handler.__name__}: the standard emit must stamp {emit_marker}"
            )
            assert "create_success_response" in tail, (
                f"{handler.__name__}: the standard emit must sit immediately before the 200 response"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
