"""Guardrail META-tests for the adapter-owned-client conformance scan
(integration-adapters, GitLab #35, task 14.2, Requirement 8).

Where task 14.1 (``test_adapter_owned_clients.py``) IS the guardrail — the real scan of
``source/lambda`` plus scanner example/edge-case tests — this module tests the guardrail
*as a mechanism*. It reuses the guardrail's own importable helpers/constants (no scan is
re-implemented) and asserts the three land-green properties from Requirement 8:

  * **8.1 / 8.3 — land-green at introduction.** The guardrail's end-to-end verdict over the
    real ``source/lambda`` tree, computed exactly the way the guardrail computes it
    (:func:`_scan_all_constructions` + :func:`_is_allowed_location`), yields **no
    un-allowlisted offenders** with the seeded allowlist. i.e. the guardrail passes CI from
    the moment it is introduced.
  * **8.3 — staleness detection.** The stale-allowlist mechanic is exercised **directly**:
    a synthetic allowlist carrying a bogus ``(path, client)`` entry that matches no real
    construction is flagged as stale, while the real seeded allowlist is not. (Task 14.1
    already asserts the *real* allowlist is non-stale via
    ``TestPlannedUnroutedReported.test_planned_unrouted_are_real_constructions``; this
    meta-test drives the underlying set-difference logic with a synthetic input rather than
    duplicating that end-to-end assertion.)
  * **8.2 — location verdict.** Using the guardrail's location-check
    (:func:`_is_allowed_location`) over what its own scanner
    (:func:`find_adapter_owned_client_constructions`) extracts: a synthetic ``wafv2``
    construction at a path OUTSIDE ``integrations/access/`` is reported as an offender, and
    a general ``dynamodb`` (boto3) construction is NOT reported (it is never even extracted).

These complement — rather than duplicate — 14.1: 14.1 proves the scanner recognizes/ignores
the right call shapes and that the real tree is clean; 14.2 proves the guardrail's
*verdict pipeline* (scan → location check → offender list) and the *allowlist/staleness
mechanics* behave correctly on controlled inputs.

Deterministic, gated-unit-tier, no live AWS (Requirement 8.1).
"""

from __future__ import annotations

import os

# Reuse the guardrail's own helpers/constants — do NOT re-implement the scan (task 14.2).
from tests.import_guard.test_adapter_owned_clients import (
    ADAPTER_OWNED_CLIENTS,
    PLANNED_UNROUTED_CLIENTS,
    _is_allowed_location,
    _scan_all_constructions,
    find_adapter_owned_client_constructions,
)


def _compute_offenders(constructions):
    """Reproduce the guardrail's end-to-end verdict: offenders are constructions whose
    (path, client) is neither under the owning adapter dir nor on the allowlist.

    This is exactly the predicate
    ``TestAdapterOwnedClientsScopedToAdapters.test_no_adapter_owned_client_outside_its_adapter``
    asserts is empty; here we compute it so a meta-test can assert on the verdict itself.
    """
    return [
        (rel_path, lineno, client_name)
        for rel_path, lineno, client_name in constructions
        if not _is_allowed_location(rel_path, client_name)
    ]


class TestGuardrailLandsGreenMeta:
    """Req 8.1 / 8.3 — the guardrail's verdict over the real tree is clean at introduction."""

    def test_real_scan_yields_no_unallowlisted_offenders(self):
        """The land-green property: with the seeded allowlist, the real scan of
        source/lambda produces zero un-allowlisted offenders (Requirement 8.1, 8.3)."""
        offenders = _compute_offenders(_scan_all_constructions())
        assert offenders == [], (
            "Guardrail is not land-green: the real source/lambda scan reports offender(s) "
            "with the seeded allowlist — every adapter-owned client construction must live "
            "under its adapter dir or be on PLANNED_UNROUTED_CLIENTS:\n  "
            + "\n  ".join(f"{p}:{ln}: '{c}'" for p, ln, c in sorted(offenders))
        )

    def test_seeded_allowlist_entries_are_covered_by_the_scope_map(self):
        """Every allowlisted client name is one the guardrail actually scopes to — an
        allowlist entry for an out-of-scope client would be meaningless (Requirement 8.2/8.3)."""
        for (_path, client_name) in PLANNED_UNROUTED_CLIENTS:
            assert client_name in ADAPTER_OWNED_CLIENTS, (
                f"PLANNED_UNROUTED_CLIENTS carries '{client_name}', which is not an "
                f"adapter-owned client the guardrail scopes to {sorted(ADAPTER_OWNED_CLIENTS)}"
            )


