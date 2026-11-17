#!/usr/bin/env python3
"""
AgentCore Memory deployment script for Analytics Agent

This script creates and manages AgentCore Memory resources for the analytics agent,
following the patterns from examples/07-AgentCore-E2E.
"""

import json
import os
import sys
from pathlib import Path

import boto3
import click
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    from bedrock_agentcore.memory import MemoryClient
    from bedrock_agentcore.memory.constants import StrategyType

    AGENTCORE_MEMORY_AVAILABLE = True
except ImportError:
    print("❌ bedrock-agentcore not available. Install with: pip install bedrock-agentcore")
    AGENTCORE_MEMORY_AVAILABLE = False
    sys.exit(1)

# AWS configuration
REGION = "us-west-2"
# Optional named profile; when AWS_PROFILE is unset, the standard AWS credential chain applies.
AWS_PROFILE = os.environ.get("AWS_PROFILE") or None

# Initialize clients
boto3.setup_default_session(profile_name=AWS_PROFILE, region_name=REGION)
memory_client = MemoryClient(region_name=REGION)


def get_memory_id_from_deployment_file() -> str:
    """Retrieve memory ID from local deployment file."""
    deployment_file = Path("analytics_memory_deployment.json")
    if not deployment_file.exists():
        raise Exception("Memory deployment file not found: analytics_memory_deployment.json")

    with open(deployment_file, "r") as f:
        deployment_info = json.load(f)
        memory_id = deployment_info.get("memory_id")
        if not memory_id:
            raise Exception("Memory ID not found in deployment file")
        return memory_id


def check_aws_setup():
    """Check AWS profile and region configuration."""
    try:
        sts = boto3.client("sts", region_name=REGION)
        identity = sts.get_caller_identity()
        click.echo(
            f"✅ AWS credentials ({f'profile {AWS_PROFILE}' if AWS_PROFILE else 'default credential chain'}) "
            f"configured for account: {identity.get('Account')}"
        )
        click.echo(f"✅ Region: {REGION}")
        return True
    except Exception as e:
        click.echo(f"❌ AWS setup error: {e}")
        return False


@click.group()
def cli():
    """AgentCore Memory management for Analytics Agent."""
    pass


@cli.command()
@click.option("--memory-name", default="PublisherAnalyticsMemory", help="Name for the memory resource")
@click.option(
    "--publisher-name",
    default=lambda: os.getenv("PUBLISHER_NAME", "Sample Publisher"),
    help="Publisher name for memory configuration",
)
@click.option("--event-expiry-days", default=90, help="Number of days before memories expire")
def create(memory_name, publisher_name, event_expiry_days):
    """Create AgentCore Memory resource for analytics agent."""
    click.echo("🚀 Creating AgentCore Memory for Analytics Agent")
    click.echo("=" * 50)

    if not check_aws_setup():
        sys.exit(1)

    try:
        # Check if memory already exists
        try:
            existing_memory_id = get_memory_id_from_deployment_file()
            memory_client.gmcp_client.get_memory(memoryId=existing_memory_id)
            click.echo(f"✅ Memory already exists: {existing_memory_id}")
            click.echo("   Stored in local deployment file")
            return
        except Exception:
            pass  # Memory doesn't exist, create new one

        # Define memory strategies for analytics agent
        strategies = [
            {
                StrategyType.USER_PREFERENCE.value: {
                    "name": "AnalyticsPreferences",
                    "description": f"Captures {publisher_name} user analytics preferences and behavior patterns",
                    "namespaces": [f"analytics/{publisher_name.lower()}/{{actorId}}/preferences"],
                }
            },
            {
                StrategyType.SEMANTIC.value: {
                    "name": "AnalyticsSemanticMemory",
                    "description": f"Stores {publisher_name} analytics insights and conversation context",
                    "namespaces": [f"analytics/{publisher_name.lower()}/{{actorId}}/semantic"],
                }
            },
        ]

        click.echo(f"📋 Creating memory resource: {memory_name}")
        click.echo(f"   Publisher: {publisher_name}")
        click.echo(f"   Strategies: {len(strategies)}")
        click.echo(f"   Event expiry: {event_expiry_days} days")
        click.echo("   This may take a couple of minutes...")

        # Create memory resource
        response = memory_client.create_memory_and_wait(
            name=memory_name,
            description=f"Analytics agent memory for {publisher_name} publisher platform",
            strategies=strategies,
            event_expiry_days=event_expiry_days,
        )

        memory_id = response["id"]

        # Save deployment info
        created_at = response.get("createdAt", "unknown")
        # Ensure created_at is JSON serializable
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        elif created_at != "unknown":
            created_at = str(created_at)

        deployment_info = {
            "memory_id": memory_id,
            "memory_name": memory_name,
            "publisher_name": publisher_name,
            "strategies": strategies,
            "event_expiry_days": event_expiry_days,
            "region": REGION,
            "created_at": created_at,
        }

        try:
            with open("analytics_memory_deployment.json", "w") as f:
                json.dump(deployment_info, f, indent=2)
        except Exception as json_error:
            click.echo(f"❌ Failed to write deployment file: {json_error}")
            # Try to write a minimal version
            minimal_info = {
                "memory_id": memory_id,
                "memory_name": memory_name,
                "publisher_name": publisher_name,
                "region": REGION,
                "created_at": "unknown",
            }
            with open("analytics_memory_deployment.json", "w") as f:
                json.dump(minimal_info, f, indent=2)
            click.echo("⚠️  Wrote minimal deployment info due to serialization error")

        click.echo("🎉 Memory setup completed successfully!")
        click.echo(f"   Memory ID: {memory_id}")
        click.echo("   Deployment info saved to: analytics_memory_deployment.json")

    except Exception as e:
        click.echo(f"❌ Failed to create memory: {e}")
        sys.exit(1)


