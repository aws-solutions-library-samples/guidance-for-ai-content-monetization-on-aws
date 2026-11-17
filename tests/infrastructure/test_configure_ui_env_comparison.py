"""The comparison page's prefill URL (spec scrape-vs-api-comparison R10.0).

An explicit ``--comparison-default-url`` (the demo publisher site's free article) wins over the
CMUIStack fixture output; without it the fixture stays the fallback.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deployment" / "scripts" / "configure-ui-env.py"
FIXTURE = "https://d111111abcdef8.cloudfront.net/fixtures/comparison/article.html"
DEMO_ARTICLE = "https://d222222abcdef8.cloudfront.net/free/ev-adoption.html"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("configure_ui_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prefill(module, tmp_path, **kwargs) -> str:
    out = tmp_path / ".env"
    ok = module.generate_env_file(
        {"AWSRegion": {"value": "us-west-2"}},
        {"ComparisonFixtureUrl": {"value": FIXTURE}},
        output_file=str(out),
        **kwargs,
    )
    assert ok
    lines = [line for line in out.read_text().splitlines() if line.startswith("VITE_COMPARISON_FIXTURE_URL=")]
    assert len(lines) == 1
    return lines[0].split("=", 1)[1]


def test_fixture_is_the_fallback(module, tmp_path):
    assert _prefill(module, tmp_path) == FIXTURE


def test_explicit_demo_article_wins(module, tmp_path):
    assert _prefill(module, tmp_path, comparison_default_url=DEMO_ARTICLE) == DEMO_ARTICLE
