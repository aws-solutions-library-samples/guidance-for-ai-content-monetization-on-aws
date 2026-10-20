#!/usr/bin/env python3
"""
Create DynamoDB tables for CoMP Licensing and Pricing Management

This script creates the following tables:
1. LicensesTable - Stores RSL license documents with versioning
2. PricingRulesTable - Stores pricing rules with version history

Usage:
    python3 scripts/create_licensing_tables.py --profile genai --region us-west-2
    python3 scripts/create_licensing_tables.py --profile genai --region us-west-2 --delete  # Delete tables first
"""

import argparse
import sys
import time

import boto3
from botocore.exceptions import ClientError


def create_licenses_table(dynamodb, table_name: str, delete_existing: bool = False):
    """
    Create the LicensesTable with composite key and GSIs.
    
    Schema:
        PK: license_id (String)
        SK: version (Number)
        GSIs: StatusIndex, CreatedByIndex
    """
    
    try:
        # Delete existing table if requested
        if delete_existing:
            try:
                print(f"Checking if {table_name} exists...")
                dynamodb.Table(table_name).load()
                print(f"Deleting existing table: {table_name}")
                dynamodb.Table(table_name).delete()
                print(f"Waiting for {table_name} to be deleted...")
                waiter = dynamodb.meta.client.get_waiter('table_not_exists')
                waiter.wait(TableName=table_name)
                print(f"✓ {table_name} deleted")
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
                print(f"Table {table_name} does not exist, skipping deletion")
        
        print(f"\nCreating table: {table_name}")
        
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': 'license_id',
                    'KeyType': 'HASH'  # Partition key
                },
                {
                    'AttributeName': 'version',
                    'KeyType': 'RANGE'  # Sort key
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'license_id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'version',
                    'AttributeType': 'N'
                },
                {
                    'AttributeName': 'status',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'updated_at',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'created_by',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'created_at',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'StatusIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'status',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'updated_at',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                },
                {
                    'IndexName': 'CreatedByIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'created_by',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'created_at',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            BillingMode='PROVISIONED',
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10
            },
            StreamSpecification={
                'StreamEnabled': True,
                'StreamViewType': 'NEW_AND_OLD_IMAGES'
            },
            Tags=[
                {
                    'Key': 'Environment',
                    'Value': 'production'
                },
                {
                    'Key': 'Purpose',
                    'Value': 'CoMP-RSL-Licensing'
                },
                {
                    'Key': 'ManagedBy',
                    'Value': 'AI-Content-Monetization'
                }
            ]
        )
        
        print(f"Waiting for {table_name} to be created...")
        table.wait_until_exists()
        
        print(f"✓ Table {table_name} created successfully")
        print("  - Partition Key: license_id (String)")
        print("  - Sort Key: version (Number)")
        print("  - GSI: StatusIndex (status, updated_at)")
        print("  - GSI: CreatedByIndex (created_by, created_at)")
        print("  - Stream: Enabled (NEW_AND_OLD_IMAGES)")
        
        return table
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"⚠ Table {table_name} already exists")
            return dynamodb.Table(table_name)
        else:
            print(f"✗ Error creating {table_name}: {e}")
            raise


