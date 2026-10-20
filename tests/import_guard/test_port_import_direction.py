"""Port-import-direction guard (integration-adapters, GitLab #35, Requirement 1.2).

Enforces the hexagonal dependency direction at the port layer: every
``source/lambda/integrations/<domain>/port.py`` MUST depend on **nothing external** — no
``boto3``, no vendor/cloud SDK, and no cross-domain integration module. Ports define the
interface the core logic depends on; adapters (the ``<domain>/*.py`` files beside the port)
are the only place ``boto3``/vendor clients appear. If a ``port.py`` reaches for boto3 or
imports another domain's package, the direction has been inverted and this guard fails.

This is an AST-only check (it never imports/executes the code under test): it parses each
``port.py`` and inspects its top-level ``import`` / ``from`` statements.

Forbidden in a ``port.py``:
  * ``boto3`` / ``botocore`` and known vendor SDKs (see ``FORBIDDEN_TOP_LEVEL``).
  * an absolute import of ``integrations`` beyond that port's own domain (cross-domain).

Allowed:
  * stdlib and typing (``typing``, ``dataclasses``, ``abc``, ``enum``, ``__future__``, ...).
  * ``shared...`` (the Canonical_Model types the ports reference — Requirement 1.4).
  * the port's own domain (``integrations.<own-domain>...``) and same-package relative imports.
"""

from __future__ import annotations

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INTEGRATIONS_ROOT = os.path.join(REPO_ROOT, "source", "lambda", "integrations")

# Top-level packages a port.py may never import: AWS SDK + known third-party vendor clients.
# Ports depend on the canonical model + stdlib only — external clients live in adapters.
FORBIDDEN_TOP_LEVEL = {
    "boto3",
    "botocore",
    "aws_lambda_powertools",
    "requests",
    "urllib3",
    "httpx",
    "aiohttp",
    "pydantic",
    "sqlalchemy",
    "redis",
    "pymysql",
    "psycopg2",
    "opensearchpy",
    "elasticsearch",
}


def _domains() -> list[str]:
    """Domain subpackage names under integrations/ (content, marketplace, telemetry, access)."""
    return sorted(
        name
        for name in os.listdir(INTEGRATIONS_ROOT)
        if os.path.isdir(os.path.join(INTEGRATIONS_ROOT, name)) and name != "__pycache__"
    )


def _port_files() -> list[str]:
    """Absolute paths to every integrations/<domain>/port.py."""
    files = []
    for domain in _domains():
        candidate = os.path.join(INTEGRATIONS_ROOT, domain, "port.py")
        if os.path.isfile(candidate):
            files.append(candidate)
    return files


def _top_level(module: str | None) -> str | None:
    if not module:
        return None
    return module.split(".", 1)[0]


def find_port_import_violations(source: str, filename: str, own_domain: str) -> list[tuple[int, str]]:
    """Return (lineno, reason) for any forbidden import in a port.py (AST-only).

    Flags: forbidden external SDKs, and absolute cross-domain ``integrations.<other>`` imports.
    Relative imports (``level > 0``) stay within the port's own package and are allowed.
    """
    violations: list[tuple[int, str]] = []
    tree = ast.parse(source, filename=filename)

    def _check(module_name: str, lineno: int) -> None:
        top = _top_level(module_name)
        if top in FORBIDDEN_TOP_LEVEL:
            violations.append((lineno, f"forbidden external dependency '{top}'"))
            return
        # Cross-domain: an absolute integrations.<other-domain> import.
        parts = module_name.split(".")
        if parts[0] == "integrations" and len(parts) >= 2 and parts[1] != own_domain:
            violations.append(
                (lineno, f"cross-domain import '{module_name}' (own domain is '{own_domain}')")
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:  # relative import within the port's own package
                continue
            _check(node.module or "", node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                _check(alias.name, node.lineno)
    return violations


def test_integrations_layer_exists_with_domain_ports():
    """The additive Integrations_Layer skeleton exists with a port.py per domain (Req 1.1)."""
    assert os.path.isdir(INTEGRATIONS_ROOT), "source/lambda/integrations/ must exist"
    domains = set(_domains())
    assert {"content", "marketplace", "telemetry", "access"}.issubset(domains), (
        f"expected content/marketplace/telemetry/access domain subpackages, found {sorted(domains)}"
    )
    assert _port_files(), "each integrations/<domain>/ must contain a port.py"


def test_ports_depend_on_nothing_external():
    """Every integrations/<domain>/port.py imports no boto3/vendor SDK and no cross-domain module."""
    offenders: list[str] = []
    for path in _port_files():
        own_domain = os.path.basename(os.path.dirname(path))
        with open(path, "r", encoding="utf-8") as fh:
            try:
                source = fh.read()
                for lineno, reason in find_port_import_violations(source, path, own_domain):
                    rel = os.path.relpath(path, REPO_ROOT)
                    offenders.append(f"{rel}:{lineno}: {reason}")
            except SyntaxError as exc:  # pragma: no cover - a syntax error is a different failure
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}: could not parse: {exc}")
    assert not offenders, (
        "Port-import-direction violations (hexagonal direction inverted, #35 Req 1.2):\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Example / edge-case tests for the guard itself.
# ---------------------------------------------------------------------------

def test_guard_rejects_boto3_from_import():
    v = find_port_import_violations("from boto3 import client", "access/port.py", "access")
    assert v and v[0][0] == 1 and "boto3" in v[0][1]


def test_guard_rejects_boto3_plain_import():
    v = find_port_import_violations("import boto3", "access/port.py", "access")
    assert v == [(1, "forbidden external dependency 'boto3'")]


def test_guard_rejects_cross_domain_import():
    v = find_port_import_violations(
        "from integrations.marketplace.port import LicenseResolverPort", "access/port.py", "access"
    )
    assert v and "cross-domain" in v[0][1]


def test_guard_accepts_stdlib_and_typing():
    src = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from typing import Protocol\n"
        "from abc import ABC\n"
    )
    assert find_port_import_violations(src, "access/port.py", "access") == []


def test_guard_accepts_shared_model_import():
    # Ports reference the Canonical_Model types in shared.utils.models (Req 1.4).
    v = find_port_import_violations(
        "from shared.utils.models import UsageEvent", "telemetry/port.py", "telemetry"
    )
    assert v == []


def test_guard_accepts_own_domain_import():
    v = find_port_import_violations(
        "from integrations.access.port import AccessPolicyPort", "access/port.py", "access"
    )
    assert v == []


def test_guard_ignores_relative_imports():
    assert find_port_import_violations("from . import something", "access/port.py", "access") == []
