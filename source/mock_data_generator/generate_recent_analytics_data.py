#!/usr/bin/env python3
"""
Generate Recent Analytics Data

This script uses the mock_data_generator to clear existing analytics data
and generate a small, recent dataset that fits within the current time window.
"""

import os
import sys
from datetime import datetime, timezone

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator


def generate_recent_analytics_data():
    """
    Generate recent analytics data using the mock_data_generator
    """

    print("🚀 Starting recent analytics data generation...")

    # Create configuration for small, recent dataset
    config = GeneratorConfig(
        # Small data volumes for testing
        content_count=10,
        partner_count=3,
        access_log_count=50,
        job_count=20,
        # Only generate data for the last 7 days (not months)
        date_range_months=1,  # We'll override this in the generator
        # Clear existing data first
        clear_existing=True,
        # AWS configuration
        aws_profile=None,  # standard AWS credential chain
        aws_region="us-west-2",
    )

    print("📊 Configuration:")
    print(f"  - Content items: {config.content_count}")
    print(f"  - Partners: {config.partner_count}")
    print(f"  - Access logs: {config.access_log_count}")
    print(f"  - Jobs: {config.job_count}")
    print(f"  - Clear existing: {config.clear_existing}")
    print(f"  - AWS Profile: {config.aws_profile}")

    try:
        # Initialize the generator
        generator = MockDataGenerator(config)

        # Clear existing analytics data first
        print("\n🧹 Clearing existing analytics data...")
        analytics_table_name = generator.TABLE_NAMES["analytics"]

        try:
            # Clear only analytics table to avoid disrupting other data
            analytics_table = generator.dynamodb_resource.Table(analytics_table_name)

            # Scan and delete all items in analytics table
            response = analytics_table.scan()
            items = response.get("Items", [])

            if items:
                print(f"🗑️  Deleting {len(items)} existing analytics records...")

                # Delete in batches
                with analytics_table.batch_writer() as batch:
                    for item in items:
                        batch.delete_item(
                            Key={
                                "metric_date": item["metric_date"],
                                "metric_type_timestamp": item["metric_type_timestamp"],
                            }
                        )

                # Handle pagination if there are more items
                while "LastEvaluatedKey" in response:
                    response = analytics_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
                    items = response.get("Items", [])
                    if items:
                        with analytics_table.batch_writer() as batch:
                            for item in items:
                                batch.delete_item(
                                    Key={
                                        "metric_date": item["metric_date"],
                                        "metric_type_timestamp": item["metric_type_timestamp"],
                                    }
                                )

                print("✅ Existing analytics data cleared")
            else:
                print("ℹ️  No existing analytics data to clear")

        except Exception as e:
            print(f"⚠️  Warning: Could not clear existing data: {e}")

        # Generate minimal content and partner data if needed
        print("\n📄 Generating minimal content and partner data...")

        # Check if we have content and partners
        content_table = generator.dynamodb_resource.Table(generator.TABLE_NAMES["content"])
        partner_table = generator.dynamodb_resource.Table(generator.TABLE_NAMES["partners"])

        # Get existing content IDs
        content_response = content_table.scan(Limit=10)
        content_ids = [item["content_id"] for item in content_response.get("Items", [])]

        # Get existing partner IDs
        partner_response = partner_table.scan(Limit=5)
        partner_ids = [item["partner_id"] for item in partner_response.get("Items", [])]

        # Generate minimal data if we don't have enough
        if len(content_ids) < 5:
            print("📝 Generating minimal content data...")
            content_records = generator.generate_content_data()[:5]
            generator.write_records_to_table(content_records, generator.TABLE_NAMES["content"])
            content_ids = [record["content_id"]["S"] for record in content_records]

        if len(partner_ids) < 3:
            print("🤝 Generating minimal partner data...")
            partner_records = generator.generate_partner_data()[:3]
            generator.write_records_to_table(partner_records, generator.TABLE_NAMES["partners"])
            partner_ids = [record["partner_id"]["S"] for record in partner_records]

        print(f"✅ Using {len(content_ids)} content items and {len(partner_ids)} partners")

        # Generate recent analytics data (override date range to last 7 days)
        print("\n📊 Generating recent analytics data...")

        # Temporarily modify the date range to generate recent data
        from datetime import timedelta

        original_date_range = config.date_range_months

        # Generate analytics data for the last 7 days
        analytics_records = []
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)

        # Generate data day by day for the last 7 days
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            print(f"📅 Generating analytics for {date_str}")

            # Create Faker instance for this date
            from faker import Faker

            fake = Faker()

            # Generate bot access metrics for this date
            daily_records = generator._generate_bot_access_metrics(date_str, content_ids, partner_ids, fake, None)

            # Generate API request metrics
            daily_records.extend(generator._generate_api_request_metrics(date_str, fake))

            # Generate content access metrics
            daily_records.extend(generator._generate_content_access_metrics(date_str, content_ids, fake))

            analytics_records.extend(daily_records)
            current_date += timedelta(days=1)

        # Write analytics data to table
        if analytics_records:
            print(f"💾 Writing {len(analytics_records)} analytics records to DynamoDB...")
            generator.batch_write_items_chunked(generator.TABLE_NAMES["analytics"], analytics_records)
            print("✅ Analytics data written successfully")
        else:
            print("⚠️  No analytics records generated")

        print("\n🎉 Recent analytics data generation completed!")
        print(f"📊 Generated {len(analytics_records)} analytics records")
        print(f"📅 Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        print("🔄 You can now refresh the dashboard to see updated bot statistics")

        return True

    except Exception as e:
        print(f"❌ Error generating recent analytics data: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = generate_recent_analytics_data()

    if success:
        print("\n✅ Recent analytics data generation completed successfully!")
    else:
        print("\n❌ Recent analytics data generation failed!")
        sys.exit(1)
