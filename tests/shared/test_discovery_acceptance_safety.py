"""Acceptance tooling must not leak temporary artifact-download credentials."""

import traceback
from unittest.mock import Mock

import pytest
import requests

from tests.integration import test_discovery_review as acceptance


def test_artifact_download_exception_omits_presigned_credentials(monkeypatch):
    url = "https://artifact.invalid/code.zip?X-Amz-Security-Token=synthetic-secret"
    monkeypatch.setattr(
        acceptance.requests, "get", Mock(side_effect=requests.ConnectionError(f"Cannot connect to {url}"))
    )
    with pytest.raises(RuntimeError, match="Unable to download") as raised:
        acceptance._download_artifact(url)
    rendered = "".join(traceback.format_exception(raised.value))
    assert "synthetic-secret" not in rendered
    assert "X-Amz-Security-Token" not in rendered
    assert raised.value.__suppress_context__
