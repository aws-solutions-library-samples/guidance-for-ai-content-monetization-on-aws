"""Model-convention & scope-boundary unit test (integration-adapters, GitLab #35).

Validates the #33 model convention and the #35 scope boundary for the port-facing
Canonical_Model types added to ``source/lambda/shared/utils/models.py`` by tasks 2.1/2.2:

  * **Convention (Requirement 4.1 / design "Data Models"):** the new port-facing types are
    stdlib ``@dataclass`` types (``dataclasses.is_dataclass``), NOT Pydantic, and the module
    imports no Pydantic (``pydantic`` never appears in an ``import``/``from`` in models.py).
    They are all importable from the canonical home ``shared.utils.models`` (Requirement 1.4).

  * **Scope boundary (Requirement 4.2 / design):** this spec creates **type definitions only** —
    population/persistence/wiring stays with the verticals. Concretely, the internal-only types
    the design says are NOT persisted by this spec (``UsageEvent``, ``Entitlement``,
    ``AccessPolicy``) carry **no ``to_item()``** method (``to_item`` lives only where a type is
    actually persisted; these are not persisted here — persistence is added by the owning vertical:
    UsageEvent → #3, Entitlement → #3/#15, versioned rule persistence → #29). The existing
    persisted ``ContentMetadata`` still carries ``to_item()`` (guards against a convention drift
    that would drop persistence where it IS required).

AST-only for the no-Pydantic-import assertion (never imports pydantic); a normal import for the
dataclass/importability assertions.
"""

from __future__ import annotations

import ast
import dataclasses
import os

import pytest

from shared.utils import models
from shared.utils.models import (
    AccessMetrics,
    AccessPolicy,
    AccessRule,
    AdapterDescriptor,
    ChangeRequest,
    ContentMetadata,
    CustomerRegistration,
    DriftReport,
    Entitlement,
    HealthStatus,
    ListingDefinition,
    ListingStatus,
    ReportResult,
    TargetConfig,
    TestResult,
    UsageEvent,
)

# Every port-facing type this spec added to models.py (tasks 2.1 supporting + 2.2 core).
PORT_FACING_TYPES = [
    # 2.1 supporting value types
    TestResult,
    HealthStatus,
    DriftReport,
    AccessMetrics,
    TargetConfig,
    ReportResult,
    ChangeRequest,
    ListingStatus,
    ListingDefinition,
    CustomerRegistration,
    AdapterDescriptor,
    # 2.2 core port-facing model types
    UsageEvent,
    Entitlement,
    AccessRule,
    AccessPolicy,
]

# Internal-only types the design says this spec does NOT persist (below the wire) — they must
# therefore NOT carry a to_item() (persistence is owned by the verticals). Requirement 4.2.
INTERNAL_ONLY_NOT_PERSISTED = [UsageEvent, Entitlement, AccessPolicy]


def test_port_facing_types_are_stdlib_dataclasses():
    """Every new port-facing type is a stdlib dataclass (Requirement 4.1, #33 convention)."""
    non_dataclasses = [t.__name__ for t in PORT_FACING_TYPES if not dataclasses.is_dataclass(t)]
    assert not non_dataclasses, (
        f"expected stdlib @dataclass for all port-facing types, not: {non_dataclasses}"
    )


def test_port_facing_types_are_not_pydantic():
    """The port-facing types are NOT Pydantic models (no BaseModel in the MRO; no __fields__).

    Guards the '#33 convention: stdlib dataclass, no Pydantic in the Lambda tier' rule at the
    type level, complementing the module-level no-import assertion below.
    """
    offenders: list[str] = []
    for t in PORT_FACING_TYPES:
        mro_names = {f"{c.__module__}.{c.__qualname__}" for c in t.__mro__}
        looks_pydantic = (
            any("pydantic" in name for name in mro_names)
            # pydantic v1 BaseModel exposes __fields__; a stdlib dataclass does not.
            or (hasattr(t, "__fields__") and not dataclasses.is_dataclass(t))
        )
        if looks_pydantic:
            offenders.append(t.__name__)
    assert not offenders, f"port-facing types must not be Pydantic models: {offenders}"


def test_models_module_imports_no_pydantic():
    """models.py imports no pydantic (AST-only; never imports pydantic) — Requirement 4.1.

    'NO Pydantic in the Lambda tier' — the canonical models home must not depend on pydantic.
    """
    source = _read_models_source()
    tree = ast.parse(source, filename=models.__file__)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] == "pydantic":
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] == "pydantic":
                offenders.append(f"line {node.lineno}: from {node.module} import ...")
    assert not offenders, (
        "shared/utils/models.py must not import pydantic (#33 stdlib-dataclass convention):\n"
        + "\n".join(offenders)
    )


def test_port_facing_types_importable_from_canonical_home():
    """All port-facing types import from the single canonical home shared.utils.models (Req 1.4)."""
    for t in PORT_FACING_TYPES:
        assert t.__module__ == "shared.utils.models", (
            f"{t.__name__} must live in shared.utils.models, found in {t.__module__}"
        )


def test_internal_only_types_have_no_persistence_wiring():
    """Scope boundary (Requirement 4.2): internal-only types are NOT persisted here.

    Per the design Data Models section, UsageEvent / Entitlement / AccessPolicy carry NO
    to_item() — persistence (and population/wiring) stays with the owning verticals. This asserts
    the absence of any persistence method on these types, so the spec does not silently introduce
    a write path it does not own.
    """
    persistence_methods = ("to_item",)
    offenders: list[str] = []
    for t in INTERNAL_ONLY_NOT_PERSISTED:
        for meth in persistence_methods:
            if hasattr(t, meth):
                offenders.append(f"{t.__name__}.{meth}")
    assert not offenders, (
        "internal-only types must NOT define persistence wiring in this spec "
        f"(population/persistence stays with the verticals, Req 4.2): {offenders}"
    )


def test_persisted_content_metadata_still_has_to_item():
    """The persisted ContentMetadata still carries to_item() — to_item lives where a type IS
    persisted. Guards against a convention drift that removes persistence where it is required."""
    assert hasattr(ContentMetadata, "to_item"), "ContentMetadata must retain its to_item()"


def _read_models_source() -> str:
    with open(models.__file__, "r", encoding="utf-8") as fh:
        return fh.read()


# Sanity: models.__file__ is the canonical shared/utils/models.py under source/lambda.
def test_models_module_is_the_canonical_file():
    assert os.path.join("source", "lambda", "shared", "utils", "models.py") in os.path.normpath(
        models.__file__
    )