@cli.command()
def info():
    """Get information about the deployed memory resource."""
    click.echo("📋 Analytics Agent Memory Information")
    click.echo("=" * 40)

    if not check_aws_setup():
        sys.exit(1)

    try:
        memory_id = get_memory_id_from_deployment_file()
        click.echo(f"📖 Memory ID from deployment file: {memory_id}")

        # Get memory details
        memory_info = memory_client.gmcp_client.get_memory(memoryId=memory_id)

        click.echo(f"   Name: {memory_info.get('name', 'N/A')}")
        click.echo(f"   Description: {memory_info.get('description', 'N/A')}")
        click.echo(f"   Status: {memory_info.get('status', 'N/A')}")
        click.echo(f"   Created: {memory_info.get('createdAt', 'N/A')}")

        # Get strategies
        strategies = memory_client.get_memory_strategies(memory_id)
        click.echo(f"   Strategies: {len(strategies)}")
        for strategy in strategies:
            click.echo(f"     - {strategy.get('name', 'N/A')} ({strategy.get('type', 'N/A')})")

    except Exception as e:
        click.echo(f"❌ Failed to get memory info: {e}")
        sys.exit(1)


@cli.command()
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def delete(confirm):
    """Delete the AgentCore Memory resource."""
    click.echo("🗑️  Delete Analytics Agent Memory")
    click.echo("=" * 35)

    if not check_aws_setup():
        sys.exit(1)

    try:
        memory_id = get_memory_id_from_deployment_file()
        click.echo(f"📖 Memory ID from deployment file: {memory_id}")

        if not confirm:
            click.confirm("Are you sure you want to delete this memory resource?", abort=True)

        # Delete memory
        memory_client.delete_memory(memory_id=memory_id)
        click.echo(f"✅ Deleted memory resource: {memory_id}")

        # Clean up deployment file
        deployment_file = Path("analytics_memory_deployment.json")
        if deployment_file.exists():
            deployment_file.unlink()
            click.echo("✅ Cleaned up deployment file")

        click.echo("🎉 Memory cleanup completed!")

    except Exception as e:
        click.echo(f"❌ Failed to delete memory: {e}")
        sys.exit(1)


@cli.command()
@click.option("--actor-id", default="test_user", help="Actor ID for test")
@click.option("--query", default="revenue analytics", help="Test query for memory retrieval")
def test(actor_id, query):
    """Test memory functionality."""
    click.echo("🧪 Testing Analytics Agent Memory")
    click.echo("=" * 35)

    if not check_aws_setup():
        sys.exit(1)

    try:
        memory_id = get_memory_id_from_deployment_file()
        click.echo(f"📖 Using memory ID: {memory_id}")

        # Test memory retrieval
        strategies = memory_client.get_memory_strategies(memory_id)
        click.echo(f"✅ Found {len(strategies)} memory strategies")

        for strategy in strategies:
            strategy_type = strategy.get("type")
            namespace = strategy.get("namespaces", [""])[0].format(actorId=actor_id)

            click.echo(f"   Testing {strategy_type} strategy...")
            click.echo(f"   Namespace: {namespace}")

            # Try to retrieve memories
            memories = memory_client.retrieve_memories(memory_id=memory_id, namespace=namespace, query=query, top_k=3)

            click.echo(f"   Retrieved {len(memories)} memories")

        click.echo("🎉 Memory test completed successfully!")

    except Exception as e:
        click.echo(f"❌ Memory test failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
