"""MonetizationApi — the two Content Monetization REST APIs, generated from the contract at synth.

SSOT: both API Gateway deploy definitions are DERIVED from ``openapi-spec.yaml`` at ``cdk synth``
rather than hand-maintained. This construct owns the whole contract -> gateway concern: it reads
the committed OpenAPI 3.0 contract, classifies each operation, attaches Lambda integrations,
authentication and cache keys from CDK tokens, and creates two ``SpecRestApi``s:

* **Internal API** (``self.api``): the Cognito-authenticated superset the publisher UI calls.
  The explicit ``PARTNER_EXPLICIT`` paths integrate directly to the ``partner_api`` Lambda (public
  discovery stays public; hybrid operations such as ``/query`` carry ``CognitoAuth`` so the UI can
  call them); everything else is served by the ``/{proxy+}`` -> ``ui_backend`` catch-all (whose
  own ROUTE_TABLE dispatches). The generator VALIDATES that each explicit path exists in the
  contract (fail-closed).
* **Partner API** (``self.partner_api``): the native-API-key plane licensees call. Its operation
  set is derived from the contract (``x-audience: partner`` and either ``PartnerKeyAuth`` or
  anonymous access) minus the deferred operations declared once in
  ``shared.auth.partner_operations``, which the Lambda allow-list filter imports too. Key-gated
  operations require an API Gateway API key (``x-api-key``); public operations have no security.
  There is no ``/{proxy+}`` on this plane, so nothing internal can leak onto it.

Integration URIs are built from CDK tokens passed in by the stack; nothing is committed to source
(OSS-safe). The deploy definitions exist only in the synthesized template.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from aws_cdk import Duration
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Repo-root openapi-spec.yaml.
_CONTRACT_PATH = _REPO_ROOT / "openapi-spec.yaml"

# The partner-plane operation registry is Lambda code; import it so CDK, Lambda and tests share
# one definition of what is deferred / key-gated.
_LAMBDA_SRC = str(_REPO_ROOT / "source" / "lambda")
if _LAMBDA_SRC not in sys.path:
    sys.path.insert(0, _LAMBDA_SRC)
from shared.auth.partner_operations import (  # noqa: E402
    DEFERRED_NO_HANDLER,
    KEY_GATED_OPERATIONS,
)

# Internal API: paths integrated directly to the partner_api Lambda. Kept here as the single
# source; the reachability guardrail imports the same set. Everything else -> /{proxy+} ->
# ui_backend.
PARTNER_EXPLICIT: tuple[str, ...] = ("/info", "/query", "/taxonomy", "/llms.txt")

_PARTNER_METHODS: Dict[str, List[str]] = {
    "/info": ["get"],
    "/query": ["post"],
    "/taxonomy": ["get"],
    "/llms.txt": ["get"],
}
# On the internal plane the only authentication is Cognito: public discovery stays public, the
# hybrid /query requires a Cognito user.
_INTERNAL_COGNITO: Dict[str, bool] = {
    "/info": False,
    "/query": True,
    "/taxonomy": False,
    "/llms.txt": False,
}
# Query params promoted to stage cache keys (declared on the contract; applied at the method).
_CACHE_KEY_PARAMS: Dict[str, List[str]] = {
    "/taxonomy": ["tier_level", "parent_id", "search"],
}

# Partner API: which Lambda serves each operation. Discovery/query live in partner_api; the
# remaining partner operations are ui_backend routes (dispatched by its ROUTE_TABLE).
_PARTNER_API_LAMBDA_PATHS: frozenset[str] = frozenset({"/info", "/query", "/taxonomy", "/llms.txt"})
# Served contract for the partner plane (Req 10); public, -> ui_backend.
PARTNER_OPENAPI_PATH = "/openapi"

CORS_ALLOW_HEADERS = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,If-Match'"
CORS_ALLOW_METHODS = "'GET,POST,PUT,DELETE,OPTIONS'"
_CORS_HEADERS = {
    "method.response.header.Access-Control-Allow-Origin": "'*'",
    "method.response.header.Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
    "method.response.header.Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
}
_CORS_GATEWAY_PARAMS = {
    "gatewayresponse.header.Access-Control-Allow-Origin": "'*'",
    "gatewayresponse.header.Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
    "gatewayresponse.header.Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
}

# Partner API gateway responses (design "Gateway responses"): contract error bodies for the
# failures API Gateway produces before the Lambda runs.
PARTNER_GATEWAY_RESPONSES: Dict[str, Dict[str, Any]] = {
    "INVALID_API_KEY": {
        "statusCode": 403,
        "responseParameters": _CORS_GATEWAY_PARAMS,
        "responseTemplates": {"application/json": '{"error": "Forbidden", "error_code": "invalid_api_key"}'},
    },
    "QUOTA_EXCEEDED": {
        "statusCode": 429,
        "responseParameters": {**_CORS_GATEWAY_PARAMS, "gatewayresponse.header.Retry-After": "'3600'"},
        "responseTemplates": {"application/json": '{"error": "Quota exceeded", "error_code": "quota_exceeded"}'},
    },
    "THROTTLED": {
        "statusCode": 429,
        "responseParameters": {**_CORS_GATEWAY_PARAMS, "gatewayresponse.header.Retry-After": "'1'"},
        "responseTemplates": {"application/json": '{"error": "Too many requests", "error_code": "throttled"}'},
    },
    "DEFAULT_4XX": {"responseParameters": _CORS_GATEWAY_PARAMS},
    "DEFAULT_5XX": {"responseParameters": _CORS_GATEWAY_PARAMS},
}

_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


def _load_contract(contract_path: Path) -> Dict[str, Any]:
    with open(contract_path) as f:
        return yaml.safe_load(f)


def _load_contract_paths(contract_path: Path) -> set[str]:
    return set((_load_contract(contract_path).get("paths") or {}).keys())


def _lambda_uri(region: str, arn: str) -> str:
    return f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{arn}/invocations"


def _proxy_integration(region: str, arn: str, cache_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    integ: Dict[str, Any] = {
        "type": "aws_proxy",
        "httpMethod": "POST",
        "uri": _lambda_uri(region, arn),
        "passthroughBehavior": "when_no_match",
    }
    if cache_keys:
        integ["cacheKeyParameters"] = [f"method.request.querystring.{k}" for k in cache_keys]
    return integ


def _cors_response_headers() -> Dict[str, Any]:
    return {
        "Access-Control-Allow-Origin": {"schema": {"type": "string"}},
        "Access-Control-Allow-Headers": {"schema": {"type": "string"}},
        "Access-Control-Allow-Methods": {"schema": {"type": "string"}},
    }


def _mock_cors_options(parameters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    op: Dict[str, Any] = {
        "summary": "CORS preflight",
        "responses": {"200": {"description": "Success", "headers": _cors_response_headers()}},
        "x-amazon-apigateway-integration": {
            "type": "mock",
            "requestTemplates": {"application/json": '{"statusCode": 200}'},
            "responses": {"default": {"statusCode": "200", "responseParameters": _CORS_HEADERS}},
        },
    }
    if parameters:
        op["parameters"] = parameters
    return op


def _cognito_scheme(cognito_user_pool_arn: str) -> Dict[str, Any]:
    return {
        "type": "apiKey",
        "name": "Authorization",
        "in": "header",
        "x-amazon-apigateway-authtype": "cognito_user_pools",
        "x-amazon-apigateway-authorizer": {"type": "cognito_user_pools", "providerARNs": [cognito_user_pool_arn]},
    }


def build_deploy_definition(
    *,
    region: str,
    partner_api_arn: str,
    ui_backend_arn: str,
    cognito_user_pool_arn: str,
    contract_path: Path = _CONTRACT_PATH,
) -> Dict[str, Any]:
    """Build the OpenAPI 3.0 deploy definition of the **internal** API (Cognito-only).

    Fail-closed: every PARTNER_EXPLICIT path MUST exist in the contract, else raise (a partner
    path was renamed/removed in the contract without updating routing).
    """
    contract_paths = _load_contract_paths(contract_path)
    missing = [p for p in PARTNER_EXPLICIT if p not in contract_paths]
    if missing:
        raise ValueError(
            f"MonetizationApi: PARTNER_EXPLICIT paths missing from openapi-spec.yaml: {missing}. "
            "The deploy definition cannot advertise a path the contract does not define."
        )

    paths: Dict[str, Any] = {}
    for path in PARTNER_EXPLICIT:
        ops: Dict[str, Any] = {}
        cache_keys = _CACHE_KEY_PARAMS.get(path)
        for method in _PARTNER_METHODS[path]:
            op: Dict[str, Any] = {
                "responses": {"200": {"description": "Success"}},
                "x-amazon-apigateway-integration": _proxy_integration(region, partner_api_arn, cache_keys),
            }
            if _INTERNAL_COGNITO[path]:
                op["security"] = [{"CognitoAuth": []}]
            if cache_keys:
                op["parameters"] = [
                    {"name": k, "in": "query", "required": False, "schema": {"type": "string"}} for k in cache_keys
                ]
            ops[method] = op
        # /info carries a CORS OPTIONS served by partner_api (characterized behaviour).
        if path == "/info":
            ops["options"] = {
                "summary": "CORS preflight",
                "responses": {"200": {"description": "Success", "headers": _cors_response_headers()}},
                "x-amazon-apigateway-integration": _proxy_integration(region, partner_api_arn),
            }
        paths[path] = ops

    proxy_param = [{"name": "proxy", "in": "path", "required": True, "schema": {"type": "string"}}]
    paths["/{proxy+}"] = {
        "x-amazon-apigateway-any-method": {
            "summary": "UI Backend proxy for all authenticated routes",
            "security": [{"CognitoAuth": []}],
            "parameters": proxy_param,
            "responses": {"200": {"description": "Success"}},
            "x-amazon-apigateway-integration": _proxy_integration(region, ui_backend_arn),
        },
        "options": _mock_cors_options(proxy_param),
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "AI Content Monetization API",
            "description": "Internal API for AI content monetization and management",
            "version": "1.0.0",
        },
        "components": {"securitySchemes": {"CognitoAuth": _cognito_scheme(cognito_user_pool_arn)}},
        "paths": paths,
    }


def derive_partner_operations(contract: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Return the partner-plane operations, ``(METHOD, path) -> contract operation``.

    An operation is on the partner plane when the contract marks it ``x-audience: partner`` and it
    is reachable with a partner key or anonymously; deferred operations are removed.
    """
    selected: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for path, item in (contract.get("paths") or {}).items():
        for method, op in item.items():
            if method not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            if op.get("x-audience") != "partner":
                continue
            security = op.get("security")
            partner_reachable = security is None or any(req == {} or "PartnerKeyAuth" in req for req in security)
            if not partner_reachable:
                continue
            key = (method.upper(), path)
            if key in DEFERRED_NO_HANDLER:
                continue
            selected[key] = op
    return selected


