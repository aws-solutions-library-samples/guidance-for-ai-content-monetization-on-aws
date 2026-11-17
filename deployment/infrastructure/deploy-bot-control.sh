#!/bin/bash

# Deploy IAB Bot Control Stack (Standalone)
# Usage: ./deploy-bot-control.sh [options]

set -e

# Default values
INSPECTION_LEVEL="COMMON"
ENABLE_GEO_RESTRICTIONS="false"
LOG_RETENTION_DAYS="30"
API_RATE_LIMIT="1000"
REGION=""
PROFILE=""
AUTO_ASSOCIATE_API_GATEWAY="false"
API_GATEWAY_STAGE_ARN=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Deploy the IAB Bot Control Stack independently.

OPTIONS:
    -i, --inspection-level LEVEL    Bot Control inspection level (COMMON|TARGETED) [default: COMMON]
    -g, --enable-geo-restrictions   Enable geographic restrictions [default: false]
    -l, --log-retention DAYS        CloudWatch log retention in days [default: 30]
    -r, --rate-limit LIMIT          API rate limit per 5 minutes per IP [default: 1000]
    --region REGION                 AWS region to deploy to
    --profile PROFILE               AWS profile to use
    --auto-associate-api-gateway    Automatically find and associate with existing API Gateway
    --api-gateway-stage-arn ARN     Specific API Gateway stage ARN to associate with
    -h, --help                      Show this help message

EXAMPLES:
    # Basic deployment with default settings
    $0

    # Deploy with enhanced bot detection
    $0 --inspection-level TARGETED

    # Deploy with geographic restrictions enabled
    $0 --enable-geo-restrictions

    # Deploy to specific region with custom profile
    $0 --region us-east-1 --profile production

    # Deploy with custom rate limiting
    $0 --rate-limit 500 --log-retention 90

INSPECTION LEVELS:
    COMMON    - Standard bot detection (lower cost, good for most use cases)
    TARGETED  - Enhanced ML-based detection (higher cost, more accurate)

NOTES:
    - The stack will create a WebACL that can be associated with:
      * API Gateway REST APIs
      * Application Load Balancers
      * CloudFront distributions
    - Geographic restrictions can be customized by editing the stack code
    - Rate limiting applies to requests starting with '/api/'
    - All blocked bots are logged to CloudWatch for analysis

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--inspection-level)
            INSPECTION_LEVEL="$2"
            if [[ "$INSPECTION_LEVEL" != "COMMON" && "$INSPECTION_LEVEL" != "TARGETED" ]]; then
                print_error "Invalid inspection level. Must be COMMON or TARGETED."
                exit 1
            fi
            shift 2
            ;;
        -g|--enable-geo-restrictions)
            ENABLE_GEO_RESTRICTIONS="true"
            shift
            ;;
        -l|--log-retention)
            LOG_RETENTION_DAYS="$2"
            if ! [[ "$LOG_RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
                print_error "Log retention days must be a number."
                exit 1
            fi
            shift 2
            ;;
        -r|--rate-limit)
            API_RATE_LIMIT="$2"
            if ! [[ "$API_RATE_LIMIT" =~ ^[0-9]+$ ]]; then
                print_error "Rate limit must be a number."
                exit 1
            fi
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --auto-associate-api-gateway)
            AUTO_ASSOCIATE_API_GATEWAY="true"
            shift
            ;;
        --api-gateway-stage-arn)
            API_GATEWAY_STAGE_ARN="$2"
            shift 2
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

print_status "🤖 Deploying IAB Bot Control Stack..."
echo ""

# Auto-detect API Gateway if requested
if [[ "$AUTO_ASSOCIATE_API_GATEWAY" == "true" && -z "$API_GATEWAY_STAGE_ARN" ]]; then
    print_status "🔍 Auto-detecting existing API Gateway..."
    
    # Look for AI Content Monetization API Gateway
    API_ID=$(aws apigateway get-rest-apis \
        --query 'items[?contains(name, `IAB`) || contains(name, `LLM`) || contains(name, `Ingest`)].id' \
        --output text \
        ${AWS_PARAMS} 2>/dev/null | head -1)
    
    if [[ -n "$API_ID" && "$API_ID" != "None" ]]; then
        # Get the stage name (usually 'v1' or 'prod')
        STAGE_NAME=$(aws apigateway get-stages \
            --rest-api-id "$API_ID" \
            --query 'item[0].stageName' \
            --output text \
            ${AWS_PARAMS} 2>/dev/null)
        
        if [[ -n "$STAGE_NAME" && "$STAGE_NAME" != "None" ]]; then
            DETECTED_REGION=${REGION:-$(aws configure get region ${PROFILE:+--profile $PROFILE} 2>/dev/null || echo "us-west-2")}
            API_GATEWAY_STAGE_ARN="arn:aws:apigateway:${DETECTED_REGION}::/restapis/${API_ID}/stages/${STAGE_NAME}"
            print_success "✅ Found API Gateway: $API_ID (stage: $STAGE_NAME)"
        else
            print_warning "⚠️ Found API Gateway $API_ID but no stages found"
        fi
    else
        print_warning "⚠️ No AI Content Monetization API Gateway found for auto-association"
    fi
