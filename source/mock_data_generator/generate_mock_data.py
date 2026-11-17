#!/usr/bin/env python3
"""
Main execution script for generating mock data for the AI Content Monetization platform.

This script orchestrates the generation of realistic test data for all DynamoDB tables
in the proper dependency order, with progress reporting and completion statistics.

Usage:
    python generate_mock_data.py [options]

Examples:
    # Generate default amounts of data
    python generate_mock_data.py

    # Generate custom amounts with clearing existing data
    python generate_mock_data.py --content-count 100 --partner-count 20 --clear-existing

    # Generate data for specific date range
    python generate_mock_data.py --date-range-months 6
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator


def setup_logging(verbose: bool = False) -> None:
    """
    Set up logging configuration for the script.

    Args:
        verbose: Enable debug-level logging if True
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(level=log_level, format=log_format, handlers=[logging.StreamHandler(sys.stdout)])

    # Reduce boto3 logging noise
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_arguments() -> argparse.Namespace:
    """
    Parse command line arguments for the mock data generator.

    Returns:
        Parsed command line arguments
    """
    parser = argparse.ArgumentParser(
        description="Generate mock data for AI Content Monetization platform DynamoDB tables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Generate default amounts of data
  %(prog)s --content-count 100 --partner-count 20  # Custom data volumes
  %(prog)s --clear-existing                   # Clear existing data first
  %(prog)s --date-range-months 6              # Generate data for last 6 months
  %(prog)s --aws-profile myprofile            # Use specific AWS profile
        """,
    )

    # Data volume options
    parser.add_argument(
        "--content-count", type=int, default=20, help="Number of content items to generate (default: 20)"
    )

    parser.add_argument(
        "--partner-count", type=int, default=5, help="Number of partner records to generate (default: 5)"
    )

    parser.add_argument(
        "--access-log-count", type=int, default=100, help="Number of access log entries to generate (default: 100)"
    )

    parser.add_argument("--job-count", type=int, default=30, help="Number of job records to generate (default: 30)")

    # Time range options
    parser.add_argument(
        "--date-range-months", type=int, default=12, help="Number of months back to generate data for (default: 12)"
    )

    # Data management options
    parser.add_argument(
        "--clear-existing", action="store_true", help="Clear existing data from tables before generating new data"
    )

    # AWS configuration options
    parser.add_argument("--aws-profile", type=str, default=None,
        help="AWS profile to use (default: standard AWS credential chain)",)

    parser.add_argument("--aws-region", type=str, default="us-west-2", help="AWS region to use (default: us-west-2)")

    parser.add_argument(
        "--stack-name",
        type=str,
        default=os.environ.get("STACK_NAME", "ContentMonetizationStack"),
        help="Stack whose PartnerTableName output names the partner table (default: ContentMonetizationStack)",
    )

    parser.add_argument(
        "--partner-table-name",
        type=str,
        default=os.environ.get("PARTNER_TABLE_NAME", ""),
        help="Partner table name; overrides the stack output (default: PARTNER_TABLE_NAME)",
    )

    # Output options
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug output")

    parser.add_argument(
        "--dry-run", action="store_true", help="Validate configuration and connectivity without generating data"
    )

    parser.add_argument(
        "--validate-only", action="store_true", help="Generate data in memory and validate without writing to DynamoDB"
    )

    return parser.parse_args()


def resolve_partner_table_name(stack_name: str, profile: str, region: str) -> str:
    """Return the ``PartnerTableName`` output of ``stack_name`` (empty string when unavailable)."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

    try:
        try:
            session = boto3.Session(profile_name=profile or None, region_name=region)
        except ProfileNotFound:
            session = boto3.Session(region_name=region)
        stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    except (BotoCoreError, ClientError) as exc:
        logging.getLogger(__name__).error(f"Cannot read outputs of stack {stack_name}: {exc}")
        return ""
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    return outputs.get("PartnerTableName", "")


def validate_configuration(config: GeneratorConfig) -> bool:
    """
    Validate the generator configuration.

    Args:
        config: Generator configuration to validate

    Returns:
        True if configuration is valid, False otherwise
    """
    logger = logging.getLogger(__name__)

    try:
        # Validate data counts
        if config.content_count <= 0:
            logger.error("Content count must be positive")
            return False

        if config.partner_count <= 0:
            logger.error("Partner count must be positive")
            return False

        if config.access_log_count <= 0:
            logger.error("Access log count must be positive")
            return False

        if config.date_range_months <= 0:
            logger.error("Date range months must be positive")
            return False

        # Validate reasonable limits
        if config.content_count > 10000:
            logger.warning(f"Large content count ({config.content_count}) may take significant time")

        if config.access_log_count > 100000:
            logger.warning(f"Large access log count ({config.access_log_count}) may take significant time")

        logger.info("Configuration validation passed")
        return True

    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        return False


def print_generation_plan(config: GeneratorConfig) -> None:
    """
    Print the data generation plan to the user.

    Args:
        config: Generator configuration
    """
    logger = logging.getLogger(__name__)

    logger.info("=== Mock Data Generation Plan ===")
    logger.info(f"Content items: {config.content_count}")
    logger.info(f"Partner records: {config.partner_count}")
    logger.info(f"Access log entries: {config.access_log_count}")
    logger.info(f"Job records: {config.job_count}")
    logger.info(f"Date range: {config.date_range_months} months")
    logger.info(f"AWS Profile: {config.aws_profile}")
    logger.info(f"AWS Region: {config.aws_region}")
    logger.info(f"Clear existing data: {config.clear_existing}")
    logger.info("================================")


def generate_data_with_progress(generator: MockDataGenerator, config: GeneratorConfig) -> Dict[str, Any]:
    """
    Generate all mock data with progress reporting.

    Args:
        generator: Configured mock data generator
        config: Generator configuration

    Returns:
        Dictionary with generation statistics
    """
    logger = logging.getLogger(__name__)
    start_time = time.time()

    stats = {
        "start_time": datetime.now().isoformat(),
        "content_items": 0,
        "partner_records": 0,
        "access_logs": 0,
        "job_records": 0,
        "analytics_records": 0,
        "pricing_rules_records": 0,
        "pricing_transactions_records": 0,
        "errors": [],
        "total_time_seconds": 0,
    }

    try:
        # Step 1: Clear existing data if requested
        if config.clear_existing:
            logger.info("Step 1: Clearing existing data from all tables...")
            step_start = time.time()

            if generator.clear_all_tables():
                logger.info(f"✓ Cleared existing data in {time.time() - step_start:.1f}s")
            else:
                error_msg = "Failed to clear existing data"
                logger.error(f"✗ {error_msg}")
                stats["errors"].append(error_msg)
                return stats
        else:
            logger.info("Step 1: Skipping data clearing (not requested)")

        # Step 2: Generate partner data (no dependencies)
        logger.info("Step 2: Generating partner authentication data...")
        step_start = time.time()

        partner_data = generator.generate_partner_data()
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["partners"], partner_data):
            stats["partner_records"] = len(partner_data)
            logger.info(f"✓ Generated {len(partner_data)} partner records in {time.time() - step_start:.1f}s")
        else:
            error_msg = "Failed to write partner data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 3: Generate content data (no dependencies)
        logger.info("Step 3: Generating content items with IAB taxonomy...")
        step_start = time.time()

        content_data = generator.generate_content_data()
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["content"], content_data):
            stats["content_items"] = len(content_data)
            logger.info(f"✓ Generated {len(content_data)} content items in {time.time() - step_start:.1f}s")
        else:
            error_msg = "Failed to write content data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 4: Generate job data (depends on content and partners)
        logger.info("Step 4: Generating processing job records...")
        step_start = time.time()

        # Extract IDs for referential integrity
        content_ids = [item["content_id"]["S"] for item in content_data]
        partner_ids = [item["partner_id"]["S"] for item in partner_data]

        job_data = generator.generate_job_data(content_ids, partner_ids)
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["jobs"], job_data):
            stats["job_records"] = len(job_data)
            logger.info(f"✓ Generated {len(job_data)} job records in {time.time() - step_start:.1f}s")
        else:
            error_msg = "Failed to write job data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 5: Generate access log data (depends on content and partners)
        logger.info("Step 5: Generating access log entries...")
        step_start = time.time()

        access_log_data = generator.generate_access_logs(content_ids, partner_ids)
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["access_logs"], access_log_data):
            stats["access_logs"] = len(access_log_data)
            logger.info(f"✓ Generated {len(access_log_data)} access log entries in {time.time() - step_start:.1f}s")
        else:
            error_msg = "Failed to write access log data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 6: Generate analytics data (depends on content and partners)
        logger.info("Step 6: Generating analytics metrics and bot access tracking data...")
        step_start = time.time()

        analytics_data = generator.generate_analytics_data(content_ids, partner_ids)
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["analytics"], analytics_data):
            stats["analytics_records"] = len(analytics_data)
            logger.info(f"✓ Generated {len(analytics_data)} analytics records in {time.time() - step_start:.1f}s")
        else:
            error_msg = "Failed to write analytics data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 7: Generate pricing rules data (no dependencies)
        logger.info("Step 7: Generating pricing rules with versioning...")
        step_start = time.time()

        pricing_rules_data = generator.generate_pricing_rules_data()
        if generator.batch_write_items_chunked(generator.TABLE_NAMES["pricing_rules"], pricing_rules_data):
            stats["pricing_rules_records"] = len(pricing_rules_data)
            logger.info(
                f"✓ Generated {len(pricing_rules_data)} pricing rules records in {time.time() - step_start:.1f}s"
            )
        else:
            error_msg = "Failed to write pricing rules data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Step 8: Generate pricing transactions data (depends on content and partners)
        logger.info("Step 8: Generating pricing transaction history...")
        step_start = time.time()

        pricing_transactions_data = generator.generate_pricing_transactions_data(content_ids, partner_ids)
        if generator.batch_write_items_chunked(
            generator.TABLE_NAMES["pricing_transactions"], pricing_transactions_data
        ):
            stats["pricing_transactions_records"] = len(pricing_transactions_data)
            logger.info(
                f"✓ Generated {len(pricing_transactions_data)} pricing transaction records in {time.time() - step_start:.1f}s"
            )
        else:
            error_msg = "Failed to write pricing transactions data to DynamoDB"
            logger.error(f"✗ {error_msg}")
            stats["errors"].append(error_msg)
            return stats

        # Calculate total time
        stats["total_time_seconds"] = time.time() - start_time
        stats["end_time"] = datetime.now().isoformat()

        logger.info("=== Data Generation Complete ===")
        logger.info(f"✓ Total time: {stats['total_time_seconds']:.1f} seconds")

        return stats

    except Exception as e:
        error_msg = f"Unexpected error during data generation: {e}"
        logger.error(f"✗ {error_msg}")
        stats["errors"].append(error_msg)
        stats["total_time_seconds"] = time.time() - start_time
        return stats


