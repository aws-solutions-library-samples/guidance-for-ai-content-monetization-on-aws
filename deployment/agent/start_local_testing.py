#!/usr/bin/env python3
"""
Start Local Testing Environment
This script starts both the API server and the UI for testing the enhanced analytics agent.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def run_command(command, cwd=None, name="Process"):
    """Run a command and handle output."""
    print(f"🚀 Starting {name}...")
    print(f"📁 Working directory: {cwd or os.getcwd()}")
    print(f"⚡ Command: {' '.join(command)}")

    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1
        )

        # Print output in real-time
        for line in iter(process.stdout.readline, ""):
            print(f"[{name}] {line.rstrip()}")

        process.wait()
        return process.returncode
    except KeyboardInterrupt:
        print(f"\n🛑 Stopping {name}...")
        process.terminate()
        return 0
    except Exception as e:
        print(f"❌ Error running {name}: {e}")
        return 1


def check_dependencies():
    """Check if required dependencies are available."""
    print("🔍 Checking dependencies...")

    # Check Python dependencies
    try:
        import flask
        import flask_cors

        print("✅ Flask dependencies found")
    except ImportError as e:
        print(f"❌ Missing Python dependencies: {e}")
        print("💡 Install with: pip install flask flask-cors")
        return False

    # Check if analytics agent exists
    if not os.path.exists("analytics_agent_strands.py"):
        print("❌ Analytics agent not found")
        return False
    print("✅ Analytics agent found")

    # Check if UI directory exists
    if not os.path.exists("../ui"):
        print("❌ UI directory not found")
        return False
    print("✅ UI directory found")

    # Check if Node.js is available
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ Node.js found: {result.stdout.strip()}")
        else:
            print("❌ Node.js not found")
            return False
    except FileNotFoundError:
        print("❌ Node.js not found")
        print("💡 Install Node.js from: https://nodejs.org/")
        return False

    # Check if npm packages are installed
    if not os.path.exists("../ui/node_modules"):
        print("⚠️  UI dependencies not installed")
        print("💡 Installing UI dependencies...")
        try:
            subprocess.run(["npm", "install"], cwd="../ui", check=True)
            print("✅ UI dependencies installed")
        except subprocess.CalledProcessError:
            print("❌ Failed to install UI dependencies")
            return False
    else:
        print("✅ UI dependencies found")

    return True


def start_api_server():
    """Start the API server."""
    return run_command([sys.executable, "local_api_server.py"], name="API Server")


def start_ui_server():
    """Start the UI development server."""
    return run_command(["npm", "run", "dev"], cwd="../ui", name="UI Server")


def main():
    """Main function to start both servers."""
    print("🎯 Analytics Agent Local Testing Environment")
    print("=" * 50)

    # Check dependencies
    if not check_dependencies():
        print("\n❌ Dependency check failed. Please resolve the issues above.")
        sys.exit(1)

    print("\n✅ All dependencies satisfied!")
    print("\n🚀 Starting local testing environment...")
    print("📊 This will start both the API server and UI development server")
    print("🌐 API Server: http://localhost:5001")
    print("🎨 UI Server: http://localhost:5173 (or similar)")
    print("\n⚡ Press Ctrl+C to stop both servers")

    # Start both servers in separate threads
    api_thread = threading.Thread(target=start_api_server, daemon=True)
    ui_thread = threading.Thread(target=start_ui_server, daemon=True)

    try:
        # Start API server
        api_thread.start()
        time.sleep(2)  # Give API server time to start

        # Start UI server
        ui_thread.start()

        # Wait for both threads
        api_thread.join()
        ui_thread.join()

    except KeyboardInterrupt:
        print("\n🛑 Shutting down servers...")
        print("✅ Local testing environment stopped")


if __name__ == "__main__":
    main()
