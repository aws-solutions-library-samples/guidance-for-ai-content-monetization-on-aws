#!/usr/bin/env python3
"""
Configure UI Environment Variables from CDK Stack Outputs

This script reads CDK stack outputs and generates the appropriate .env file
for the UI build process, including observability configuration.
"""

import json
import os
import re
import sys
from typing import Any, Dict, Optional

import boto3


def get_solution_display_name(
    manifest_path: str = "solution-manifest.yaml",
) -> str:
    """Derive the UI-facing solution name from solution-manifest.yaml.

    Reads the manifest `name:` (the canonical full name, e.g.
    "Guidance for Publisher Content Monetization for AI Agents on AWS") and
    strips the "Guidance for " prefix and " on AWS" suffix to produce the UI
    short form ("Publisher Content Monetization for AI Agents"). Parsed with a
    scalar regex to avoid a PyYAML dependency in this build script. Returns an
    empty string if the manifest or `name:` is absent, so the UI's own
    hardcoded fallback (solutionName.ts) takes over.
    """
    try:
        with open(manifest_path, "r") as f:
            content = f.read()
    except OSError:
        print(f"⚠️  {manifest_path} not found; UI will use its built-in name fallback")
        return ""

    # Match `name:` under the top-level `solution:` block. The manifest keeps
    # name on its own line as a simple scalar.
    match = re.search(r"^\s{2,}name:\s*(.+?)\s*$", content, re.MULTILINE)
    if not match:
        print(f"⚠️  No `name:` found in {manifest_path}; UI will use its built-in fallback")
        return ""

    full_name = match.group(1).strip().strip("'\"")
    short = re.sub(r"^Guidance for\s+", "", full_name)
    short = re.sub(r"\s+on AWS$", "", short)
    return short.strip()


def get_stack_outputs(stack_name: str, region: str = None, profile: str = None) -> Dict[str, Any]:
    """Get outputs from a CloudFormation stack."""
    try:
        # Create session with profile if specified
        if profile:
            session = boto3.Session(profile_name=profile)
        else:
            session = boto3.Session()
            
        # Use specified region or session default
        if region:
            cf = session.client('cloudformation', region_name=region)
        else:
            cf = session.client('cloudformation')
            
        # Get stack outputs
        response = cf.describe_stacks(StackName=stack_name)
        stacks = response.get('Stacks', [])
        
        if not stacks:
            print(f"❌ Stack '{stack_name}' not found")
            return {}
            
        stack = stacks[0]
        outputs = stack.get('Outputs', [])
        
        # Convert outputs to dictionary
        output_dict = {}
        for output in outputs:
            key = output.get('OutputKey')
            value = output.get('OutputValue')
            description = output.get('Description', '')
            
            if key and value:
                output_dict[key] = {
                    'value': value,
                    'description': description
                }
                
        return output_dict
        
    except Exception as e:
        print(f"❌ Error getting stack outputs: {str(e)}")
        return {}