fi

print_status "Configuration:"
echo "  • Inspection Level: $INSPECTION_LEVEL"
echo "  • Geographic Restrictions: $ENABLE_GEO_RESTRICTIONS"
echo "  • Log Retention: $LOG_RETENTION_DAYS days"
echo "  • API Rate Limit: $API_RATE_LIMIT requests/5min/IP"
if [[ -n "$REGION" ]]; then
    echo "  • Region: $REGION"
fi
if [[ -n "$PROFILE" ]]; then
    echo "  • AWS Profile: $PROFILE"
fi
if [[ -n "$API_GATEWAY_STAGE_ARN" ]]; then
    echo "  • API Gateway Association: $API_GATEWAY_STAGE_ARN"
fi
echo ""

# Check if AWS CLI is configured
if ! aws sts get-caller-identity ${PROFILE:+--profile $PROFILE} > /dev/null 2>&1; then
    print_error "AWS CLI is not configured or credentials are invalid"
    exit 1
fi

# Check if CDK is installed
if ! command -v cdk &> /dev/null; then
    print_error "AWS CDK is not installed. Please install it with: npm install -g aws-cdk"
    exit 1
fi

# Check if Python dependencies are installed
if ! python3 -c "import aws_cdk" 2>/dev/null; then
    print_warning "AWS CDK Python dependencies not found. Installing..."
    pip3 install -r requirements.txt
fi

# Build CDK context parameters
CDK_CONTEXT=""
if [[ -n "$REGION" ]]; then
    CDK_CONTEXT="$CDK_CONTEXT --context region=$REGION"
fi

# Build AWS CLI parameters
AWS_PARAMS=""
if [[ -n "$PROFILE" ]]; then
    AWS_PARAMS="$AWS_PARAMS --profile $PROFILE"
fi
if [[ -n "$REGION" ]]; then
    AWS_PARAMS="$AWS_PARAMS --region $REGION"
fi

# Bootstrap CDK if needed
print_status "Checking CDK bootstrap status..."
if ! cdk bootstrap ${CDK_CONTEXT} --app "python3 scripts/bot_control_app.py" ${AWS_PARAMS} 2>/dev/null; then
    print_warning "CDK bootstrap required. Bootstrapping..."
    cdk bootstrap ${CDK_CONTEXT} --app "python3 scripts/bot_control_app.py" ${AWS_PARAMS}
fi

# Deploy the stack
print_status "Deploying Bot Control Stack..."
CDK_PARAMS="--app python3 scripts/bot_control_app.py"
CDK_PARAMS="$CDK_PARAMS --parameters InspectionLevel=$INSPECTION_LEVEL"
CDK_PARAMS="$CDK_PARAMS --parameters EnableGeographicRestrictions=$ENABLE_GEO_RESTRICTIONS"
CDK_PARAMS="$CDK_PARAMS --parameters LogRetentionDays=$LOG_RETENTION_DAYS"
CDK_PARAMS="$CDK_PARAMS --parameters ApiRateLimit=$API_RATE_LIMIT"

# Add API Gateway stage ARN if provided
if [[ -n "$API_GATEWAY_STAGE_ARN" ]]; then
    CDK_PARAMS="$CDK_PARAMS --parameters ApiGatewayStageArn=$API_GATEWAY_STAGE_ARN"
fi

cdk deploy BotControlStack \
    $CDK_PARAMS \
    --require-approval never \
    ${CDK_CONTEXT} \
    ${AWS_PARAMS}

if [ $? -eq 0 ]; then
    print_success "✅ Bot Control Stack deployed successfully!"
    echo ""
    
    # Get stack outputs
    print_status "📊 Stack Outputs:"
    aws cloudformation describe-stacks \
        --stack-name BotControlStack \
        --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue,Description]' \
        --output table \
        ${AWS_PARAMS}
    
    echo ""
    print_status "🔗 Next Steps:"
    echo ""
    echo "1. Associate the WebACL with your resources:"
    echo "   • API Gateway: Use the WebACL ARN in your API Gateway stage configuration"
    echo "   • Load Balancer: Associate via AWS Console or CLI"
    echo "   • CloudFront: Associate via AWS Console (requires us-east-1 region)"
    echo ""
    echo "2. Monitor bot activity:"
    echo "   • View the CloudWatch Dashboard for real-time metrics"
    echo "   • Check WAF logs in CloudWatch Logs"
    echo "   • Set up additional alarms if needed"
    echo ""
    echo "3. Test the configuration:"
    echo "   • Run: ./tests/test-bot-control.sh [your-api-endpoint]"
    echo ""
    echo "4. Customize rules if needed:"
    echo "   • Edit infrastructure/bot_control_stack.py"
    echo "   • Redeploy with: $0"
    echo ""
    
    # Show cost estimate
    if [[ "$INSPECTION_LEVEL" == "TARGETED" ]]; then
        print_warning "💰 Cost Note: TARGETED inspection level incurs additional charges (~\$1/million requests)"
    else
        print_status "💰 Cost Note: COMMON inspection level (~\$1/million requests + WAF base costs)"
    fi
    
else
    print_error "❌ Failed to deploy Bot Control Stack"
    exit 1
fi