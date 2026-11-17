# Licensing Tables Setup Scripts

Quick reference for creating and managing DynamoDB tables for CoMP Licensing and Pricing Management.

## Quick Start

```bash
# Create tables with default data
python3 create_licensing_tables.py --profile my-profile --region us-west-2
```

## Usage Examples

### Basic Creation
```bash
# Create both tables
python3 create_licensing_tables.py --profile my-profile --region us-west-2
```

### Without Default Data
```bash
# Skip creating default license and pricing rules
python3 create_licensing_tables.py --profile my-profile --region us-west-2 --skip-defaults
```

### Recreate Tables
```bash
# Delete existing tables and recreate
python3 create_licensing_tables.py --profile my-profile --region us-west-2 --delete
```

### View Information Only
```bash
# Display table info without creating
python3 create_licensing_tables.py --profile my-profile --region us-west-2 --info-only
```

### Custom Table Names
```bash
# Use custom names instead of defaults
python3 create_licensing_tables.py \
  --profile my-profile \
  --region us-west-2 \
  --licenses-table MyLicensesTable \
  --pricing-table MyPricingTable
```

## What Gets Created

### Tables
1. **LicensesTable** - RSL license documents
   - Key: (license_id, version)
   - GSI: StatusIndex, CreatedByIndex
   - Capacity: 10 RCU / 10 WCU (auto-scales to 100)

2. **PricingRulesTable** - Pricing rules
   - Key: (rule_set_id, version)
   - GSI: StatusIndex, EffectiveDateIndex
   - Capacity: 10 RCU / 10 WCU (auto-scales to 100)

### Default Data

**Default License** (`lic_default_v1`):
- Allows: Browsing (1) and Inference (3)
- Attribution required (text format)
- $0.01 per query

**Default Pricing Rules** (`default` v1):
- Base: $0.01/query
- Multipliers for content types, categories, usage types
- Volume discounts at 1K, 10K, 100K queries

## Output Example

```
============================================================
DynamoDB Table Creation for Content Monetization API
============================================================
Profile: my-profile
Region: us-west-2
Licenses Table: LicensesTable
Pricing Table: PricingRulesTable
============================================================

============================================================
Creating Tables
============================================================

Creating table: LicensesTable
Waiting for LicensesTable to be created...
✓ Table LicensesTable created successfully
  - Partition Key: license_id (String)
  - Sort Key: version (Number)
  - GSI: StatusIndex (status, updated_at)
  - GSI: CreatedByIndex (created_by, created_at)
  - Stream: Enabled (NEW_AND_OLD_IMAGES)

Creating table: PricingRulesTable
Waiting for PricingRulesTable to be created...
✓ Table PricingRulesTable created successfully
  - Partition Key: rule_set_id (String)
  - Sort Key: version (Number)
  - GSI: StatusIndex (status, created_at)
  - GSI: EffectiveDateIndex (rule_set_id, effective_date)
  - Stream: Enabled (NEW_AND_OLD_IMAGES)

============================================================
Creating Default Data
============================================================

Creating default license...
✓ Default license created: lic_default_v1

Creating default pricing rules...
✓ Default pricing rules created: default v1

============================================================
✓ All tables created successfully!
============================================================
```

## Troubleshooting

### Permission Denied
```
Error: An error occurred (AccessDeniedException)
```
**Solution:** Ensure AWS profile has DynamoDB create permissions

### Table Already Exists
```
⚠ Table LicensesTable already exists
```
**Solution:** Use `--delete` flag to recreate or `--info-only` to view

### Profile Not Found
```
Error: The config profile (my-profile) could not be found
```
**Solution:** Configure AWS profile or use different profile name

## Verification

After creation, verify tables:

```bash
# List tables
aws dynamodb list-tables --profile my-profile --region us-west-2

# Get default license
aws dynamodb get-item \
  --table-name LicensesTable \
  --key '{"license_id": {"S": "lic_default_v1"}, "version": {"N": "1"}}' \
  --profile my-profile \
  --region us-west-2 \
  | jq .Item
```

## Related Files

- **CDK Stack:** `iab_llm_ingest/licensing_tables_stack.py`
- **Documentation:** `docs/DYNAMODB_TABLES_SETUP.md`
- **Lambda Functions:**
  - `lambda/comp_licensing.py`
  - `lambda/pricing_management.py`

## Help

```bash
python3 create_licensing_tables.py --help
```

## Complete Documentation

See `docs/DYNAMODB_TABLES_SETUP.md` for:
- Complete table schema details
- CDK deployment instructions
- IAM permissions
- Cost estimation
- Monitoring and alarms
- Backup and recovery procedures