def generate_env_file(
    main_stack_outputs: Dict[str, Any],
    ui_stack_outputs: Dict[str, Any],
    enable_demo: bool = True,
    analytics_agent_arn: Optional[str] = None,
    mcp_server_arn: Optional[str] = None,
    use_mock_data: bool = False,
    use_mock_analytics: bool = False,
    demo_mode: bool = False,
    output_file: str = "source/ui/.env",
    comparison_default_url: Optional[str] = None,
) -> bool:
    """Generate .env file from stack outputs."""
    
    try:
        # Extract values from stack outputs
        api_base_url = main_stack_outputs.get('ApiGatewayUrl', {}).get('value', 'https://api.example.com')
        region = main_stack_outputs.get('AWSRegion', {}).get('value', 'us-west-2')
        
        # Cognito configuration
        user_pool_id = main_stack_outputs.get('UserPoolId', {}).get('value', '')
        user_pool_client_id = main_stack_outputs.get('UserPoolClientId', {}).get('value', '')
        identity_pool_id = main_stack_outputs.get('IdentityPoolId', {}).get('value', '')
        
        # UI stack specific outputs
        cloudwatch_genai_url = ui_stack_outputs.get('CloudWatchGenAIUrl', {}).get('value', '')
        # Comparison prefill (scrape-vs-api-comparison R10.0): an explicit demo article (e.g. the
        # publisher site's free article) wins; the CMUIStack fixture is the fallback.
        comparison_fixture_url = comparison_default_url or ui_stack_outputs.get('ComparisonFixtureUrl', {}).get('value', '')
        
        # Extract analytics endpoint from main stack MCP configuration
        analytics_api_url = ''
        analytics_endpoint = '/analytics-agent/chat'
        
        mcp_config_str = main_stack_outputs.get('MCPServerConfiguration', {}).get('value', '{}')
        try:
            mcp_config = json.loads(mcp_config_str)
            analytics_full_endpoint = mcp_config.get('analytics_endpoint', '')
            if analytics_full_endpoint:
                # Extract base URL and endpoint path
                if '/analytics-agent/chat' in analytics_full_endpoint:
                    analytics_api_url = analytics_full_endpoint.replace('/analytics-agent/chat', '')
                    analytics_endpoint = '/analytics-agent/chat'
                    print(f"✅ Extracted analytics API URL: {analytics_api_url}")
        except json.JSONDecodeError:
            print("⚠️  Could not parse MCP server configuration for analytics endpoint")
        
        # Generate .env content
        solution_name = get_solution_display_name()
        env_content = f"""# AI Content Monetization API - Environment Configuration
# Generated automatically from CDK stack outputs

# Solution display name (derived from solution-manifest.yaml `name`)
{f'VITE_SOLUTION_NAME={solution_name}' if solution_name else '# VITE_SOLUTION_NAME=<manifest name not found; UI uses its built-in fallback>'}

# Demo Mode Configuration
VITE_ENABLE_DEMO={str(enable_demo).lower()}

# API Configuration
VITE_API_BASE_URL={api_base_url}
VITE_API_VERSION=v1

# AWS Configuration
VITE_AWS_REGION={region}

# Cognito Authentication Configuration
VITE_USER_POOL_ID={user_pool_id}
VITE_USER_POOL_CLIENT_ID={user_pool_client_id}
VITE_IDENTITY_POOL_ID={identity_pool_id}

# Analytics Configuration
VITE_ENABLE_ANALYTICS=true
VITE_ANALYTICS_ENDPOINT={analytics_endpoint}
VITE_ANALYTICS_API_URL={analytics_api_url}

# AgentCore Observability Configuration
{f'VITE_ANALYTICS_AGENT_ARN={analytics_agent_arn}' if analytics_agent_arn else '# VITE_ANALYTICS_AGENT_ARN=<not-configured>'}
{f'VITE_MCP_SERVER_ARN={mcp_server_arn}' if mcp_server_arn else '# VITE_MCP_SERVER_ARN=<not-configured>'}
VITE_CLOUDWATCH_GENAI_URL={cloudwatch_genai_url or f'https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#/gen-ai-observability/agent-core/agents'}

# Scrape-vs-API comparison page
VITE_COMPARISON_FIXTURE_URL={comparison_fixture_url}
VITE_COMPARISON_POLL_LIMIT_MS=180000

# Mock Data Integration Configuration
VITE_USE_MOCK_DATA={str(use_mock_data).lower()}
VITE_USE_MOCK_ANALYTICS={str(use_mock_analytics).lower()}
VITE_DEMO_MODE={str(demo_mode).lower()}

# Feature Flags
VITE_ENABLE_DEMO_CONTROLLER={str(enable_demo).lower()}
# NOTE: Both WordPress build flags were retired. VITE_ENABLE_WORDPRESS_INTEGRATION
# was retired by the WordPress re-home, and VITE_ENABLE_WORDPRESS_MOCK_UI was retired
# alongside it (it was tied to the deprecated WordPressConnector). WordPress is now a
# content-domain Connection driven by connection-activation state, managed through the
# Connections framework -- not a build-time flag. Do not re-add either.
"""

        # Write to file
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(env_content)
            
        print(f"✅ Environment configuration written to {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ Error generating .env file: {str(e)}")
        return False

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Configure UI environment from CDK stack outputs')
    parser.add_argument('--main-stack', default='ContentMonetizationStack', help='Main CDK stack name')
    parser.add_argument('--ui-stack', default='CMUIStack', help='UI CDK stack name')
    parser.add_argument('--region', help='AWS region')
    parser.add_argument('--profile', help='AWS profile to use')
    parser.add_argument('--enable-demo', action='store_true', default=True, help='Enable demo mode')
    parser.add_argument('--disable-demo', action='store_true', help='Disable demo mode')
    parser.add_argument('--analytics-agent-arn', help='Analytics Agent ARN')
    parser.add_argument('--mcp-server-arn', help='MCP Server ARN')
    parser.add_argument('--use-mock-data', action='store_true', help='Enable mock data in MCP server')
    parser.add_argument('--use-mock-analytics', action='store_true', help='Enable mock analytics tools')
    parser.add_argument('--demo-mode', action='store_true', help='Enable all mock flags for demo')
    parser.add_argument('--output', default='source/ui/.env', help='Output file path')
    parser.add_argument('--comparison-default-url', help='Prefill URL for the scrape-vs-API comparison (e.g. the demo publisher site free article); defaults to the CMUIStack fixture')
    
    args = parser.parse_args()
    
    # Determine demo mode
    enable_demo = args.enable_demo and not args.disable_demo
    
    # Get region from environment if not specified
    region = args.region or os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('AWS_REGION')
    
    print(f"🔍 Getting outputs from main stack: {args.main_stack}")
    main_outputs = get_stack_outputs(args.main_stack, region, args.profile)
    
    print(f"🔍 Getting outputs from UI stack: {args.ui_stack}")
    ui_outputs = get_stack_outputs(args.ui_stack, region, args.profile)
    
    if not main_outputs and not ui_outputs:
        print("❌ No stack outputs found. Make sure the stacks are deployed.")
        sys.exit(1)
    
    # Generate .env file
    success = generate_env_file(
        main_outputs,
        ui_outputs,
        enable_demo,
        args.analytics_agent_arn,
        args.mcp_server_arn,
        args.use_mock_data,
        args.use_mock_analytics,
        args.demo_mode,
        args.output,
        args.comparison_default_url,
    )
    
    if success:
        print("✅ UI environment configuration complete!")
        print(f"📁 Configuration saved to: {args.output}")
        print("\n🚀 Next steps:")
        print("   cd source/ui")
        print("   npm install")
        print("   npm run build")
        print("   cdk deploy CMUIStack")
    else:
        sys.exit(1)

if __name__ == '__main__':
    main()