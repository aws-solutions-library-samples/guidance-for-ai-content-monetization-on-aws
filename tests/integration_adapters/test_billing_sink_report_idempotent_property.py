"""Property test for Property 6 (integration-adapters, GitLab #35, task 5.7).

Feature: integration-adapters, Property 6: BillingSinkPort.report is batch-idempotent under a
repeated idempotency key

Validates: Requirements 2.5.

*For any* batch of ``UsageEvent``s and *for any* ``idempotency_key``, invoking
``report(batch, idempotency_key=k)`` twice with the same key has the same effect as invoking it
once (no double-metering). Because the concrete ``BatchMeterUsage`` adapter body is owned by #3
and not implemented in this spec, this property validates the **port contract** against an
in-memory FAKE idempotent sink implementing ``BillingSinkPort`` (dedup by ``idempotency_key``),
built locally in this module — deterministic, gated unit tier, no live AWS (design "Testing
Strategy" → property-based tests, fakes-not-AWS).

The fake is intentionally local to this property: a fake idempotent billing sink is specific to
the batch-idempotency contract this test exercises. Hypothesis runs >=100 examples per the design
(``@settings(max_examples=100)``). One property -> one property-based test.

Collection-warning note: this module does NOT import ``TestResult`` (or any ``Test*``-named model)
into its namespace, to avoid the benign ``PytestCollectionWarning`` pytest raises for dataclasses
matching its ``Test*`` collection glob. The fake's ``test()``/``status()`` methods construct their
return values lazily inside the method body (importing the model locally), so no ``Test*`` symbol
enters the module namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from hypothesis import given, settings
from hypothesis import strategies as st

from integrations.marketplace.port import BillingSinkPort
from shared.utils.models import ReportResult, UsageEvent

# ─────────────────────────────────────────────────────────────────────────────
# Local fake: an in-memory idempotent billing sink implementing BillingSinkPort.
#
# Contract modeled (Requirement 2.5 / design Property 6): report(batch, idempotency_key=k) is
# idempotent under a repeated key. The sink records each metered batch keyed by idempotency_key;
# a second report under an already-seen key is a no-op with respect to metered state (no
# double-metering) and returns a result reflecting the already-recorded batch. This mirrors the
# AWS BatchMeterUsage retry-safe contract the concrete adapter (owned by #3) must satisfy.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FakeIdempotentBillingSink:
    """In-memory idempotent billing sink implementing the BillingSinkPort contract.

    ``_metered`` maps ``idempotency_key`` -> the accepted event count first recorded under that
    key. A repeated key does not add to metered state (the invariant Property 6 checks).
    """

    _metered: Dict[str, int] = field(default_factory=dict)

    def report(self, batch: List[UsageEvent], *, idempotency_key: str) -> ReportResult:
        if idempotency_key in self._metered:
            # Already metered under this key — no double-metering. Return the recorded result.
            accepted = self._metered[idempotency_key]
            return ReportResult(
                accepted=accepted, rejected=0, idempotency_key=idempotency_key, detail="deduped"
            )
        accepted = len(batch)
        self._metered[idempotency_key] = accepted
        return ReportResult(
            accepted=accepted, rejected=0, idempotency_key=idempotency_key, detail="metered"
        )

    @property
    def total_metered(self) -> int:
        """The total number of events metered across all recorded keys (metered state)."""
        return sum(self._metered.values())

    def test(self):  # noqa: ANN201 - return type imported locally to avoid Test* collection glob
        from shared.utils.models import TestResult

        return TestResult(ok=True, message="fake idempotent billing sink reachable")

    def status(self):  # noqa: ANN201
        from shared.utils.models import HealthStatus

        return HealthStatus(healthy=True, detail="fake idempotent billing sink healthy")


def _usage_events() -> st.SearchStrategy[UsageEvent]:
    """Generate arbitrary UsageEvents across the batch input space."""
    return st.builds(
        UsageEvent,
        content_id=st.text(min_size=0, max_size=24),
        partner_id=st.text(min_size=0, max_size=24),
        accessed_at=st.text(min_size=0, max_size=16),
        price_charged=st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False),
        currency=st.sampled_from(["USD", "EUR", "GBP"]),
        license_id=st.text(min_size=0, max_size=16),
        content_type=st.text(min_size=0, max_size=16),
    )


@settings(max_examples=100)
@given(
    batch=st.lists(_usage_events(), min_size=0, max_size=20),
    idempotency_key=st.text(min_size=1, max_size=32),
)
def test_report_is_batch_idempotent_under_repeated_key(
    batch: List[UsageEvent], idempotency_key: str
) -> None:
    """Feature: integration-adapters, Property 6: BillingSinkPort.report is batch-idempotent under
    a repeated idempotency key. Validates: Requirements 2.5.

    Reporting the same batch twice under the same idempotency_key has the same metered effect as
    reporting it once (no double-metering), against an idempotent sink implementing the port.
    """
    sink_once: BillingSinkPort = FakeIdempotentBillingSink()
    sink_twice: BillingSinkPort = FakeIdempotentBillingSink()

    # One invocation.
    result_once = sink_once.report(batch, idempotency_key=idempotency_key)

    # Two invocations under the SAME key.
    first = sink_twice.report(batch, idempotency_key=idempotency_key)
    second = sink_twice.report(batch, idempotency_key=idempotency_key)

    # Same effect: the metered state after two same-key reports equals the state after one report.
    assert sink_twice.total_metered == sink_once.total_metered == len(batch)

    # The first invocation of the twice-sink meters the batch exactly like the once-sink.
    assert first.accepted == result_once.accepted == len(batch)

    # The second (repeated-key) invocation does not add to metered state (no double-metering).
    assert second.accepted == first.accepted
    assert second.idempotency_key == idempotency_key