def _path_parameters(path: str) -> List[Dict[str, Any]]:
    names = [seg[1:-1] for seg in path.split("/") if seg.startswith("{") and seg.endswith("}")]
    return [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}} for n in names]


def build_partner_definition(
    *,
    region: str,
    partner_api_arn: str,
    ui_backend_arn: str,
    contract_path: Path = _CONTRACT_PATH,
) -> Dict[str, Any]:
    """Build the OpenAPI 3.0 deploy definition of the **partner** API (native API keys).

    Fail-closed: every key-gated operation in the registry must be present in the contract, so
    the deploy surface and the Lambda allow-list cannot drift apart.
    """
    contract = _load_contract(contract_path)
    operations = derive_partner_operations(contract)

    missing = [op for op in KEY_GATED_OPERATIONS if op not in operations and op not in DEFERRED_NO_HANDLER]
    if missing:
        raise ValueError(
            f"MonetizationApi: key-gated partner operations missing from openapi-spec.yaml: {missing}. "
            "shared.auth.partner_operations and the contract must agree."
        )

    paths: Dict[str, Any] = {}
    for (method, path), op in operations.items():
        target_arn = partner_api_arn if path in _PARTNER_API_LAMBDA_PATHS else ui_backend_arn
        cache_keys = _CACHE_KEY_PARAMS.get(path)
        deploy_op: Dict[str, Any] = {
            "operationId": op.get("operationId"),
            "responses": {"200": {"description": "Success"}},
            "x-amazon-apigateway-integration": _proxy_integration(region, target_arn, cache_keys),
        }
        parameters: List[Dict[str, Any]] = _path_parameters(path)
        if cache_keys:
            parameters += [
                {"name": k, "in": "query", "required": False, "schema": {"type": "string"}} for k in cache_keys
            ]
        if parameters:
            deploy_op["parameters"] = parameters
        if (method, path) in KEY_GATED_OPERATIONS:
            deploy_op["security"] = [{"PartnerKeyAuth": []}]
            deploy_op["x-amazon-apigateway-api-key-required"] = True
        paths.setdefault(path, {})[method.lower()] = deploy_op

    # Served contract for this plane; ui_backend forces audience=partner (Req 10).
    paths[PARTNER_OPENAPI_PATH] = {
        "get": {
            "operationId": "getPartnerOpenApi",
            "responses": {"200": {"description": "Success"}},
            "x-amazon-apigateway-integration": _proxy_integration(region, ui_backend_arn),
        }
    }

    for path, ops in paths.items():
        ops["options"] = _mock_cors_options(_path_parameters(path) or None)

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "AI Content Monetization Partner API",
            "description": "Partner API for licensed AI content discovery and access",
            "version": "1.0.0",
        },
        "x-amazon-apigateway-api-key-source": "HEADER",
        "x-amazon-apigateway-gateway-responses": PARTNER_GATEWAY_RESPONSES,
        "components": {"securitySchemes": {"PartnerKeyAuth": {"type": "apiKey", "name": "x-api-key", "in": "header"}}},
        "paths": paths,
    }


