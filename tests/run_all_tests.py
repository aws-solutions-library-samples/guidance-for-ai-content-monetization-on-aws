#!/usr/bin/env python3
"""
Comprehensive test runner for the Publisher Content MCP Server project.
Runs all tests in organized categories.
"""

import sys
import os
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_test_category(category_name: str, test_path: Path) -> bool:
    """Run tests in a specific category."""
    print(f"\n🧪 Running {category_name} Tests")
    print("=" * 60)

    if not test_path.exists():
        print(f"❌ Test directory {test_path} does not exist")
        return False

    # Get all test files in the category
    test_files = list(test_path.glob("test_*.py"))

    if not test_files:
        print(f"⚠️  No test files found in {test_path}")
        return True

    passed = 0
    total = len(test_files)

    for test_file in sorted(test_files):
        test_name = test_file.stem.replace("test_", "").replace("_", " ").title()
        print(f"\n🔍 Running: {test_name}")

        try:
            # Use pytest for most tests, direct execution for specific ones
            if "strands_integration" in test_file.name or "deployed_strands_agent" in test_file.name:
                # Run directly for these specific tests
                result = subprocess.run(
                    [sys.executable, str(test_file)], capture_output=True, text=True, cwd=project_root
                )
            else:
                # Use pytest for other tests
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", str(test_file), "-v"],
                    capture_output=True,
                    text=True,
                    cwd=project_root,
                )

            if result.returncode == 0:
                print(f"✅ {test_name} PASSED")
                passed += 1
            else:
                print(f"❌ {test_name} FAILED")
                if result.stdout:
                    print(f"   STDOUT: {result.stdout[:200]}...")
                if result.stderr:
                    print(f"   STDERR: {result.stderr[:200]}...")

        except Exception as e:
            print(f"❌ {test_name} ERROR: {e}")

    print(f"\n📊 {category_name} Results: {passed}/{total} tests passed")
    return passed == total


def run_pytest_category(category_name: str, test_path: Path) -> bool:
    """Run pytest tests in a specific category."""
    print(f"\n🧪 Running {category_name} Tests (pytest)")
    print("=" * 60)

    if not test_path.exists():
        print(f"❌ Test directory {test_path} does not exist")
        return False

    try:
        # Run pytest on the directory
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-v"], capture_output=True, text=True, cwd=project_root
        )

        if result.returncode == 0:
            print(f"✅ {category_name} pytest PASSED")
            return True
        else:
            print(f"❌ {category_name} pytest FAILED")
            if result.stdout:
                print(f"   STDOUT: {result.stdout[-500:]}")  # Last 500 chars
            if result.stderr:
                print(f"   STDERR: {result.stderr[-500:]}")
            return False

    except Exception as e:
        print(f"❌ {category_name} pytest ERROR: {e}")
        return False


def main():
    """Run all test categories."""
    print("🚀 Publisher Content MCP Server - Comprehensive Test Suite")
    print("=" * 80)

    test_categories = [
        ("Analytics Agent", project_root / "tests" / "analytics_agent", "direct"),
        ("MCP Server", project_root / "tests" / "mcp_server_tests", "pytest"),
        ("Integration", project_root / "tests" / "integration", "pytest"),
        ("Authentication", project_root / "tests" / "authentication", "pytest"),
        ("Lambda Functions", project_root / "tests" / "lambda_functions", "pytest"),
    ]

    passed_categories = 0
    total_categories = len(test_categories)

    for category_name, test_path, run_type in test_categories:
        if run_type == "direct":
            success = run_test_category(category_name, test_path)
        else:  # pytest
            success = run_pytest_category(category_name, test_path)

        if success:
            passed_categories += 1

        print("-" * 80)

    print(f"\n🎯 FINAL RESULTS: {passed_categories}/{total_categories} test categories passed")

    if passed_categories == total_categories:
        print("🎉 ALL TESTS PASSED! System is ready for deployment.")
        return True
    else:
        print("❌ SOME TESTS FAILED! Please fix issues before deployment.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
