"""
/openapi route (#31) — serves the OpenAPI contract to the in-app API reference page.

- `GET /openapi`                    → the full contract (internal view; authed page).
- `GET /openapi?audience=partner`   → the partner view via the shared `derive_partner_spec`
                                       filter (the whole partner-facing product).
- `GET /openapi` on the partner API → the partner-plane document (`derive_partner_plane_spec`):
                                       only the operations that plane routes, native `x-api-key`
                                       scheme, `servers` set to the host that served the request.
                                       The partner API stage sets `api_audience=partner`, so the
                                       plane is recognised from `stageVariables`, never from a
                                       caller-controlled parameter.

Serves the contract packaged with the Lambda zip (the committed SSOT — single source of truth,
no drift). Returns JSON (Scalar/Redoc consume JSON directly). On the internal API, auth is the
Cognito authorizer on the `/{proxy+}` method; on the partner API the route is public by design.
This route adds no envelope so the docs client receives a raw OpenAPI document.
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict

import yaml
from shared.utils.openapi_filter import derive_partner_plane_spec, derive_partner_spec
from shared.utils.response_helpers import cors_headers

logger = logging.getLogger(__name__)

# The SSOT is bundled at the zip root as `openapi-spec.yaml` (packaged by package_lambda.sh).
# routes/openapi.py sits at <zip-root>/routes/, so the zip root is one directory up.
_ROUTES_DIR = os.path.dirname(os.path.abspath(__file__))
_ZIP_ROOT = os.path.dirname(_ROUTES_DIR)
_CANDIDATE_PATHS = [
    os.path.join(_ZIP_ROOT, "openapi-spec.yaml"),   # Lambda zip root (/var/task/openapi-spec.yaml)
    os.path.join(os.getcwd(), "openapi-spec.yaml"),  # runtime CWD fallback
]


def _spec_path() -> str:
    env = os.getenv("OPENAPI_SPEC_PATH")
    if env and os.path.exists(env):
        return env
    for p in _CANDIDATE_PATHS:
        if os.path.exists(p):
            return p
    # Last resort: repo root relative to this file (source/lambda/ui_backend/routes -> repo root)
    return os.path.abspath(
        os.path.join(_ROUTES_DIR, "..", "..", "..", "..", "openapi-spec.yaml")
    )


@lru_cache(maxsize=1)
def _load_spec() -> Dict[str, Any]:
    with open(_spec_path(), "r") as f:
        return yaml.safe_load(f)


def _cors_headers(event: Dict[str, Any] | None = None) -> Dict[str, str]:
    # TM-02: the OpenAPI document is uncredentialed documentation content served to the
    # in-app API reference page (and public on the partner plane), so a wider origin is a
    # deliberate, commented decision — public=True — but still config-driven via the shared
    # resolver (ALLOWED_ORIGINS / SSM), never a bare "*".
    headers = {
        "Content-Type": "application/json",
        # The doc changes only on deploy; allow short caching.
        "Cache-Control": "public, max-age=300",
    }
    headers.update(cors_headers(event, public=True))
    return headers


def _is_partner_plane(event: Dict[str, Any]) -> bool:
    return ((event.get("stageVariables") or {}).get("api_audience") or "").lower() == "partner"


def _server_url(event: Dict[str, Any]) -> str | None:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    host = headers.get("host")
    if not host:
        return None
    stage = (event.get("requestContext") or {}).get("stage")
    return f"https://{host}/{stage}" if stage else f"https://{host}"


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Serve the OpenAPI contract: full, partner view (?audience=partner), or partner-plane doc."""
    try:
        params = event.get("queryStringParameters") or {}
        audience = (params.get("audience") or "").lower()

        spec = _load_spec()
        if _is_partner_plane(event):
            spec = derive_partner_plane_spec(spec, _server_url(event))
        elif audience == "partner":
            spec = derive_partner_spec(spec)

        return {
            "statusCode": 200,
            "headers": _cors_headers(event),
            "body": json.dumps(spec),
        }
    except FileNotFoundError:
        logger.error("OpenAPI spec file not found at %s", _spec_path())
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": {"code": "spec_unavailable", "message": "OpenAPI specification not found"}}),
        }
    except Exception as e:  # noqa: BLE001 - route boundary
        logger.error("Failed to serve OpenAPI spec: %s", str(e))
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": {"code": "spec_error", "message": "Failed to load OpenAPI specification"}}),
        }