def print_completion_statistics(stats: Dict[str, Any]) -> None:
    """
    Print detailed completion statistics.

    Args:
        stats: Generation statistics dictionary
    """
    logger = logging.getLogger(__name__)

    logger.info("=== Generation Statistics ===")
    logger.info(f"Content items created: {stats['content_items']}")
    logger.info(f"Partner records created: {stats['partner_records']}")
    logger.info(f"Job records created: {stats['job_records']}")
    logger.info(f"Access log entries created: {stats['access_logs']}")
    logger.info(f"Analytics records created: {stats['analytics_records']}")
    logger.info(f"Pricing rules records created: {stats['pricing_rules_records']}")
    logger.info(f"Pricing transactions records created: {stats['pricing_transactions_records']}")
    logger.info(
        f"Total records created: {stats['content_items'] + stats['partner_records'] + stats['job_records'] + stats['access_logs'] + stats['analytics_records'] + stats['pricing_rules_records'] + stats['pricing_transactions_records']}"
    )
    logger.info(f"Total execution time: {stats['total_time_seconds']:.1f} seconds")

    if stats["errors"]:
        logger.error(f"Errors encountered: {len(stats['errors'])}")
        for error in stats["errors"]:
            logger.error(f"  - {error}")
    else:
        logger.info("✓ No errors encountered")

    logger.info("=============================")


