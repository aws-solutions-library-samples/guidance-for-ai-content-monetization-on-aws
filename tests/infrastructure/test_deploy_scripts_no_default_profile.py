"""Shipped deployment scripts have no built-in AWS profile; the standard credential chain applies."""

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "deployment"

PYTHON_SCRIPTS = [
    DEPLOYMENT / "agent" / "deploy.py",
    DEPLOYMENT / "agent" / "deploy_memory.py",
    DEPLOYMENT / "infrastructure" / "create_licensing_tables.py",
    DEPLOYMENT / "data" / "provision_telemetry_connection.py",
]
SHELL_SCRIPTS = [
    # Legacy scripts left out of the published package; checked only where they exist.
    pytest.param(
        DEPLOYMENT / "infrastructure" / name,
        marks=pytest.mark.skipif(
            not (DEPLOYMENT / "infrastructure" / name).exists(), reason=f"{name} is not in this package"
        ),
    )
    for name in ("deploy-complete.sh", "redeploy-stack.sh")
]


@pytest.mark.parametrize("script", PYTHON_SCRIPTS, ids=lambda p: p.name)
def test_python_script_has_no_profile_literal(script):
    """No string constant in the executable code names the internal profile."""
    tree = ast.parse(script.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and not node.value.count("\n"):
            assert node.value != "genai", f"{script.name}:{node.lineno} hard-codes the genai profile"


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: Path(p).name)
def test_shell_script_has_no_profile_default(script):
    text = script.read_text()
    assert "genai" not in text
    assert re.search(r'AWS_PROFILE="\$\{AWS_PROFILE:-\$\{1:-\}\}"', text)
    # A profile flag is only ever passed through the array that is empty when no profile is set.
    for line in text.splitlines():
        if line.lstrip().startswith("#") or line.strip().startswith("PROFILE_ARGS=(--profile"):
            continue
        assert not re.search(r"--profile\s+\"?\$\{?AWS_PROFILE", line), line


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: Path(p).name)
def test_shell_script_parses(script):
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_licensing_tables_profile_defaults_to_none():
    spec = importlib.util.spec_from_file_location("create_licensing_tables", DEPLOYMENT / "infrastructure" / "create_licensing_tables.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}

    import argparse

    original = argparse.ArgumentParser.add_argument

    def spy(self, *args, **kwargs):
        if "--profile" in args:
            captured.update(kwargs)
        return original(self, *args, **kwargs)

    argparse.ArgumentParser.add_argument = spy
    try:
        sys.argv = ["create_licensing_tables.py", "--help"]
        with pytest.raises(SystemExit):
            module.main()
    finally:
        argparse.ArgumentParser.add_argument = original
    assert captured.get("default") is None
