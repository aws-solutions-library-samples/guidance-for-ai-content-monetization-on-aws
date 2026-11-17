#!/bin/bash
set -e

# Function to display usage
usage() {
    echo "Usage: $0 -r <repo URL> -t <template file path> -s <stack name> -f <first_name> -l <last_name> -e <email>"
    echo "  -r: Repository URL (default: https://github.com/aws-solutions-library-samples/guidance-for-medialake-on-aws)"
    echo "  -t: Template file path (default: ./medialake.template)"
    echo "  -s: Stack name (default: MediaLakeStack)"
    echo "  -f: First name of the initial user (default: Demo)"
    echo "  -l: Last name of the initial user (default: User)"
    echo "  -e: Initial user email (default: demo-user@example.com)"
    exit 1
}

# Function to check command status
check_status() {
    if [ $? -ne 0 ]; then
        echo "Error: $1"
        exit 1
    fi
}

# Function to check if required commands exist
check_requirements() {
    command -v curl >/dev/null 2>&1 || { echo "Error: curl is required but not installed"; exit 1; }
    command -v aws >/dev/null 2>&1 || { echo "Error: aws cli is required but not installed"; exit 1; }
}

# Function to validate AWS credentials
validate_aws_credentials() {
    echo "Validating AWS credentials..."
    aws sts get-caller-identity >/dev/null 2>&1
    check_status "Invalid AWS credentials. Please configure your AWS credentials"
}

# Set default values
REPO_URL="https://raw.githubusercontent.com/aws-solutions-library-samples/guidance-for-medialake-on-aws/refs/heads/main/"
TEMPLATE_FILE="medialake.template"
STACK_NAME="IABMediaLakeStack"
EMAIL="demo-user@example.com"
FIRST_NAME="Demo"
LAST_NAME="User"

# Parse command line arguments
while getopts "r:t:s:f:l:e:" opt; do
    case $opt in
        r) REPO_URL="$OPTARG";;
        t) TEMPLATE_FILE="$OPTARG";;
        s) STACK_NAME="$OPTARG";;
        f) FIRST_NAME="$OPTARG";;
        l) LAST_NAME="$OPTARG";;
        e) EMAIL="$OPTARG";;
        ?) usage;;
    esac
done

# Check requirements
check_requirements

# Validate AWS credentials
validate_aws_credentials

# Get AWS account ID and region
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
AWS_REGION=$(aws configure get region)

# Ensure we have a region
if [ -z "$AWS_REGION" ]; then
    AWS_REGION="us-west-2"
    echo "⚠️ No region configured, defaulting to: $AWS_REGION"
fi

echo "🔍 Using AWS Account: $AWS_ACCOUNT_ID in Region: $AWS_REGION"

# Download the template file directly
# Format the URL correctly for raw GitHub content
if [[ "$REPO_URL" == *"github.com"* ]]; then
    # Convert github.com URL to raw.githubusercontent.com format
    RAW_TEMPLATE_URL="${REPO_URL/github.com/raw.githubusercontent.com}/main/${TEMPLATE_FILE}"
else
    # Use as is if it's already a raw URL
    RAW_TEMPLATE_URL="${REPO_URL}/${TEMPLATE_FILE}"
fi

echo "📥 Downloading MediaLake template file ${RAW_TEMPLATE_URL}..."
TEMPLATE_PATH="templates/${TEMPLATE_FILE}"
# Disabling because the we have edited the template file locally and keeping it. Change it when issues are fixed in media lake to support multi region deployment
# curl -L -o "${TEMPLATE_PATH}" "$RAW_TEMPLATE_URL"
# check_status "Failed to download template file from $RAW_TEMPLATE_URL"
echo "✅ Template file downloaded to ${TEMPLATE_PATH}"

# Deploy the CloudFormation stack using the template
echo "🚀 Deploying MediaLake stack: $STACK_NAME..."

# Deploy the stack with direct parameter overrides
aws cloudformation deploy \
  --template-file "$TEMPLATE_PATH" \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
    InitialUserEmail="$EMAIL" \
    InitialUserFirstName="$FIRST_NAME" \
    InitialUserLastName="$LAST_NAME" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --no-fail-on-empty-changeset

check_status "Failed to deploy CloudFormation stack"

echo "✅ Deployment completed successfully!"

# Show deployed stack outputs
echo "📊 Stack outputs:"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output table
