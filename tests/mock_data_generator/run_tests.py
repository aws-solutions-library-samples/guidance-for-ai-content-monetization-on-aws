#!/usr/bin/env python3
"""
Simple test runner for mock data generator tests.
"""

import sys
import subprocess


def main():
    """Run the mock data generator tests."""

    if len(sys.argv) > 1:
        # Run specific test class
        class_name = sys.argv[1]
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            f"tests/mock_data_generator/test_mock_data_generator.py::{class_name}",
            "-v",
        ]
        print(f"Running tests for class: {class_name}")
    else:
        # Run all tests
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "tests/mock_data_generator/test_mock_data_generator.py",
            "-v",
            "--tb=short",
        ]
        print("Running all mock data generator tests...")

    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        print("\n✅ All tests passed!")
    else:
        print("\n❌ Some tests failed!")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
