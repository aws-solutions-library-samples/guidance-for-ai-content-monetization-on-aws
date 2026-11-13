"""Redact credentials from API Gateway events before they are logged."""

from typing import Any, Dict

REDACTED = "[REDACTED]"

# Header names (lower-case) whose values are credentials. API Gateway forwards these to the integration.
SENSITIVE_HEADERS = frozenset({"x-api-key", "authorization", "cookie", "x-publisher-key", "x-amz-security-token"})


def redact_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of ``event`` with credential header values replaced.

    Only ``headers`` and ``multiValueHeaders`` are copied; the input event is never mutated. The API key
    id (``requestContext.identity.apiKeyId``) is an identifier, not a credential, and is kept for tracing.
    """
    if not isinstance(event, dict):
        return event
    redacted = dict(event)
    for field in ("headers", "multiValueHeaders"):
        headers = event.get(field)
        if isinstance(headers, dict):
            redacted[field] = {
                name: (
                    ([REDACTED] * len(value) if isinstance(value, list) else REDACTED)
                    if isinstance(name, str) and name.lower() in SENSITIVE_HEADERS
                    else value
                )
                for name, value in headers.items()
            }
    context = event.get("requestContext")
    identity = context.get("identity") if isinstance(context, dict) else None
    if isinstance(identity, dict) and identity.get("apiKey"):
        redacted["requestContext"] = {**context, "identity": {**identity, "apiKey": REDACTED}}
    return redacted
