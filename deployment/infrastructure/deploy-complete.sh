#!/bin/bash

# AI Content Monetization API - Complete Deployment Script
# This is the MAIN deployment script - use this for complete deployment

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get the parent directory (project root)
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo -e "${BOLD}${BLUE}🚀 AI Content Monetization API - Complete Deployment${NC}"
echo -e "${BLUE}===============================================${NC}"
echo ""

# Configuration
AWS_PROFILE="${AWS_PROFILE:-${1:-genai}}"
AWS_REGION="us-west-2"
EMAIL_ID=${EMAIL_ID:-"demo-user@example.com"}
FIRST_NAME=${FIRST_NAME:-"Demo"}
LAST_NAME=${LAST_NAME:-"User"}

echo -e "${BLUE}📋 Deployment Configuration:${NC}"
echo -e "${BLUE}   AWS Profile: ${AWS_PROFILE}${NC}"
echo -e "${BLUE}   AWS Region: ${AWS_REGION}${NC}"
echo -e "${BLUE}   Email: ${EMAIL_ID}${NC}"
echo ""

# Verify AWS profile using existing utility
echo -e "${YELLOW}🔍 Verifying AWS configuration...${NC}"
if ! aws sts get-caller-identity --profile $AWS_PROFILE >/dev/null 2>&1; then
    echo -e "${RED}❌ AWS profile '${AWS_PROFILE}' not configured properly${NC}"
    echo -e "${YELLOW}💡 Run: ${SCRIPT_DIR}/setup-aws-profile.sh $AWS_PROFILE to configure it${NC}"
    echo -e "${YELLOW}💡 Or run: ${SCRIPT_DIR}/check-aws-config.sh for detailed diagnosis${NC}"
    exit 1
fi
echo -e "${GREEN}✅ AWS profile verified${NC}"
echo ""

# Ask about MediaLake deployment
echo -e "${BOLD}${YELLOW}❓ Do you want to deploy MediaLake for WordPress integration?${NC}"
echo -e "${BLUE}   MediaLake provides:${NC}"
echo -e "${BLUE}   • Shared Cognito authentication${NC}"
echo -e "${BLUE}   • WordPress connector UI${NC}"
echo -e "${BLUE}   • Content management interface${NC}"
echo -e "${BLUE}   • Takes ~30 minutes to deploy${NC}"
echo ""
read -p "Deploy MediaLake? (y/n): " deploy_medialake

if [[ $deploy_medialake == "y" || $deploy_medialake == "Y" ]]; then
    echo ""
    echo -e "${BOLD}${BLUE}🌊 STEP 1: Deploying MediaLake${NC}"
    echo -e "${BLUE}================================${NC}"
    
    # Deploy MediaLake CloudFormation stack
    echo -e "${YELLOW}📦 Deploying MediaLake CloudFormation stack...${NC}"
    
    "${SCRIPT_DIR}/download_deploy_cdk_stack.sh" \
        -s "IABMediaLakeStack" \
        -e "$EMAIL_ID" \
        -f "$FIRST_NAME" \
        -l "$LAST_NAME" \
        -t "medialake.template"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ MediaLake CloudFormation stack deployed${NC}"
        
        # Wait for CodePipeline and extract configuration
        echo -e "${YELLOW}⏳ Waiting for MediaLake CodePipeline and extracting configuration...${NC}"
        "${SCRIPT_DIR}/update-ui-with-medialake-config.sh" $AWS_PROFILE $AWS_REGION
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ MediaLake deployment completed and IAB UI configured${NC}"
        else
            echo -e "${YELLOW}⚠️ MediaLake deployed but UI configuration failed${NC}"
            echo -e "${YELLOW}💡 You can run './scripts/sync-medialake-config.sh' later${NC}"
        fi
    else
        echo -e "${RED}❌ MediaLake deployment failed${NC}"
        echo -e "${YELLOW}💡 Continuing with IAB deployment without MediaLake...${NC}"
    fi
else
    echo -e "${BLUE}⏭️ Skipping MediaLake deployment${NC}"
fi

echo ""
echo -e "${BOLD}${BLUE}🏗️ STEP 2: Deploying AI Content Monetization API${NC}"
echo -e "${BLUE}========================================${NC}"

# Debug: Show what profile and region we're using
echo -e "${BLUE}🔍 Using AWS Profile: ${AWS_PROFILE}${NC}"
echo -e "${BLUE}🔍 Using AWS Region: ${AWS_REGION}${NC}"

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

# Deploy the main stack (run from root directory)
echo -e "${YELLOW}🚀 Deploying AI Content Monetization API stack...${NC}"
# without multi line profile is not picked up
(cd "${PROJECT_ROOT}" && cdk deploy ContentMonetizationStack \
                                --profile "${AWS_PROFILE}" \
                                --require-approval never)

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ AI Content Monetization API deployed successfully${NC}"
else
    echo -e "${RED}❌ AI Content Monetization API deployment failed${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}${BLUE}🎨 STEP 3: Deploying IAB UI${NC}"
echo -e "${BLUE}============================${NC}"

# Deploy UI
echo -e "${YELLOW}🎨 Deploying IAB UI with demo mode...${NC}"
"${SCRIPT_DIR}/deploy-ui.sh" demo $AWS_PROFILE $AWS_REGION

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ IAB UI deployed successfully${NC}"
    
    # Fix Cognito URLs for proper authentication
    echo -e "${YELLOW}🔧 Configuring Cognito authentication...${NC}"
    "${SCRIPT_DIR}/fix-cognito-urls.sh" $AWS_PROFILE $AWS_REGION
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Cognito authentication configured${NC}"
    else
        echo -e "${YELLOW}⚠️ Cognito configuration failed - authentication may not work properly${NC}"
    fi
else
    echo -e "${RED}❌ IAB UI deployment failed${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}${GREEN}🎉 DEPLOYMENT COMPLETE!${NC}"
echo -e "${GREEN}========================${NC}"
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

# Get IAB UI URL
IAB_UI_URL=$(aws cloudformation describe-stacks \
    --stack-name CMUIStack \
    --profile $AWS_PROFILE \
    --region $AWS_REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`UIUrl`].OutputValue' \
    --output text 2>/dev/null || echo "Not available")

# Get MediaLake UI URL (if deployed)
if [[ $deploy_medialake == "y" || $deploy_medialake == "Y" ]]; then
    MEDIALAKE_UI_URL=$(grep VITE_MEDIALAKE_UI_URL ../ui/.env | cut -d'=' -f2 2>/dev/null || echo "Not available")
    echo -e "${GREEN}🌊 MediaLake UI: ${MEDIALAKE_UI_URL}${NC}"
fi

echo -e "${GREEN}🔗 IAB API: ${IAB_API_URL}${NC}"
echo -e "${GREEN}🎨 IAB UI: ${IAB_UI_URL}${NC}"

echo ""
echo -e "${BLUE}🎯 Next Steps:${NC}"
echo -e "${BLUE}• Access the IAB UI to test the WordPress integration${NC}"
echo -e "${BLUE}• Check the WordPress Integration menu for MediaLake options${NC}"
echo -e "${BLUE}• Test API endpoints using the provided API URL${NC}"
echo ""
echo -e "${YELLOW}💡 For troubleshooting, see internal-assets/DEPLOYMENT_GUIDE.md${NC}"