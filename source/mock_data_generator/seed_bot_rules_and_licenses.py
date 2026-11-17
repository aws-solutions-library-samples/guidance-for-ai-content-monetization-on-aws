#!/usr/bin/env python3
"""
Seed the ``cm-bot-rules`` and ``cm-licenses`` DynamoDB tables.

This is the part of the optional Seed_Step (``make seed-data``) that the main
``generate_mock_data.py`` orchestrator does not cover:

- **bot-rules** are seeded via the generator's own ``generate_bot_rules_data()`` path, which
  fabricates representative WAF-style bot rules **without** requiring a live WAF WebACL (unlike the
  ``sync-waf-rules`` / ``import_waf_rules.py`` path, which reads a deployed WebACL).
- **licenses** have no generator support, so this module writes a small set of deterministic sample
  RSL license documents directly. Each document matches the shape produced by
  ``services/license_service.create_license`` (``license_id`` PK + numeric ``version`` SK,
  ``status='active'`` so ``GET /licenses`` returns them), and at least one includes the optional
  ``pricing`` field (real CoMP pricing lives on the license document — see wave0 design Decision B).

Idempotency (wave0 Req 6):
- License documents use **deterministic** ``license_id`` + ``version`` keys, so re-running upserts
  in place rather than accumulating duplicates.
- bot-rules are regenerated with fresh ids each run; pass ``--clear-existing`` (or run this after
  ``generate_mock_data.py --clear-existing``, which clears ``cm-bot-rules`` via
  ``clear_all_tables``) to keep the table in a consistent, non-duplicated state across re-seeds.

Run it the same way as ``generate_mock_data.py`` (``source`` on the import path)::

    PYTHONPATH=source python source/mock_data_generator/seed_bot_rules_and_licenses.py \
        --aws-profile my-profile --aws-region us-west-2 --clear-existing
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator

logger = logging.getLogger(__name__)

# Table name for licenses. Resolved from the CDK-provided env var with the cm-* fallback default
# (wave0 Phase 1 / steering §2). bot-rules is resolved from the generator's TABLE_NAMES.
LICENSES_TABLE_ENV = "LICENSES_TABLE_NAME"
LICENSES_TABLE_DEFAULT = "cm-licenses"


def _convert_floats_to_decimals(obj: Any) -> Any:
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, list):
        return [_convert_floats_to_decimals(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _convert_floats_to_decimals(value) for key, value in obj.items()}
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def sample_license_documents() -> List[Dict[str, Any]]:
    """Return a small set of deterministic sample RSL license documents.

    Keys (``license_id`` + ``version``) are fixed so repeated seeding upserts rather than
    duplicates. All are ``status='active'`` so ``GET /licenses`` (which filters on active) returns
    them. ``lic_default_v1`` matches the ``defaultLicenseId`` wired in ``app.py`` / ``cdk.json``.

    At least one document (``lic_commercial_paid_v1``) carries the optional ``pricing`` field with
    CoMP-style tiers (``pricetype`` enum 0-5), satisfying wave0 Req 5.4.
    """
    now = datetime.now(timezone.utc).isoformat()

    def _base(license_id: str, title: str, description: str, allowed_uses: List[int]) -> Dict[str, Any]:
        return {
            "license_id": license_id,
            "version": 1,
            "status": "active",
            "title": title,
            "description": description,
            "allowed_uses": allowed_uses,
            "allowed_subfunctions": [0, 1, 2, 3, 4, 5],
            "restrictions": {},
            "attribution": {"required": False, "format": None, "text": None, "url": None},
            "schema_url": None,
            "pricing": {},
            "effective_date": now,
            "expiration_date": None,
            "created_at": now,
            "updated_at": now,
            "created_by": "seed-script",
            "updated_by": "seed-script",
            "metadata": {"seeded": True},
        }

    default_license = _base(
        "lic_default_v1",
        "Default Discovery License",
        "Default license permitting search/discovery and inference use of catalog content.",
        [1, 3],
    )

    attribution_license = _base(
        "lic_inference_attribution_v1",
        "Inference with Attribution",
        "Permits inference and RAG with required text attribution.",
        [3],
    )
    attribution_license["allowed_subfunctions"] = [0, 1, 3, 5]
    attribution_license["attribution"] = {
        "required": True,
        "format": "text",
        "text": "Content provided by {publisher_name}. Visit {content_url} for details.",
        "url": "https://publisher.com/attribution-guidelines",
    }
    attribution_license["restrictions"] = {"max_queries_per_day": 10000, "commercial_use": True}

    commercial_license = _base(
        "lic_commercial_paid_v1",
        "Commercial License - Paid",
        "Paid commercial license without attribution requirement; real CoMP pricing tiers attached.",
        [1, 3, 4],
    )
    commercial_license["restrictions"] = {"commercial_use": True}
    # Optional pricing field (wave0 Req 5.4). CoMP-style tiers keyed by `pricetype` (enum 0-5).
    commercial_license["pricing"] = {
        "currency": "USD",
        "tiers": [
            {"pricetype": 1, "unit": "per_query", "amount": 0.002, "description": "Search/Discovery"},
            {"pricetype": 3, "unit": "per_1k_tokens", "amount": 0.02, "description": "Inference/RAG"},
            {"pricetype": 5, "unit": "flat_monthly", "amount": 1000.00, "description": "Analysis subscription"},
        ],
    }

    return [default_license, attribution_license, commercial_license]


def seed_bot_rules(generator: MockDataGenerator, clear_existing: bool) -> int:
    """Seed cm-bot-rules via the generator's bot-rules path (no live WAF). Returns record count."""
    table_name = generator.TABLE_NAMES["bot_rules"]

    if clear_existing:
        logger.info(f"Clearing existing bot-rules from '{table_name}'...")
        if not generator.clear_table(table_name):
            raise RuntimeError(f"Failed to clear bot-rules table '{table_name}'")

    records = generator.generate_bot_rules_data()
    if not generator.batch_write_items_chunked(table_name, records):
        raise RuntimeError(f"Failed to write bot-rules to '{table_name}'")

    logger.info(f"\u2713 Seeded {len(records)} bot-rules into '{table_name}'")
    return len(records)


