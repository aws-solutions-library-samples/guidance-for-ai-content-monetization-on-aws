#!/usr/bin/env python3
"""
Test runner for mock data generator tests.

This script runs the comprehensive test suite for the mock data generator,
including unit tests, integration tests, and DynamoDB operation tests.
"""

import sys
import subprocess
from pathlib import Path


def run_tests():
    """Run the mock data generator test suite."""

    # Get the project root directory (two levels up from tests/mock_data_generator/)
    project_root = Path(__file__).parent.parent.parent
    test_file = project_root / "tests" / "mock_data_generator" / "test_mock_data_generator.py"

    print("=" * 80)
    print("MOCK DATA GENERATOR TEST SUITE")
    print("=" * 80)
    print(f"Running tests from: {test_file}")
    print()

    # Run pytest with verbose output
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-v",
        "--tb=short",
        "--durations=10",  # Show 10 slowest tests
        "-W",
        "ignore::DeprecationWarning",  # Suppress deprecation warnings
    ]

    try:
        result = subprocess.run(cmd, cwd=project_root, check=False)

        print()
        print("=" * 80)
        if result.returncode == 0:
            print("✅ ALL TESTS PASSED!")
        else:
            print("❌ SOME TESTS FAILED!")
        print("=" * 80)

        return result.returncode

    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Error running tests: {e}")
        return 1


def run_specific_test_class(class_name):
    """Run tests for a specific test class."""

    # Get the project root directory (two levels up from tests/mock_data_generator/)
    project_root = Path(__file__).parent.parent.parent
    test_file = project_root / "tests" / "mock_data_generator" / "test_mock_data_generator.py"

    print(f"Running tests for class: {class_name}")

    cmd = [sys.executable, "-m", "pytest", f"{test_file}::{class_name}", "-v", "-W", "ignore::DeprecationWarning"]

    result = subprocess.run(cmd, cwd=project_root, check=False)
    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Run specific test class
        class_name = sys.argv[1]
        exit_code = run_specific_test_class(class_name)
    else:
        # Run all tests
        exit_code = run_tests()

    sys.exit(exit_code)
