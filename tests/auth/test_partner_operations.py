"""The partner-plane operation registry must equal what the contract derives.

``shared.auth.partner_operations`` is hand-listed so the Lambda and CDK can import it without
parsing YAML at runtime; this test is the fail-closed guard that keeps it equal to the derivation
rule in the design: ``x-audience: partner AND (PartnerKeyAuth OR public)``, where an operation that
admits an anonymous requirement (``{}``) is public — a native key is required or not, never optional.
"""

from pathlib import Path

import yaml

from shared.auth import partner_operations as po

SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi-spec.yaml"
METHODS = ("get", "post", "put", "delete")


def _derive():
    spec = yaml.safe_load(SPEC_PATH.read_text())
    key_gated, public = {}, {}
    for path, item in spec["paths"].items():
        for method in METHODS:
            op = item.get(method)
            if not op or op.get("x-audience") != "partner":
                continue
            security = op.get("security", spec.get("security", []))
            anonymous_allowed = not security or any(entry == {} for entry in security)
            schemes = {name for entry in security if entry for name in entry}
            if anonymous_allowed:
                # "key optional" in the contract is public on the partner plane: a native
                # apiKeyRequired flag cannot express optional (Req 4.8).
                public[(method.upper(), path)] = op["operationId"]
            elif "PartnerKeyAuth" in schemes:
                key_gated[(method.upper(), path)] = op["operationId"]
    return key_gated, public


def test_key_gated_operations_match_contract():
    key_gated, _ = _derive()
    assert po.KEY_GATED_OPERATIONS == key_gated


def test_public_operations_match_contract():
    _, public = _derive()
    assert po.PUBLIC_OPERATIONS == public


def test_deferred_are_real_contract_operations():
    key_gated, public = _derive()
    assert po.DEFERRED_NO_HANDLER <= set(key_gated) | set(public)


def test_search_is_deleted_from_the_contract_and_registry():
    spec = yaml.safe_load(SPEC_PATH.read_text())
    assert "/search" not in spec["paths"]
    assert not hasattr(po, "RETIRED")


def test_allowed_endpoint_vocabulary_is_the_key_gated_ids():
    assert po.KEY_GATED_OPERATION_IDS == {"queryContent", "submitLog", "validateLicense"}
    assert po.is_key_gated("queryContent") and not po.is_key_gated("getApiInfo") and not po.is_key_gated(None)


def test_operation_lookup_uses_templated_resource():
    assert po.operation_id_for("post", "/licenses/{license_id}/validate") == "validateLicense"
    assert po.operation_id_for("GET", "/info") == "getApiInfo"
    assert po.operation_id_for("POST", "/search") is None  # deleted: no such operation
    assert po.operation_id_for("GET", "/partners") is None  # internal
    assert po.operation_id_for(None, "/query") is None
