#!/usr/bin/env python3
"""
Seed the sample partners used for deployed acceptance and demos.

Partners live in the partners table (``PartnerTableName`` output) as ``PARTNER#<id>`` / ``META`` items; their credentials are
API Gateway keys bound to the usage plan of their pricing tier and recorded as ``KEY#`` items.
This script reuses ``PartnersService.provision_key`` so the seed and the lifecycle API issue keys
the same way. Key values are printed once, here, and never stored.

The test partner is special: its key is created by the stack (output ``TestPartnerApiKeyId``),
so the seed only writes its META/KEY# items and prints how to read the value.

Idempotent on ``partner_id``: an existing partner keeps its keys unless ``--clear-existing`` is
given, which first deletes its API Gateway keys (and their usage-plan associations) and ``KEY#``
items, then re-provisions.

Usage:
    python deployment/data/add-sample-partners.py [--region REGION] [--profile PROFILE]
        [--stack-name NAME | --table-name NAME --test-key-id ID] [--clear-existing]
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3

_LAMBDA_SRC = str(Path(__file__).resolve().parents[2] / "source" / "lambda")
if _LAMBDA_SRC not in sys.path:
    sys.path.insert(0, _LAMBDA_SRC)

from services.partners_service import PartnersService  # noqa: E402
from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS  # noqa: E402
from shared.utils.models import PARTNER_META_SK, Partner, PartnerCredential, partner_pk  # noqa: E402

TEST_PARTNER_ID = "test-publisher-mcp"
TEST_PARTNER_TIER = "standard"  # the stack binds the test key to the standard plan


def create_sample_partners() -> List[Dict[str, Any]]:
    """Sample partner metadata. ``allowed_endpoints`` are key-gated operation ids (D3/Q5)."""
    partners = [
        {
            "partner_id": TEST_PARTNER_ID,
            "partner_name": "Test Partner (MCP tooling)",
            "partner_type": "ai_operator",
            "contact_email": "test-partner@example.com",
            "pricing_tier": TEST_PARTNER_TIER,
            "allowed_endpoints": [],  # empty = every key-gated operation
            "status": "active",
        },
        {
            "partner_id": "news_publisher_001",
            "partner_name": "Global News Network",
            "partner_type": "publisher",
            "contact_email": "api@globalnews.com",
            "pricing_tier": "premium",
            "allowed_endpoints": ["queryContent", "submitLog", "validateLicense"],
            "status": "active",
        },
        {
            "partner_id": "tech_blog_002",
            "partner_name": "TechInsights Blog",
            "partner_type": "publisher",
            "contact_email": "api@techinsights.com",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "status": "active",
        },
        {
            "partner_id": "research_org_003",
            "partner_name": "AI Research Institute",
            "partner_type": "ai_operator",
            "contact_email": "api@airesearch.org",
            "pricing_tier": "enterprise",
            "allowed_endpoints": ["queryContent", "submitLog", "validateLicense"],
            "status": "active",
        },
        {
            "partner_id": "startup_004",
            "partner_name": "InnovateTech Startup",
            "partner_type": "ai_operator",
            "contact_email": "api@innovatetech.com",
            "pricing_tier": "basic",
            "allowed_endpoints": ["queryContent"],
            "status": "active",
        },
        {
            "partner_id": "suspended_005",
            "partner_name": "Suspended Partner",
            "partner_type": "analytics_provider",
            "contact_email": "api@suspended.example",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent"],
            "status": "suspended",
        },
    ]
    for partner in partners:
        unknown = set(partner["allowed_endpoints"]) - KEY_GATED_OPERATION_IDS
        if unknown:
            raise ValueError(
                f"{partner['partner_id']}: allowed_endpoints not key-gated operation ids: {sorted(unknown)}"
            )
    return partners


def _stack_outputs(stack_name: str, session: boto3.Session, region: str) -> Dict[str, str]:
    cf = session.client("cloudformation", region_name=region)
    stacks = cf.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def seed_partners(
    partners: List[Dict[str, Any]],
    *,
    table_name: str,
    test_key_id: Optional[str],
    region: str,
    session: boto3.Session,
    clear_existing: bool = False,
) -> List[Dict[str, Any]]:
    """Write META items and provision keys. Returns one summary row per partner (keys shown once)."""
    os.environ["PARTNER_TABLE_NAME"] = table_name
    dynamodb = session.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    service = PartnersService(apigateway_client=session.client("apigateway", region_name=region))
    now = datetime.now(timezone.utc).isoformat()
    summary = []

    for spec in partners:
        partner_id = spec["partner_id"]
        existing = table.get_item(Key={"PK": partner_pk(partner_id), "SK": PARTNER_META_SK}).get("Item")

        if existing and clear_existing:
            if partner_id == TEST_PARTNER_ID and test_key_id:
                # The test key is owned by the stack: drop every other credential but keep it.
                others = [k["key_id"] for k in service.get_partner(partner_id)["keys"] if k["key_id"] != test_key_id]
                service.delete_keys(partner_id, key_ids=others)
            else:
                service.delete_keys(partner_id)
            existing = None

        if existing:
            meta = Partner.from_item(existing)
            for field, value in spec.items():
                setattr(meta, field, value)
            meta.updated_at = now
        else:
            meta = Partner(created_at=now, updated_at=now, identity_source="direct", **spec)
        table.put_item(Item=meta.to_item())

        existing_keys = service.get_partner(partner_id)["keys"]  # any state: a suspended partner keeps a disabled key
        row: Dict[str, Any] = {"partner_id": partner_id, "status": meta.status, "pricing_tier": meta.pricing_tier}

        if partner_id == TEST_PARTNER_ID:
            if test_key_id and not any(k["key_id"] == test_key_id for k in existing_keys):
                table.put_item(
                    Item=PartnerCredential(
                        partner_id=partner_id,
                        api_key_id=test_key_id,
                        usage_plan_id=_usage_plan_for_tier(service, TEST_PARTNER_TIER),
                        status="active",
                        created_at=now,
                    ).to_item()
                )
            row["key_id"] = test_key_id
            row["key_value"] = "(read with: aws apigateway get-api-key --include-value --api-key %s)" % test_key_id
        elif existing_keys:
            row["key_id"] = existing_keys[0]["key_id"]
            row["key_value"] = "(existing key; value shown only when provisioned)"
        else:
            key = service.provision_key(partner_id, meta.pricing_tier)
            if meta.status == "suspended":
                service.disable_keys(partner_id)
            row["key_id"] = key["key_id"]
            row["key_value"] = key["key_value"]
        summary.append(row)

    return summary


def _usage_plan_for_tier(service: PartnersService, tier: str) -> str:
    from services.partners_service import usage_plan_id_for

    return usage_plan_id_for(tier, service._apigateway())


def print_summary(rows: List[Dict[str, Any]], table_name: str, region: str) -> None:
    print(f"📝 Seeded {len(rows)} sample partners in {table_name} ({region})")
    print("")
    for row in rows:
        print(f"✅ {row['partner_id']}  tier={row['pricing_tier']}  status={row['status']}")
        print(f"   • key id:    {row.get('key_id')}")
        print(f"   • key value: {row.get('key_value')}")
    print("")
    print("🔗 Partner API example (replace API_KEY and PARTNER_API_URL from the PartnerApiUrl output):")
    print(
        "   curl -X POST -H 'x-api-key: API_KEY' -H 'Content-Type: application/json' "
        "https://PARTNER_API_URL/query "
        """-d '{"query":"technology news","content_type":["article"],"max_results":10}'"""
    )
    print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed sample partners and provision their API keys")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--profile")
    parser.add_argument("--table-name", help="Partner table (default: PartnerTableName stack output)")
    parser.add_argument("--test-key-id", help="Test partner key id (default: TestPartnerApiKeyId stack output)")
    parser.add_argument("--stack-name", default="ContentMonetizationStack")
    parser.add_argument("--clear-existing", action="store_true", help="Delete existing keys before re-provisioning")
    args = parser.parse_args()

    print("🔐 Sample partners")
    print("=" * 60)
    try:
        # PartnersService builds its boto3 DynamoDB/API Gateway clients lazily with the ambient
        # region. Lambda always sets AWS_REGION; a local/CI shell may set neither AWS_REGION nor
        # AWS_DEFAULT_REGION, in which case boto3 falls back to ~/.aws/config (often a different
        # region) and reads/writes the wrong account's tables. Pin the region the caller asked for
        # so the seed is deterministic regardless of the shell's region configuration.
        os.environ.setdefault("AWS_DEFAULT_REGION", args.region)
        session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
        table_name, test_key_id = args.table_name, args.test_key_id
        if not table_name or not test_key_id:
            outputs = _stack_outputs(args.stack_name, session, args.region)
            table_name = table_name or outputs["PartnerTableName"]
            test_key_id = test_key_id or outputs.get("TestPartnerApiKeyId")

        rows = seed_partners(
            create_sample_partners(),
            table_name=table_name,
            test_key_id=test_key_id,
            region=args.region,
            session=session,
            clear_existing=args.clear_existing,
        )
        print_summary(rows, table_name, args.region)
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"❌ Failed to seed sample partners: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
