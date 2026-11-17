#!/usr/bin/env python3
"""
Validation script for analytics agent deployment files.
This helps ensure deployment files are valid before deployment.
"""

import json
import sys
from pathlib import Path


def validate_json_file(file_path: Path, required_fields: list = None) -> bool:
    """Validate a JSON file and check for required fields."""
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return False

    try:
        with open(file_path, "r") as f:
            content = f.read().strip()

        if not content:
            print(f"❌ File is empty: {file_path}")
            return False

        data = json.loads(content)

        if required_fields:
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                print(f"❌ Missing required fields in {file_path}: {missing_fields}")
                return False

        print(f"✅ Valid JSON file: {file_path}")
        return True

    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {file_path}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading {file_path}: {e}")
        return False


def main():
    """Validate all analytics agent deployment files."""
    print("🔍 Validating analytics agent deployment files...")
    print("=" * 50)

    script_dir = Path(__file__).parent
    all_valid = True

    # Validate memory deployment file
    memory_file = script_dir / "analytics_memory_deployment.json"
    memory_required = ["memory_id", "memory_name", "publisher_name", "region"]
    if not validate_json_file(memory_file, memory_required):
        all_valid = False

    # Validate analytics agent deployment file
    agent_file = script_dir / "analytics_agent_deployment.json"
    agent_required = ["analytics_agent_arn", "analytics_agent_name", "mcp_server_arn"]
    if not validate_json_file(agent_file, agent_required):
        all_valid = False

    print("=" * 50)
    if all_valid:
        print("✅ All deployment files are valid!")
        sys.exit(0)
    else:
        print("❌ Some deployment files have issues!")
        sys.exit(1)


if __name__ == "__main__":
    main()
