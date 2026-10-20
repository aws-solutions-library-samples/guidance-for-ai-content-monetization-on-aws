#!/bin/bash

# AI Content Monetization API - Stack Redeployment Script
# Use this when you need to redeploy the stack due to resource conflicts

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${BOLD}${BLUE}🔄 AI Content Monetization API - Stack Redeployment${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Configuration
AWS_PROFILE="${AWS_PROFILE:-${1:-genai}}"
AWS_REGION="us-west-2"

echo -e "${BLUE}📋 Redeployment Configuration:${NC}"
echo -e "${BLUE}   AWS Profile: ${AWS_PROFILE}${NC}"
echo -e "${BLUE}   AWS Region: ${AWS_REGION}${NC}"
echo ""

# Verify AWS profile
echo -e "${YELLOW}🔍 Verifying AWS configuration...${NC}"
if ! aws sts get-caller-identity --profile $AWS_PROFILE >/dev/null 2>&1; then
    echo -e "${RED}❌ AWS profile '${AWS_PROFILE}' not configured properly${NC}"
    echo -e "${YELLOW}💡 Run: ${SCRIPT_DIR}/setup-aws-profile.sh $AWS_PROFILE to configure it${NC}"
    exit 1
fi
echo -e "${GREEN}✅ AWS profile verified${NC}"
echo ""

# Warning about data loss
echo -e "${BOLD}${RED}⚠️  WARNING: This will delete and recreate the stack${NC}"
echo -e "${RED}   • All DynamoDB data will be lost${NC}"
echo -e "${RED}   • All S3 content will be lost${NC}"
echo -e "${RED}   • All Lambda functions will be recreated${NC}"
echo ""
read -p "Are you sure you want to continue? (yes/no): " confirm

if [[ $confirm != "yes" ]]; then
    echo -e "${BLUE}❌ Redeployment cancelled${NC}"
    exit 0
fi

echo ""
echo -e "${BOLD}${BLUE}🗑️  STEP 1: Destroying existing stack${NC}"
echo -e "${BLUE}=====================================${NC}"

# Get the parent directory (project root)
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Check if Python virtual environment exists
if [ ! -d "${PROJECT_ROOT}/.venv" ]; then
    echo -e "${YELLOW}📦 Creating Python virtual environment...${NC}"
    (cd "${PROJECT_ROOT}" && python3 -m venv .venv)
fi

# Activate virtual environment
echo -e "${YELLOW}🔧 Activating virtual environment...${NC}"
source "${PROJECT_ROOT}/.venv/bin/activate"

# Install dependencies
echo -e "${YELLOW}📥 Installing Python dependencies...${NC}"
pip install -r "${PROJECT_ROOT}/requirements.txt"

# Destroy the existing stack
echo -e "${YELLOW}🗑️  Destroying ContentMonetizationStack...${NC}"
(cd "${PROJECT_ROOT}" && cdk destroy ContentMonetizationStack --profile $AWS_PROFILE --force)

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Stack destroyed successfully${NC}"
else
    echo -e "${RED}❌ Stack destruction failed${NC}"
    echo -e "${YELLOW}💡 You may need to manually delete resources in AWS Console${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}${BLUE}🚀 STEP 2: Deploying fresh stack${NC}"
echo -e "${BLUE}==================================${NC}"

# Deploy the fresh stack
echo -e "${YELLOW}🚀 Deploying fresh ContentMonetizationStack...${NC}"
(cd "${PROJECT_ROOT}" && cdk deploy ContentMonetizationStack --profile $AWS_PROFILE --require-approval never)

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Stack deployed successfully${NC}"
else
    echo -e "${RED}❌ Stack deployment failed${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}${GREEN}🎉 REDEPLOYMENT COMPLETE!${NC}"
echo -e "${GREEN}=========================${NC}"
echo ""

# Get deployment outputs
echo -e "${BLUE}📋 Deployment Summary:${NC}"

# Get IAB API URL
IAB_API_URL=$(aws cloudformation describe-stacks \
    --stack-name ContentMonetizationStack \
    --profile $AWS_PROFILE \
    --region $AWS_REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiGatewayUrl`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

echo -e "${GREEN}🔗 IAB API: ${IAB_API_URL}${NC}"

echo ""
echo -e "${BLUE}🎯 Next Steps:${NC}"
echo -e "${BLUE}• Add sample data: python scripts/add-sample-partners.py${NC}"
echo -e "${BLUE}• Test API endpoints: python tests/test_api.py ${IAB_API_URL}${NC}"
echo -e "${BLUE}• Deploy UI: ./scripts/deploy-ui.sh demo $AWS_PROFILE us-west-2${NC}"
echo ""
echo -e "${YELLOW}💡 For troubleshooting, see ../internal-assets/DEPLOYMENT_GUIDE.md${NC}"