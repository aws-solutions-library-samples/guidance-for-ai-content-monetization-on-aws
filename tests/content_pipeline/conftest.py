"""Shared fixtures + path setup for content-pipeline unit tests."""

import os
import sys

import pytest

# Put the pipeline package and its sibling modules on the path exactly as they
# sit inside the deployment zip (handler, extractor, geo_enricher at root;
# services/ and shared/ as packages).
LAMBDA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
for p in (LAMBDA_ROOT, os.path.join(LAMBDA_ROOT, "content_pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("CONTENT_TABLE_NAME", "cm-content-test")
os.environ.setdefault("CONTENT_BUCKET_NAME", "iab-llm-content-test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("REGION", "us-east-1")


@pytest.fixture
def sample_html():
    return (
        b"<html><head><title>EV Costs Fall</title>"
        b'<meta name="author" content="Priya Raman">'
        b'<meta property="article:published_time" content="2026-03-04T09:00:00Z">'
        b'<link rel="canonical" href="https://x.com/ev"/></head>'
        b"<body><nav>skip me</nav><article><h1>EV Costs Fall</h1>"
        b"<p>Battery packs dropped below eighty dollars per kilowatt hour.</p>"
        b"<script>tracking()</script></article></body></html>"
    )
