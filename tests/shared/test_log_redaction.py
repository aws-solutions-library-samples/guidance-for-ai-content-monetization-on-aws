"""The partner and UI handlers must never write credential header values to the log."""

import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from shared.utils.log_redaction import REDACTED, redact_event  # noqa: E402

KEY = "abcdEFGHijklMNOPqrstUVWXyz0123456789live"
JWT = "Bearer eyJhbGciOi.secret.payload"


def _event():
    return {
        "httpMethod": "POST",
        "path": "/query",
        "headers": {"X-Api-Key": KEY, "Authorization": JWT, "Content-Type": "application/json"},
        "multiValueHeaders": {"x-api-key": [KEY], "authorization": [JWT], "accept": ["*/*"]},
        "requestContext": {"identity": {"apiKeyId": "g0dnlqx6e4", "apiKey": KEY}},
        "body": "{}",
    }


def test_redacts_credential_headers_case_insensitively_and_keeps_the_rest():
    event = _event()
    out = redact_event(event)
    assert out["headers"]["X-Api-Key"] == REDACTED
    assert out["headers"]["Authorization"] == REDACTED
    assert out["headers"]["Content-Type"] == "application/json"
    assert out["multiValueHeaders"]["x-api-key"] == [REDACTED]
    assert out["multiValueHeaders"]["accept"] == ["*/*"]
    assert out["requestContext"]["identity"] == {"apiKeyId": "g0dnlqx6e4", "apiKey": REDACTED}
    serialized = json.dumps(out)
    assert KEY not in serialized and "secret.payload" not in serialized
    assert event["headers"]["X-Api-Key"] == KEY, "input must not be mutated"


def test_tolerates_missing_or_null_headers():
    assert redact_event({"headers": None, "multiValueHeaders": None})["headers"] is None
    assert redact_event({}) == {}


def _load_handler(module, monkeypatch):
    import importlib
    import importlib.util

    if module == "partner_api.handler":
        return importlib.import_module(module)
    # ui_backend's handler imports ``routes`` from the deployment zip root, so load it with its own
    # directory on the path, as the Lambda runtime does.
    base = os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "ui_backend")
    monkeypatch.syspath_prepend(base)
    spec = importlib.util.spec_from_file_location("ui_backend_handler_under_test", os.path.join(base, "handler.py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize("module", ["partner_api.handler", "ui_backend.handler"])
def test_handler_event_log_line_contains_no_credential(module, caplog, monkeypatch):
    handler = _load_handler(module, monkeypatch)
    monkeypatch.setattr(handler, "logger", logging.getLogger(f"test.{module}"))
    with caplog.at_level(logging.INFO, logger=f"test.{module}"):
        try:
            handler.lambda_handler(_event(), None)
        except Exception:  # routing may fail without AWS; only the log line matters
            pass
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Event:" in logged
    assert KEY not in logged
    assert "secret.payload" not in logged