def create_pricing_rules_table(dynamodb, table_name: str, delete_existing: bool = False):
    """
    Create the PricingRulesTable with composite key and GSIs.
    
    Schema:
        PK: rule_set_id (String)
        SK: version (Number)
        GSIs: StatusIndex, EffectiveDateIndex
    """
    
    try:
        # Delete existing table if requested
        if delete_existing:
            try:
                print(f"Checking if {table_name} exists...")
                dynamodb.Table(table_name).load()
                print(f"Deleting existing table: {table_name}")
                dynamodb.Table(table_name).delete()
                print(f"Waiting for {table_name} to be deleted...")
                waiter = dynamodb.meta.client.get_waiter('table_not_exists')
                waiter.wait(TableName=table_name)
                print(f"✓ {table_name} deleted")
            except ClientError as e:
                if e.response['Error']['Code'] != 'ResourceNotFoundException':
                    raise
                print(f"Table {table_name} does not exist, skipping deletion")
        
        print(f"\nCreating table: {table_name}")
        
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {
                    'AttributeName': 'rule_set_id',
                    'KeyType': 'HASH'  # Partition key
                },
                {
                    'AttributeName': 'version',
                    'KeyType': 'RANGE'  # Sort key
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'rule_set_id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'version',
                    'AttributeType': 'N'
                },
                {
                    'AttributeName': 'status',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'effective_date',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'created_at',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'StatusIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'status',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'created_at',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                },
                {
                    'IndexName': 'EffectiveDateIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'rule_set_id',
                            'KeyType': 'HASH'
                        },
                        {
                            'AttributeName': 'effective_date',
                            'KeyType': 'RANGE'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            BillingMode='PROVISIONED',
            ProvisionedThroughput={
                'ReadCapacityUnits': 10,
                'WriteCapacityUnits': 10
            },
            StreamSpecification={
                'StreamEnabled': True,
                'StreamViewType': 'NEW_AND_OLD_IMAGES'
            },
            Tags=[
                {
                    'Key': 'Environment',
                    'Value': 'production'
                },
                {
                    'Key': 'Purpose',
                    'Value': 'Pricing-Management'
                },
                {
                    'Key': 'ManagedBy',
                    'Value': 'AI-Content-Monetization'
                }
            ]
        )
        
        print(f"Waiting for {table_name} to be created...")
        table.wait_until_exists()
        
        print(f"✓ Table {table_name} created successfully")
        print("  - Partition Key: rule_set_id (String)")
        print("  - Sort Key: version (Number)")
        print("  - GSI: StatusIndex (status, created_at)")
        print("  - GSI: EffectiveDateIndex (rule_set_id, effective_date)")
        print("  - Stream: Enabled (NEW_AND_OLD_IMAGES)")
        
        return table
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            print(f"⚠ Table {table_name} already exists")
            return dynamodb.Table(table_name)
        else:
            print(f"✗ Error creating {table_name}: {e}")
            raise


def create_default_license(table):
    """Create a default license for testing."""
    
    print("\nCreating default license...")
    
    from datetime import datetime, timezone
    from decimal import Decimal
    
    default_license = {
        'license_id': 'lic_default_v1',
        'version': 1,
        'status': 'active',
        'title': 'Default Public License',
        'description': 'Default license for public content access',
        'allowed_uses': [1, 3],  # Browsing and Inference
        'allowed_subfunctions': [0, 1, 3, 5],  # General, text gen, summarization, Q&A
        'restrictions': {
            'max_queries_per_day': 10000,
            'commercial_use': True,
            'attribution_required': True
        },
        'attribution': {
            'required': True,
            'format': 'text',
            'text': 'Content provided by {publisher_name}. Visit {content_url} for more information.',
            'url': 'https://publisher.com/attribution'
        },
        'pricing': {
            'model': 'per_query',
            'base_rate': Decimal('0.01'),  # Use Decimal for DynamoDB
            'currency': 'USD'
        },
        'effective_date': datetime.now(timezone.utc).isoformat(),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'created_by': 'system',
        'updated_by': 'system',
        'metadata': {
            'description': 'System-generated default license'
        }
    }
    
    try:
        table.put_item(Item=default_license)
        print(f"✓ Default license created: {default_license['license_id']}")
        return default_license
    except ClientError as e:
        print(f"✗ Error creating default license: {e}")
        return None


def create_default_pricing_rules(table):
    """Create default pricing rules for testing."""
    
    print("\nCreating default pricing rules...")
    
    from datetime import datetime, timezone
    from decimal import Decimal
    
    default_rules = {
        'rule_set_id': 'default',
        'version': 1,
        'status': 'active',
        'effective_date': datetime.now(timezone.utc).isoformat(),
        'rules': {
            'base_price_per_query': Decimal('0.01'),
            'content_type_multipliers': {
                'article': Decimal('1.0'),
                'video': Decimal('2.5'),
                'research_paper': Decimal('5.0'),
                'image': Decimal('1.5'),
                'audio': Decimal('2.0')
            },
            'category_multipliers': {
                '111': Decimal('2.0'),  # Health & Medical
                '234': Decimal('1.5'),  # Business & Finance
            },
            'usage_type_multipliers': {
                'inference': Decimal('1.0'),
                'training': Decimal('3.0'),
                'fine_tuning': Decimal('4.0')
            },
            'volume_discounts': [
                {
                    'min_queries': 1000,
                    'discount': Decimal('0.1')
                },
                {
                    'min_queries': 10000,
                    'discount': Decimal('0.2')
                },
                {
                    'min_queries': 100000,
                    'discount': Decimal('0.3')
                }
            ]
        },
        'created_at': datetime.now(timezone.utc).isoformat(),
        'created_by': 'system',
        'metadata': {
            'description': 'System-generated default pricing rules'
        }
    }
    
    try:
        table.put_item(Item=default_rules)
        print(f"✓ Default pricing rules created: {default_rules['rule_set_id']} v{default_rules['version']}")
        return default_rules
    except ClientError as e:
        print(f"✗ Error creating default pricing rules: {e}")
        return None


def list_table_info(dynamodb, table_name: str):
    """Display detailed information about a table."""
    
    try:
        table = dynamodb.Table(table_name)
        table.load()
        
        print(f"\n{'='*60}")
        print(f"Table: {table_name}")
        print(f"{'='*60}")
        print(f"Status: {table.table_status}")
        print(f"Item Count: {table.item_count}")
        print(f"Size: {table.table_size_bytes / 1024:.2f} KB")
        print(f"ARN: {table.table_arn}")
        
        print("\nKey Schema:")
        for key in table.key_schema:
            print(f"  - {key['AttributeName']} ({key['KeyType']})")
        
        if table.global_secondary_indexes:
            print("\nGlobal Secondary Indexes:")
            for gsi in table.global_secondary_indexes:
                print(f"  - {gsi['IndexName']}")
                for key in gsi['KeySchema']:
                    print(f"    - {key['AttributeName']} ({key['KeyType']})")
        
        if table.stream_specification:
            print("\nStream:")
            print(f"  - Enabled: {table.stream_specification['StreamEnabled']}")
            print(f"  - View Type: {table.stream_specification.get('StreamViewType', 'N/A')}")
        
    except ClientError as e:
        print(f"✗ Error loading table info for {table_name}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Create DynamoDB tables for CoMP Licensing and Pricing Management'
    )
    parser.add_argument(
        '--profile',
        default='genai',
        help='AWS profile name (default: genai)'
    )
    parser.add_argument(
        '--region',
        default='us-east-1',
        help='AWS region (default: us-east-1)'
    )
    parser.add_argument(
        '--licenses-table',
        default='LicensesTable',
        help='Licenses table name (default: LicensesTable)'
    )
    parser.add_argument(
        '--pricing-table',
        default='PricingRulesTable',
        help='Pricing rules table name (default: PricingRulesTable)'
    )
    parser.add_argument(
        '--delete',
        action='store_true',
        help='Delete existing tables before creating new ones'
    )
    parser.add_argument(
        '--skip-defaults',
        action='store_true',
        help='Skip creating default license and pricing rules'
    )
    parser.add_argument(
        '--info-only',
        action='store_true',
        help='Only display table information, do not create'
    )
    
    args = parser.parse_args()
    
    print("="*60)
    print("DynamoDB Table Creation for Content Monetization API")
    print("="*60)
    print(f"Profile: {args.profile}")
    print(f"Region: {args.region}")
    print(f"Licenses Table: {args.licenses_table}")
    print(f"Pricing Table: {args.pricing_table}")
    if args.delete:
        print("⚠ DELETE MODE: Existing tables will be deleted!")
    print("="*60)
    
    try:
        # Initialize boto3 session
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        dynamodb = session.resource('dynamodb')
        
        if args.info_only:
            # Just display table info
            list_table_info(dynamodb, args.licenses_table)
            list_table_info(dynamodb, args.pricing_table)
            return 0
        
        # Create tables
        print("\n" + "="*60)
        print("Creating Tables")
        print("="*60)
        
        licenses_table = create_licenses_table(
            dynamodb,
            args.licenses_table,
            delete_existing=args.delete
        )
        
        pricing_table = create_pricing_rules_table(
            dynamodb,
            args.pricing_table,
            delete_existing=args.delete
        )
        
        # Create default data
        if not args.skip_defaults:
            print("\n" + "="*60)
            print("Creating Default Data")
            print("="*60)
            
            create_default_license(licenses_table)
            create_default_pricing_rules(pricing_table)
        
        # Display table info
        print("\n" + "="*60)
        print("Table Information")
        print("="*60)
        
        list_table_info(dynamodb, args.licenses_table)
        list_table_info(dynamodb, args.pricing_table)
        
        print("\n" + "="*60)
        print("✓ All tables created successfully!")
        print("="*60)
        
        return 0
        
    except ClientError as e:
        print(f"\n✗ AWS Error: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