def main() -> int:
    """
    Main entry point for the mock data generation script.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Parse command line arguments
    args = parse_arguments()

    # Set up logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        # Create configuration from arguments
        config = GeneratorConfig(
            content_count=args.content_count,
            partner_count=args.partner_count,
            access_log_count=args.access_log_count,
            job_count=args.job_count,
            date_range_months=args.date_range_months,
            clear_existing=args.clear_existing,
            aws_profile=args.aws_profile,
            aws_region=args.aws_region,
            partner_table_name=args.partner_table_name,
        )

        # The partner table name is generated by CloudFormation; resolve it from the stack output.
        if not config.partner_table_name:
            config.partner_table_name = resolve_partner_table_name(args.stack_name, args.aws_profile, args.aws_region)
            logger.info(f"Partner table from {args.stack_name} output PartnerTableName: {config.partner_table_name}")

        # Validate configuration
        if not validate_configuration(config):
            logger.error("Configuration validation failed")
            return 1

        # Print generation plan
        print_generation_plan(config)

        # Initialize generator
        logger.info("Initializing mock data generator...")
        generator = MockDataGenerator(config)

        # Validate AWS connectivity
        logger.info("Validating AWS connectivity...")
        if not generator.validate_aws_connectivity():
            logger.error("AWS connectivity validation failed")
            return 1

        # Validate table access
        logger.info("Validating DynamoDB table access...")
        if not generator.validate_all_tables():
            logger.error("DynamoDB table validation failed")
            return 1

        # Handle dry run
        if args.dry_run:
            logger.info("✓ Dry run completed successfully - configuration and connectivity validated")
            return 0

        # Handle validation-only mode
        if args.validate_only:
            logger.info("Starting validation-only mode...")
            if generator.validate_data_generation_only():
                logger.info("✓ Validation-only mode completed successfully")
                return 0
            else:
                logger.error("✗ Validation-only mode failed")
                return 1

        # Generate data with progress reporting
        logger.info("Starting mock data generation...")
        stats = generate_data_with_progress(generator, config)

        # Print completion statistics
        print_completion_statistics(stats)

        # Return appropriate exit code
        if stats["errors"]:
            logger.error("Data generation completed with errors")
            return 1
        else:
            logger.info("✓ Data generation completed successfully")
            return 0

    except KeyboardInterrupt:
        logger.warning("Data generation interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback

            logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
