#!/usr/bin/env python3
"""
Setup Script for Analytics Agent with Strands
This script helps configure the analytics agent with your preferred LLM provider.
"""

import os
import subprocess
import sys
from pathlib import Path


def print_header():
    """Print setup header."""
    print("🤖 Analytics Agent Setup with Strands")
    print("=" * 50)
    print("This script will help you configure the analytics agent with your preferred LLM provider.")
    print()


def check_python_version():
    """Check Python version compatibility."""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8 or higher is required")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")


def install_base_dependencies():
    """Install base Strands dependencies."""
    print("\n📦 Installing base Strands dependencies...")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "strands-agents>=0.1.0",
                "strands-agents-tools>=0.1.0",
                "httpx>=0.25.0",
                "pydantic>=2.0.0",
                "python-dotenv>=1.0.0",
                "flask>=2.3.0",
                "flask-cors>=4.0.0",
            ],
            check=True,
        )
        print("✅ Base dependencies installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install base dependencies")
        sys.exit(1)


def choose_llm_provider():
    """Let user choose their preferred LLM provider."""
    print("\n🧠 Choose your preferred LLM provider:")
    print("1. Local Ollama (free, runs locally)")
    print("2. OpenAI (requires API key)")
    print("3. Anthropic Claude (requires API key)")
    print("4. AWS Bedrock (requires AWS credentials)")
    print("5. Skip LLM configuration")

    while True:
        choice = input("\nEnter your choice (1-5): ").strip()
        if choice in ["1", "2", "3", "4", "5"]:
            return choice
        print("Invalid choice. Please enter 1, 2, 3, 4, or 5.")


def setup_ollama():
    """Setup Ollama for local LLM."""
    print("\n🦙 Setting up Ollama (Local LLM)...")

    # Install Ollama client
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "strands-agents[ollama]"], check=True)
        print("✅ Ollama client installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install Ollama client")
        return False

    # Check if Ollama is running
    try:
        import requests

        response = requests.get("http://localhost:11434/api/version", timeout=5)
        if response.status_code == 200:
            print("✅ Ollama server is running")
        else:
            print("⚠️  Ollama server not responding")
    except Exception:
        print("⚠️  Ollama server not detected at localhost:11434")
        print("💡 Install Ollama from: https://ollama.ai/")
        print("💡 Then run: ollama pull llama3.1:8b")

    return {"USE_LOCAL_LLM": "true", "OLLAMA_HOST": "http://localhost:11434", "OLLAMA_MODEL": "llama3.1:8b"}


def setup_openai():
    """Setup OpenAI."""
    print("\n🤖 Setting up OpenAI...")

    # Install OpenAI client via LiteLLM
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "strands-agents[litellm]"], check=True)
        print("✅ OpenAI client installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install OpenAI client")
        return False

    api_key = input("Enter your OpenAI API key: ").strip()
    if not api_key:
        print("❌ API key is required")
        return False

    model = input("Enter OpenAI model (default: gpt-4o-mini): ").strip() or "gpt-4o-mini"

    return {"OPENAI_API_KEY": api_key, "OPENAI_MODEL": model}


def setup_anthropic():
    """Setup Anthropic Claude."""
    print("\n🧠 Setting up Anthropic Claude...")

    # Install Anthropic client
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "strands-agents[anthropic]"], check=True)
        print("✅ Anthropic client installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install Anthropic client")
        return False

    api_key = input("Enter your Anthropic API key: ").strip()
    if not api_key:
        print("❌ API key is required")
        return False

    model = input("Enter Anthropic model (default: claude-3-haiku-20240307): ").strip() or "claude-3-haiku-20240307"

    return {"ANTHROPIC_API_KEY": api_key, "ANTHROPIC_MODEL": model}


def setup_bedrock():
    """Setup AWS Bedrock."""
    print("\n☁️  Setting up AWS Bedrock...")

    # Install boto3
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "boto3>=1.34.0"], check=True)
        print("✅ AWS SDK installed")
    except subprocess.CalledProcessError:
        print("❌ Failed to install AWS SDK")
        return False

    print("💡 Make sure you have AWS credentials configured:")
    print("   - AWS CLI: aws configure")
    print("   - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    print("   - IAM role (if running on EC2)")

    region = input("Enter AWS region (default: us-west-2): ").strip() or "us-west-2"
    model = (
        input("Enter Bedrock model (default: us.anthropic.claude-3-7-sonnet-20250219-v1:0): ").strip()
        or "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
    )

    return {"AWS_DEFAULT_REGION": region, "BEDROCK_MODEL": model}


def create_env_file(config):
    """Create .env file with configuration."""
    env_path = Path("analytics_agent/.env")

    # Read example file
    example_path = Path("analytics_agent/.env.example")
    if example_path.exists():
        with open(example_path, "r") as f:
            content = f.read()
    else:
        content = "# Analytics Agent Configuration\n"

    # Add configuration
    content += "\n# Generated configuration\n"
    for key, value in config.items():
        content += f"{key}={value}\n"

    # Write .env file
    with open(env_path, "w") as f:
        f.write(content)

    print(f"✅ Configuration saved to {env_path}")


def test_configuration():
    """Test the analytics agent configuration."""
    print("\n🧪 Testing analytics agent configuration...")

    try:
        # Import and test the agent
        from analytics_agent_strands import PublisherAnalyticsAgent

        agent = PublisherAnalyticsAgent()
        print("✅ Analytics agent initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Error testing configuration: {e}")
        return False


def main():
    """Main setup function."""
    print_header()

    # Check Python version
    check_python_version()

    # Install base dependencies
    install_base_dependencies()

    # Choose LLM provider
    choice = choose_llm_provider()

    config = {}

    if choice == "1":
        config = setup_ollama()
    elif choice == "2":
        config = setup_openai()
    elif choice == "3":
        config = setup_anthropic()
    elif choice == "4":
        config = setup_bedrock()
    elif choice == "5":
        print("⏭️  Skipping LLM configuration")
        config = {"AWS_DEFAULT_REGION": "us-west-2"}  # Default to Bedrock

    if config:
        create_env_file(config)

    # Test configuration
    if test_configuration():
        print("\n🎉 Setup completed successfully!")
        print("\n📋 Next steps:")
        print("1. Start the API server: python local_api_server.py")
        print("2. Start the UI: cd source/ui && npm run dev")
        print("3. Open your browser and test the analytics assistant")

        if choice == "1":
            print("\n🦙 Ollama specific:")
            print("   - Make sure Ollama is running: ollama serve")
            print("   - Pull a model: ollama pull llama3.1:8b")
    else:
        print("\n❌ Setup completed with errors. Please check the configuration.")


if __name__ == "__main__":
    main()
