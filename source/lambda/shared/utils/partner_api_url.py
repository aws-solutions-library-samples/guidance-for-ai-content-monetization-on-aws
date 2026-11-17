"""The partner API base URL as a single, environment-driven fact (design Q8).

The partner plane has no custom domain in this guidance: its URL is the generated
``https://<api-id>.execute-api.<region>.amazonaws.com/<stage>`` hostname, exported by the stack as
``PartnerApiUrl`` and published to Parameter Store (the Lambdas that advertise partner links are
referenced by the API definitions, so injecting the URL as an environment variable would be a
CloudFormation dependency cycle). ``PARTNER_API_URL`` overrides everything for tests and custom
domains; nothing else changes.
"""

import logging
import os
from typing import Any, Dict, Optional

import boto3

PARTNER_API_URL_ENV = "PARTNER_API_URL"
PARTNER_API_URL_PARAMETER_ENV = "PARTNER_API_URL_PARAMETER"
_parameter_cache: Dict[str, str] = {}
logger = logging.getLogger(__name__)


def _from_parameter_store() -> Optional[str]:
    """Read the stack-published URL once per container; absent or unreadable -> ``None``."""
    name = os.environ.get(PARTNER_API_URL_PARAMETER_ENV)
    if not name:
        return None
    if name in _parameter_cache:
        return _parameter_cache[name]
    try:
        value = boto3.client("ssm").get_parameter(Name=name)["Parameter"]["Value"].strip().rstrip("/")
    except Exception:  # noqa: BLE001 - advertising falls back to the request host
        logger.warning("partner API URL parameter %s unavailable; falling back to request host", name)
        return None
    _parameter_cache[name] = value
    return value


def configured_partner_api_base_url() -> Optional[str]:
    """Return the configured partner API base URL (``PARTNER_API_URL``, then Parameter Store).

    Never derived from a request, so callers that must not trust request headers use this.
    """
    configured = (os.environ.get(PARTNER_API_URL_ENV) or "").strip().rstrip("/")
    if configured:
        return configured
    return _from_parameter_store()


def partner_api_base_url(event: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Return the partner API base URL without a trailing slash, or ``None`` when unknown.

    Prefers ``PARTNER_API_URL`` (tests, custom domains), then the Parameter Store value the stack
    publishes, then the host and stage of the request being served, which is correct whenever the
    caller reached us through the partner API itself.
    """
    configured = configured_partner_api_base_url()
    if configured:
        return configured
    if not event:
        return None
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    request_context = event.get("requestContext") or {}
    host = headers.get("host") or request_context.get("domainName")
    if not host:
        return None
    stage = request_context.get("stage")
    suffix = f"/{stage}" if stage and stage != "$default" else ""
    return f"https://{host}{suffix}"


def partner_api_link(path: str, event: Optional[Dict[str, Any]] = None) -> str:
    """Absolute link to ``path`` on the partner API when the base is known, else the relative path."""
    base = partner_api_base_url(event)
    return f"{base}{path}" if base else path