class TestStaleAllowlistDetectionMeta:
    """Req 8.3 — the stale-entry detection logic flags a bogus allowlist entry."""

    @staticmethod
    def _stale_entries(allowlist, constructions):
        """The guardrail's staleness predicate (set-difference), parameterized on the
        allowlist so it can be driven with a synthetic input. Mirrors
        ``TestPlannedUnroutedReported.test_planned_unrouted_are_real_constructions``."""
        actual = {(rel_path, client) for rel_path, _lineno, client in constructions}
        return [entry for entry in allowlist if entry not in actual]

    def test_bogus_allowlist_entry_is_flagged_stale(self):
        """A synthetic allowlist entry that matches no real construction is detected."""
        constructions = _scan_all_constructions()
        bogus = (os.path.join("services", "does_not_exist.py"), "wafv2")
        synthetic_allowlist = {**PLANNED_UNROUTED_CLIENTS, bogus: "#bogus"}
        stale = self._stale_entries(synthetic_allowlist, constructions)
        assert bogus in stale, (
            "Staleness detection failed to flag a synthetic allowlist entry with no matching "
            "construction — a stale allowlist entry must be caught (Requirement 8.3)."
        )

    def test_real_seeded_allowlist_is_not_flagged_stale(self):
        """The seeded allowlist is NOT stale — every entry maps to a real construction."""
        constructions = _scan_all_constructions()
        stale = self._stale_entries(PLANNED_UNROUTED_CLIENTS, constructions)
        assert stale == [], (
            "The seeded PLANNED_UNROUTED_CLIENTS has stale entries (should ratchet toward "
            f"empty as sites migrate): {sorted(stale)}"
        )


class TestLocationVerdictMeta:
    """Req 8.2 — the location verdict flags an out-of-adapter wafv2 and ignores dynamodb."""

    def test_synthetic_out_of_adapter_wafv2_is_flagged(self):
        """A wafv2 construction the scanner extracts, located OUTSIDE integrations/access/,
        is reported as an offender by the guardrail's location check (Requirement 8.2)."""
        rel_path = os.path.join("services", "some_service.py")
        source = 'import boto3\nclient = boto3.client("wafv2")\n'

        extracted = find_adapter_owned_client_constructions(source, rel_path)
        assert extracted == [(2, "wafv2")], (
            "Scanner should extract the wafv2 construction from the synthetic snippet"
        )

        # Feed the extraction through the guardrail's verdict pipeline.
        synthetic_constructions = [(rel_path, lineno, name) for lineno, name in extracted]
        offenders = _compute_offenders(synthetic_constructions)
        assert (rel_path, 2, "wafv2") in offenders, (
            "A wafv2 client constructed outside integrations/access/ (and not on the "
            "allowlist) must be flagged as an offender (Requirement 8.2)."
        )

    def test_wafv2_inside_adapter_dir_is_not_flagged(self):
        """The same wafv2 construction INSIDE integrations/access/ is allowed — proving the
        offender verdict is location-driven, not name-driven (Requirement 8.2)."""
        rel_path = os.path.join("integrations", "access", "aws_waf.py")
        extracted = find_adapter_owned_client_constructions('boto3.client("wafv2")', rel_path)
        synthetic_constructions = [(rel_path, lineno, name) for lineno, name in extracted]
        assert _compute_offenders(synthetic_constructions) == [], (
            "A wafv2 client constructed inside its owning adapter dir must NOT be flagged."
        )

    def test_synthetic_dynamodb_is_not_flagged(self):
        """A general boto3 (dynamodb) construction is never extracted, hence never an
        offender — the guardrail must not flag pervasive legitimate boto3 (Requirement 8.2)."""
        rel_path = os.path.join("services", "content_service.py")
        source = 'import boto3\ntable = boto3.client("dynamodb")\n'

        extracted = find_adapter_owned_client_constructions(source, rel_path)
        assert extracted == [], (
            "dynamodb is general boto3 and must not be extracted as an adapter-owned client "
            "(Requirement 8.2)."
        )

        synthetic_constructions = [(rel_path, lineno, name) for lineno, name in extracted]
        assert _compute_offenders(synthetic_constructions) == [], (
            "A dynamodb construction must never be reported as an offender (Requirement 8.2)."
        )