def seed_licenses(generator: MockDataGenerator) -> int:
    """Seed cm-licenses with deterministic sample documents (idempotent upsert). Returns count."""
    table_name = os.environ.get(LICENSES_TABLE_ENV, LICENSES_TABLE_DEFAULT)
    table = generator.dynamodb_resource.Table(table_name)

    documents = sample_license_documents()
    for doc in documents:
        table.put_item(Item=_convert_floats_to_decimals(doc))

    logger.info(f"\u2713 Seeded {len(documents)} licenses into '{table_name}'")
    return len(documents)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed cm-bot-rules (via generator) and cm-licenses (deterministic sample docs)."
    )
    parser.add_argument("--aws-profile", type=str, default=None,
        help="AWS profile to use (default: standard AWS credential chain)",)
    parser.add_argument("--aws-region", type=str, default="us-west-2", help="AWS region to use (default: us-west-2)")
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Clear existing bot-rules before seeding (licenses always upsert deterministically)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug output")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)

    # Only the AWS session fields of GeneratorConfig matter here; data counts keep their defaults.
    config = GeneratorConfig(
        clear_existing=args.clear_existing,
        aws_profile=args.aws_profile,
        aws_region=args.aws_region,
    )

    try:
        generator = MockDataGenerator(config)
        if not generator.validate_aws_connectivity():
            logger.error("AWS connectivity validation failed")
            return 1

        bot_rules_count = seed_bot_rules(generator, args.clear_existing)
        licenses_count = seed_licenses(generator)

        logger.info(
            f"\u2713 Seed complete: {bot_rules_count} bot-rules, {licenses_count} licenses"
        )
        return 0
    except Exception as e:  # noqa: BLE001 - top-level script boundary
        logger.error(f"\u2717 Failed to seed bot-rules/licenses: {e}")
        if args.verbose:
            import traceback

            logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