def _grant_invoke(fn: lambda_.IFunction, cid: str, region: str, account: str, api_id: str) -> None:
    fn.add_permission(
        cid,
        principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
        action="lambda:InvokeFunction",
        source_arn=f"arn:aws:execute-api:{region}:{account}:{api_id}/*/*",
    )


def _cached(ttl_seconds: int) -> apigateway.MethodDeploymentOptions:
    return apigateway.MethodDeploymentOptions(
        caching_enabled=True, cache_ttl=Duration.seconds(ttl_seconds), cache_data_encrypted=True
    )


_CACHED_METHODS = {"/info/GET": _cached(60), "/taxonomy/GET": _cached(120), "/llms.txt/GET": _cached(60)}


class MonetizationApi(Construct):
    """The two Content Monetization REST APIs, generated from openapi-spec.yaml at synth.

    Owns: contract parse -> derive -> integrations/authentication/cache-keys -> two SpecRestApis
    + stage caching + gateway CORS responses + Lambda invoke permissions. Usage plans and API
    keys for the partner API are owned by the stack (they are deployment configuration).
    """

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        region: str,
        account: str,
        partner_api_fn: lambda_.IFunction,
        ui_backend_fn: lambda_.IFunction,
        cognito_user_pool_arn: str,
        contract_path: Path = _CONTRACT_PATH,
    ) -> None:
        super().__init__(scope, cid)

        internal_spec = build_deploy_definition(
            region=region,
            partner_api_arn=partner_api_fn.function_arn,
            ui_backend_arn=ui_backend_fn.function_arn,
            cognito_user_pool_arn=cognito_user_pool_arn,
            contract_path=contract_path,
        )
        partner_spec = build_partner_definition(
            region=region,
            partner_api_arn=partner_api_fn.function_arn,
            ui_backend_arn=ui_backend_fn.function_arn,
            contract_path=contract_path,
        )

        self.api = apigateway.SpecRestApi(
            self,
            "CMApiStack",
            rest_api_name="AI Content Monetization API",
            api_definition=apigateway.ApiDefinition.from_inline(internal_spec),
            deploy_options=apigateway.StageOptions(
                stage_name="v1",
                variables={"api_audience": "internal"},
                throttling_rate_limit=100,
                throttling_burst_limit=200,
                cache_cluster_enabled=True,
                cache_cluster_size="0.5",
                method_options={"/*/*": apigateway.MethodDeploymentOptions(caching_enabled=False), **_CACHED_METHODS},
            ),
        )

        # CORS headers on the internal API's error responses (unchanged behaviour).
        for cid_resp, rtype in (
            ("cors-unauthorized-response", apigateway.ResponseType.UNAUTHORIZED),
            ("cors-access-denied-response", apigateway.ResponseType.ACCESS_DENIED),
            ("cors-default-4xx-response", apigateway.ResponseType.DEFAULT_4_XX),
            ("cors-default-5xx-response", apigateway.ResponseType.DEFAULT_5_XX),
        ):
            self.api.add_gateway_response(
                cid_resp,
                type=rtype,
                response_headers={
                    "Access-Control-Allow-Origin": "'*'",
                    "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
                    "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
                },
            )

        self.partner_api = apigateway.SpecRestApi(
            self,
            "PartnerApi",
            rest_api_name="AI Content Monetization Partner API",
            api_definition=apigateway.ApiDefinition.from_inline(partner_spec),
            deploy_options=apigateway.StageOptions(
                stage_name="v1",
                # Lets ui_backend serve the partner-plane GET /openapi without a caller-controlled
                # parameter and without the API id (which would be a dependency cycle).
                variables={"api_audience": "partner"},
                throttling_rate_limit=100,
                throttling_burst_limit=200,
                cache_cluster_enabled=True,
                cache_cluster_size="0.5",
                method_options={"/*/*": apigateway.MethodDeploymentOptions(caching_enabled=False), **_CACHED_METHODS},
            ),
        )

        # Lambda invoke permissions: both Lambdas, both APIs (internal IDs unchanged from before).
        _grant_invoke(partner_api_fn, "ApiGatewayInvokePartnerApi", region, account, self.api.rest_api_id)
        _grant_invoke(ui_backend_fn, "ApiGatewayInvokeUIBackend", region, account, self.api.rest_api_id)
        _grant_invoke(
            partner_api_fn, "PartnerApiGatewayInvokePartnerApi", region, account, self.partner_api.rest_api_id
        )
        _grant_invoke(ui_backend_fn, "PartnerApiGatewayInvokeUIBackend", region, account, self.partner_api.rest_api_id)

    @property
    def rest_api_id(self) -> str:
        return self.api.rest_api_id

    @property
    def url(self) -> str:
        return self.api.url

    @property
    def partner_rest_api_id(self) -> str:
        return self.partner_api.rest_api_id

    @property
    def partner_url(self) -> str:
        return self.partner_api.url


def partner_operation_keys(contract_path: Path = _CONTRACT_PATH) -> Iterable[Tuple[str, str]]:
    """Convenience for tests and tooling: the derived partner-plane ``(METHOD, path)`` set."""
    return derive_partner_operations(_load_contract(contract_path)).keys()
