"""Partner / PartnerCredential model round-trips (spec: native-apikey-auth-migration, task 2.2)."""

from shared.utils.models import (
    PARTNER_META_SK,
    Partner,
    PartnerCredential,
    partner_key_sk,
    partner_pk,
)


class TestPartnerItem:
    def test_to_item_adds_identity_keys_and_drops_none(self):
        partner = Partner(
            partner_id="ptr_abc123",
            partner_name="Global News Network",
            pricing_tier="premium",
            allowed_endpoints=["queryContent", "submitLog"],
            contact_email="ai@gnn.example",
            created_at="2026-10-05T00:00:00Z",
        )
        item = partner.to_item()
        assert item["PK"] == "PARTNER#ptr_abc123"
        assert item["SK"] == PARTNER_META_SK
        assert item["status"] == "active"
        assert item["identity_source"] == "direct"
        assert item["allowed_endpoints"] == ["queryContent", "submitLog"]
        assert "partner_type" not in item  # None dropped
        for forbidden in ("partner_key_hash", "api_quota", "api_usage", "api_key_value"):
            assert forbidden not in item

    def test_from_item_ignores_keys_and_unknown_attributes(self):
        item = {
            "PK": "PARTNER#ptr_abc123",
            "SK": "META",
            "partner_id": "ptr_abc123",
            "partner_name": "Global News Network",
            "pricing_tier": "basic",
            "status": "suspended",
            "identity_source": "direct",
            "legacy_attribute": "ignored",
        }
        partner = Partner.from_item(item)
        assert partner.partner_id == "ptr_abc123"
        assert partner.status == "suspended"
        assert partner.allowed_endpoints == []
        assert not hasattr(partner, "legacy_attribute")

    def test_round_trip(self):
        original = Partner(
            partner_id="ptr_rt",
            partner_name="Round Trip",
            identity_source="marketplace",
            marketplace_type="aws",
            marketplace_customer_id="cust-1",
        )
        assert Partner.from_item(original.to_item()) == original


class TestPartnerCredentialItem:
    def test_to_item_uses_key_sort_key_and_never_a_value(self):
        cred = PartnerCredential(
            partner_id="ptr_abc123",
            api_key_id="k1a2b3c4d5",
            usage_plan_id="plan-premium",
            created_at="2026-10-05T00:00:00Z",
        )
        item = cred.to_item()
        assert item["PK"] == partner_pk("ptr_abc123")
        assert item["SK"] == partner_key_sk("k1a2b3c4d5") == "KEY#k1a2b3c4d5"
        assert item["status"] == "active"
        assert "disabled_at" not in item
        assert not any("value" in k for k in item)

    def test_round_trip_with_disabled_key(self):
        original = PartnerCredential(
            partner_id="ptr_abc123",
            api_key_id="k_old",
            usage_plan_id="plan-basic",
            status="disabled",
            disabled_at="2026-10-06T00:00:00Z",
        )
        assert PartnerCredential.from_item(original.to_item()) == original
