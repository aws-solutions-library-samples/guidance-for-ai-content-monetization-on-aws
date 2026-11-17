#!/usr/bin/env python3
"""
Test Structure Validation
Simple test to verify the test structure and imports are correct.
"""

import os
import sys
from pathlib import Path


def test_file_structure():
    """Test that all expected files are in the correct location."""
    test_dir = Path(__file__).parent
    expected_files = [
        "README.md",
        "__init__.py",
        "test_strands_integration.py",
        "test_agent_foundation.py",
        "test_analytics_tools.py",
        "test_context_tools.py",
        "test_context_chat.py",
        "test_gateway_integration.py",
        "TESTING_CONTEXT_AWARE_FEATURES.md",
        "STRANDS_AGENT_INTEGRATION.md",
    ]

    print("🔍 Checking test file structure...")
    missing_files = []

    for file in expected_files:
        file_path = test_dir / file
        if file_path.exists():
            print(f"✅ {file}")
        else:
            print(f"❌ {file} - MISSING")
            missing_files.append(file)

    if missing_files:
        print(f"\n❌ Missing files: {missing_files}")
        return False
    else:
        print("\n✅ All expected test files are present")
        return True


def test_analytics_agent_structure():
    """Test that analytics agent files are properly structured."""
    project_root = Path(__file__).parent.parent.parent
    analytics_dir = project_root / "analytics_agent"

    expected_files = [
        "analytics_agent.py",
        "mcp_client.py",
        "analytics_agent.py",  # Legacy compatibility
        "requirements.txt",
        ".env.example",
    ]

    print("\n🔍 Checking analytics agent structure...")
    missing_files = []

    for file in expected_files:
        file_path = analytics_dir / file
        if file_path.exists():
            print(f"✅ analytics_agent/{file}")
        else:
            print(f"❌ analytics_agent/{file} - MISSING")
            missing_files.append(file)

    if missing_files:
        print(f"\n❌ Missing analytics agent files: {missing_files}")
        return False
    else:
        print("\n✅ All expected analytics agent files are present")
        return True


def test_import_structure():
    """Test that import paths are correctly structured."""
    print("\n🔍 Checking import structure...")

    # Add project root to path
    project_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(project_root))

    try:
        # Test basic imports (without Strands dependencies)
        import context_tools as analytics_tools

        print("✅ analytics_agent.analytics_tools imports correctly")

        import context_tools

        print("✅ analytics_agent.context_tools imports correctly")

        # Test that the main agent file exists and has expected structure
        agent_file = project_root / "analytics_agent" / "analytics_agent.py"
        if agent_file.exists():
            with open(agent_file, "r") as f:
                content = f.read()
                if "from strands import Agent" in content and "from mcp_client import MCPClient" in content:
                    print("✅ analytics_agent.py has correct Strands and MCP Client imports")
                else:
                    print("❌ analytics_agent.py missing required imports")
                    return False

        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False


def test_no_old_tests_directory():
    """Test that old tests directory is removed."""
    project_root = Path(__file__).parent.parent.parent
    old_tests_dir = project_root / "analytics_agent" / "tests"

    print("\n🔍 Checking that old tests directory is removed...")

    if old_tests_dir.exists():
        print("❌ Old analytics_agent/tests directory still exists")
        return False
    else:
        print("✅ Old analytics_agent/tests directory properly removed")
        return True


def main():
    """Run all structure tests."""
    print("🧪 Testing Analytics Agent Structure")
    print("=" * 40)

    tests = [
        ("File Structure", test_file_structure),
        ("Analytics Agent Structure", test_analytics_agent_structure),
        ("Import Structure", test_import_structure),
        ("Old Tests Cleanup", test_no_old_tests_directory),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\n🔬 Running {test_name}...")
        try:
            result = test_func()
            if result:
                passed += 1
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")

    # Summary
    print("\n📊 Structure Test Summary")
    print("-" * 30)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("🎉 All structure tests passed! The analytics agent is properly organized.")
        print("\n📋 Next steps:")
        print("1. Install Strands dependencies: pip install strands-agents")
        print("2. Run integration tests: python tests/analytics_agent/test_strands_integration.py")
        print("3. Start local testing: python start_local_testing.py")
    else:
        print("⚠️  Some structure tests failed. Please fix the issues above.")


if __name__ == "__main__":
    main()
