# AI Content Monetization API - Makefile
# Usage: AWS_PROFILE=genai make <target>

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

STACK_NAME ?= ContentMonetizationStack
UI_STACK_NAME ?= CMUIStack
COGNITO_STACK_NAME ?= CMCognitoStack
PIPELINE_STACK_NAME ?= CMPipelineStack
SEARCH_STACK_NAME ?= CMSearchStack
CDK ?= npx cdk
# AWS_PROFILE is not defaulted — pass explicitly: AWS_PROFILE=genai make deploy-cdk
PROFILE_FLAG := $(if $(AWS_PROFILE),--profile $(AWS_PROFILE))
# Same profile expressed with the --aws-profile flag used by the mock_data_generator scripts.
SEED_PROFILE_FLAG := $(if $(AWS_PROFILE),--aws-profile $(AWS_PROFILE))
SEED_REGION := $(or $(AWS_REGION),us-west-2)
BACKFILL_DRY_RUN := $(if $(filter true 1 yes,$(DRY_RUN)),true,false)

PUBLISHER_NAME ?= Sample Publisher
MCP_SERVER_PORT ?= 8000
ENABLE_DEMO ?= true
ANALYTICS_AGENT_ARN ?=
MCP_SERVER_ARN ?=

# Derived
PUBLISHER_SLUG = $(shell echo "$(PUBLISHER_NAME)" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
MCP_SERVER_NAME = $(PUBLISHER_SLUG)-content-mcp-server

# Virtual environment
VENV_PATH = .venv
VENV_ACTIVATE = $(VENV_PATH)/bin/activate
PYTHON = $(if $(wildcard $(VENV_ACTIVATE)),$(shell pwd)/$(VENV_PATH)/bin/python,python)
PIP = $(if $(wildcard $(VENV_ACTIVATE)),$(shell pwd)/$(VENV_PATH)/bin/pip,pip)

# Colors
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
NC = \033[0m

.PHONY: help
help: ## Show available targets
	@echo "$(GREEN)AI Content Monetization API$(NC)"
	@echo "Usage: AWS_PROFILE=genai make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-28s$(NC) %s\n", $$1, $$2}'

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

install: ## Install Python dependencies into .venv
	@if [ ! -f $(VENV_ACTIVATE) ]; then python3 -m venv $(VENV_PATH); fi
	$(PIP) install -r requirements.txt -q

setup: install ## Setup MCP server environment from CDK outputs
	cd mcp_server && $(PYTHON) setup_env_from_cdk.py "$(STACK_NAME)" "$(AWS_PROFILE)" "$(PUBLISHER_NAME)"

setup-kiro: ## Configure Kiro MCP integration (local stdio)
	cd mcp_server && $(PYTHON) setup_kiro_mcp.py --type local --publisher "$(PUBLISHER_NAME)"

# ─────────────────────────────────────────────────────────────────────────────
# Lint
# ─────────────────────────────────────────────────────────────────────────────

lint: ## Run ruff linter (informational, non-blocking)
	$(PYTHON) -m ruff check infrastructure/ source/ deployment/ ci/ tests/ app.py || true

lint-strict: ## Run ruff linter (fails on violations)
	$(PYTHON) -m ruff check infrastructure/ source/ deployment/ ci/ tests/ app.py

lint-fix: ## Run ruff with auto-fix + format
	$(PYTHON) -m ruff check --fix infrastructure/ source/ deployment/ ci/ tests/ app.py
	$(PYTHON) -m ruff format infrastructure/ source/ deployment/ ci/ tests/ app.py

security-scan: ## Run ASH locally (same scanners as Probe: bandit, semgrep, gitleaks/detect-secrets, cdk-nag, checkov); honors .ash/.ash.yaml
	ash scan --source-dir . --output-dir .ash/ash_output

install-hooks: ## Install the local git hooks (pre-push ASH security scan)
	chmod +x hooks/pre-push
	ln -sf ../../hooks/pre-push .git/hooks/pre-push
	@echo "Installed pre-push hook -> hooks/pre-push (runs 'make security-scan' before every push)"

# ─────────────────────────────────────────────────────────────────────────────
# Build
# ─────────────────────────────────────────────────────────────────────────────

package-lambda: ## Package all Lambda functions into deployment zips
	@echo "$(GREEN)Packaging all Lambda functions...$(NC)"
	./deployment/infrastructure/package_lambda.sh --function partner-api source/lambda
	./deployment/infrastructure/package_lambda.sh --function ui-backend source/lambda
	./deployment/infrastructure/package_lambda.sh --function shared-services source/lambda
	./deployment/infrastructure/package_lambda.sh --function content-pipeline source/lambda
	./deployment/infrastructure/package_lambda.sh --function llms-txt-worker source/lambda
	./deployment/infrastructure/package_lambda.sh --function search-projection-backfill source/lambda
	@echo "$(GREEN)✅ All Lambda functions packaged$(NC)"

setup-ui-env: ## Generate source/ui/.env from deployed stack outputs
	@echo "$(GREEN)Configuring UI environment from CDK stack outputs...$(NC)"
	@$(eval EXTRACTED_MCP_ARN := $(shell cd mcp_server && if [ -f publisher_mcp_arn.txt ]; then cat publisher_mcp_arn.txt; elif [ -f agent_arn.txt ]; then cat agent_arn.txt; else echo ""; fi))
	@$(eval EXTRACTED_ANALYTICS_ARN := $(shell cd analytics_agent && if [ -f analytics_agent_arn.txt ]; then cat analytics_agent_arn.txt; elif [ -f agent_arn.txt ]; then cat agent_arn.txt; else echo ""; fi))
	$(PYTHON) internal-assets/scripts/configure-ui-env.py \
		--main-stack $(STACK_NAME) \
		--ui-stack $(UI_STACK_NAME) \
		$(PROFILE_FLAG) \
		$(if $(or $(ANALYTICS_AGENT_ARN),$(EXTRACTED_ANALYTICS_ARN)),--analytics-agent-arn $(or $(ANALYTICS_AGENT_ARN),$(EXTRACTED_ANALYTICS_ARN))) \
		$(if $(or $(MCP_SERVER_ARN),$(EXTRACTED_MCP_ARN)),--mcp-server-arn $(or $(MCP_SERVER_ARN),$(EXTRACTED_MCP_ARN))) \
		$(if $(ENABLE_DEMO),--enable-demo,--disable-demo)

build-ui: setup-ui-env ## Build UI assets (generates .env then npm build)
	@echo "$(GREEN)Building UI assets...$(NC)"
	cd source/ui && npm install && npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Deploy
# ─────────────────────────────────────────────────────────────────────────────

deploy-backend: ## Deploy Cognito + main CDK stack (assumes lambda zips exist)
	@echo "$(GREEN)Deploying Cognito stack...$(NC)"
	@CURRENT_ACCOUNT=$$(aws sts get-caller-identity $(PROFILE_FLAG) --query Account --output text) && \
	echo "$(YELLOW)Using account: $$CURRENT_ACCOUNT$(NC)" && \
	AWS_DEFAULT_REGION=$(or $(AWS_REGION),us-west-2) $(CDK) deploy $(COGNITO_STACK_NAME) --require-approval never \
		-c account=$$CURRENT_ACCOUNT -c region=$(or $(AWS_REGION),us-west-2) \
		$(PROFILE_FLAG) && \
	echo "$(GREEN)✅ Cognito stack deployed$(NC)" && \
	echo "$(GREEN)Deploying main CDK stack with Cognito parameters...$(NC)" && \
	COGNITO_POOL_ID=$$(aws cloudformation describe-stacks --stack-name $(COGNITO_STACK_NAME) \
		$(PROFILE_FLAG) --region $(or $(AWS_REGION),us-west-2) \
		--query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text) && \
	COGNITO_CLIENT_ID=$$(aws cloudformation describe-stacks --stack-name $(COGNITO_STACK_NAME) \
		$(PROFILE_FLAG) --region $(or $(AWS_REGION),us-west-2) \
		--query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' --output text) && \
	COGNITO_IDENTITY_ID=$$(aws cloudformation describe-stacks --stack-name $(COGNITO_STACK_NAME) \
		$(PROFILE_FLAG) --region $(or $(AWS_REGION),us-west-2) \
		--query 'Stacks[0].Outputs[?OutputKey==`IdentityPoolId`].OutputValue' --output text) && \
	echo "$(YELLOW)Cognito Pool: $$COGNITO_POOL_ID, Client: $$COGNITO_CLIENT_ID$(NC)" && \
	AWS_DEFAULT_REGION=$(or $(AWS_REGION),us-west-2) $(CDK) deploy $(STACK_NAME) --require-approval never \
		-c account=$$CURRENT_ACCOUNT -c region=$(or $(AWS_REGION),us-west-2) \
		$(PROFILE_FLAG) \
		--parameters CognitoUserPoolId=$$COGNITO_POOL_ID \
		--parameters CognitoUserPoolClientId=$$COGNITO_CLIENT_ID \
		--parameters CognitoIdentityPoolId=$$COGNITO_IDENTITY_ID && \
	echo "$(GREEN)✅ CDK deployment completed$(NC)"
	@echo "$(YELLOW)Migrating IAB taxonomy data to DynamoDB...$(NC)"
	@make migrate-taxonomy
	@echo "$(YELLOW)Seeding sample data into cm-* tables (Deploy_Path Seed_Step)...$(NC)"
	@make seed-data CLEAR_EXISTING=true

deploy-cdk: package-lambda deploy-backend ## Package lambdas + deploy backend (local convenience)

deploy-pipeline: ## Deploy GEO enrichment pipeline + top-level search stack (assumes main stack + lambda zips exist)
	@echo "$(GREEN)Deploying GEO enrichment pipeline stack...$(NC)"
	@CURRENT_ACCOUNT=$$(aws sts get-caller-identity $(PROFILE_FLAG) --query Account --output text) && \
	AWS_DEFAULT_REGION=$(or $(AWS_REGION),us-west-2) $(CDK) deploy $(PIPELINE_STACK_NAME) --exclusively --require-approval never \
		-c account=$$CURRENT_ACCOUNT -c region=$(or $(AWS_REGION),us-west-2) \
		$(PROFILE_FLAG)
	@echo "$(GREEN)✅ Pipeline stack deployed$(NC)"
	@echo "$(GREEN)Deploying top-level search collection stack...$(NC)"
	@# Order matters: CMPipelineStack is deployed FIRST (it removes any pre-existing
	@# nested Search sub-stack, freeing the fixed cm-content-search collection/policy
	@# names, and creates the pipeline writer roles). CMSearchStack is deployed SECOND
	@# (it creates the collection in a free-standing top-level stack — isolated so a
	@# SolutionVersion tag bump cannot replace the collection — and its AOSS
	@# data-access policy references the pipeline writer roles that now exist).
	@CURRENT_ACCOUNT=$$(aws sts get-caller-identity $(PROFILE_FLAG) --query Account --output text) && \
	AWS_DEFAULT_REGION=$(or $(AWS_REGION),us-west-2) $(CDK) deploy $(SEARCH_STACK_NAME) --exclusively --require-approval never \
		-c account=$$CURRENT_ACCOUNT -c region=$(or $(AWS_REGION),us-west-2) \
		$(PROFILE_FLAG)
	@echo "$(GREEN)✅ Search stack deployed$(NC)"

deploy-ui-stack: ## Deploy UI CDK stack (assumes ui/dist already built)
	@echo "$(GREEN)Deploying UI stack...$(NC)"
	@CURRENT_ACCOUNT=$$(aws sts get-caller-identity $(PROFILE_FLAG) --query Account --output text) && \
	AWS_DEFAULT_REGION=$(or $(AWS_REGION),us-west-2) $(CDK) deploy $(UI_STACK_NAME) --require-approval never \
		-c account=$$CURRENT_ACCOUNT -c region=$(or $(AWS_REGION),us-west-2) \
		$(PROFILE_FLAG)
	@echo "$(GREEN)✅ UI stack deployed$(NC)"

deploy-ui: build-ui deploy-ui-stack ## Build UI + deploy to CloudFront

deploy-agentcore: setup ## Deploy MCP server to AgentCore
	cd mcp_server && AWS_DEFAULT_REGION=us-west-2 $(PYTHON) deploy.py

deploy-analytics-agent: ## Deploy analytics agent to AgentCore
	cd analytics_agent && AWS_DEFAULT_REGION=us-west-2 $(PYTHON) deploy.py

deploy-full-stack: deploy-cdk deploy-agentcore deploy-analytics-agent deploy-ui ## Deploy complete stack

# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────

migrate-taxonomy: ## Migrate IAB taxonomy TSV to DynamoDB
	@echo "$(GREEN)Migrating IAB taxonomy data to DynamoDB...$(NC)"
	$(PYTHON) deployment/data/migrate_taxonomy_to_db.py

generate-mock-data: migrate-taxonomy ## Generate mock data for DynamoDB tables
	@echo "$(GREEN)Generating mock data...$(NC)"
	PYTHONPATH=source $(PYTHON) source/mock_data_generator/generate_mock_data.py \
		--aws-region $(SEED_REGION) $(SEED_PROFILE_FLAG) \
		--content-count $(or $(CONTENT_COUNT),20) \
		--partner-count $(or $(PARTNER_COUNT),5) \
		--access-log-count $(or $(ACCESS_LOG_COUNT),100) \
		--job-count $(or $(JOB_COUNT),30) \
		--stack-name $(STACK_NAME) \
		$(if $(CLEAR_EXISTING),--clear-existing)

seed-data: ## Deploy_Path Seed_Step: seed all cm-* tables (set CLEAR_EXISTING=true to reset first)
	@echo "$(GREEN)[1/3] Seeding content/partners/jobs/analytics/access-logs/pricing tables...$(NC)"
	PYTHONPATH=source $(PYTHON) source/mock_data_generator/generate_mock_data.py \
		--aws-region $(SEED_REGION) $(SEED_PROFILE_FLAG) \
		--content-count $(or $(CONTENT_COUNT),20) \
		--partner-count $(or $(PARTNER_COUNT),5) \
		--access-log-count $(or $(ACCESS_LOG_COUNT),100) \
		--job-count $(or $(JOB_COUNT),30) \
		--stack-name $(STACK_NAME) \
		$(if $(CLEAR_EXISTING),--clear-existing)
	@echo "$(GREEN)[2/3] Seeding sample partners with known API keys...$(NC)"
	$(PYTHON) deployment/data/add-sample-partners.py --region $(SEED_REGION) $(PROFILE_FLAG) --stack-name $(STACK_NAME)
	@echo "$(GREEN)[3/3] Seeding bot-rules (generator path, no live WAF) and licenses...$(NC)"
	PYTHONPATH=source $(PYTHON) source/mock_data_generator/seed_bot_rules_and_licenses.py \
		--aws-region $(SEED_REGION) $(SEED_PROFILE_FLAG) \
		$(if $(CLEAR_EXISTING),--clear-existing)
	@echo "$(GREEN)✅ Seed_Step complete$(NC)"

backfill-search-projection: ## Explicitly rebuild OpenSearch documents from published content artifacts
	@FUNCTION_NAME=$$(aws cloudformation describe-stacks --stack-name $(PIPELINE_STACK_NAME) \
		$(PROFILE_FLAG) --region $(SEED_REGION) \
		--query 'Stacks[0].Outputs[?OutputKey==`SearchProjectionBackfillLambdaName`].OutputValue' --output text) && \
	if [ -z "$$FUNCTION_NAME" ] || [ "$$FUNCTION_NAME" = "None" ]; then \
		echo "$(RED)Search projection backfill Lambda output not found$(NC)"; exit 1; \
	fi && \
	PAYLOAD=$$(mktemp) && \
	aws lambda invoke --function-name "$$FUNCTION_NAME" $(PROFILE_FLAG) --region $(SEED_REGION) \
		--cli-binary-format raw-in-base64-out \
		--payload '{"dry_run": $(BACKFILL_DRY_RUN)}' "$$PAYLOAD" >/dev/null && \
	cat "$$PAYLOAD" && rm -f "$$PAYLOAD"

clean-mock-data: ## Clear all mock data from DynamoDB tables
	@echo "$(RED)⚠️  This will delete ALL data from DynamoDB tables$(NC)"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ] || exit 1
	$(PYTHON) -c "from mock_data_generator.config import GeneratorConfig; from mock_data_generator.generator import MockDataGenerator; g = MockDataGenerator(GeneratorConfig(aws_profile='$(AWS_PROFILE)')); g.clear_all_tables()"

sync-waf-rules: ## Import existing WAF rules into DynamoDB bot rules table
	$(PYTHON) deployment/data/import_waf_rules.py $(PROFILE_FLAG) --table-name cm-bot-rules --web-acl-name iab-bot-control-webacl

# ─────────────────────────────────────────────────────────────────────────────
# Test
# ─────────────────────────────────────────────────────────────────────────────

# Gated Python test suites — single source of truth for local/CI parity (CI runs the exact same
# `make test` a developer runs locally). Extended by the test-suite-overhaul effort (#11, Req 4.2)
# to the full green gated set. Per-test quarantines (live-AWS, behavior-drift now pinned by the
# route Characterization_Tests, stale-structure, math/generator drift) are applied centrally by the
# documented node-id registry in tests/quarantine_registry.py via the tests/conftest.py
# collection hook — so no per-command --ignore/--deselect excludes are needed. Per-suite
# GATE/QUARANTINE/DELETE decisions are recorded in
# .kiro/specs/test-suite-overhaul/suite-decisions.md.
#
# CI's `test` job `needs: build-backend`, so source/lambda/*.zip artifacts exist for the
# infrastructure (CDK asset), AST zip-import validator, and deployed-import fidelity checks.
# Run `make package-lambda` first when running locally.
TEST_SUITES := \
	tests/pricing \
	tests/ui_backend \
	tests/partner_api \
	tests/analytics \
	tests/content_management \
	tests/processing_status \
	tests/mock_data_generator \
	tests/content_pipeline \
	tests/services \
	tests/shared \
	tests/infrastructure \
	tests/bot_control \
	tests/import_guard \
	tests/integration_adapters \
	tests/packaging \
	tests/auth \
	tests/authentication \
	tests/api_consistency \
	tests/api_spec \
	tests/ui \
	tests/taxonomy \
	tests/test_deployed_imports.py

.PHONY: test-edge
test-edge: ## Run CloudFront Function unit and static-safety tests
	node --experimental-loader ./tests/edge/cloudfront-loader.mjs --test tests/edge/*.test.mjs

.PHONY: test
test: test-edge ## Run this spec's gated test suites (blocking; JUnit report); fails if a listed suite is missing or empty
	@echo "$(GREEN)Preflight: verifying each gated suite exists and collects tests...$(NC)"
	@for s in $(TEST_SUITES); do \
		if [ ! -e "$$s" ]; then echo "$(RED)ERROR gated suite missing from repo: $$s$(NC)"; exit 1; fi; \
		out=$$($(PYTHON) -m pytest "$$s" --collect-only -q -o addopts= -m "not integration" -p no:cacheprovider 2>&1); \
		n=$$(printf '%s\n' "$$out" | grep -c "::"); \
		if [ "$$n" -eq 0 ]; then echo "$(RED)ERROR gated suite collects zero Unit_Tier tests: $$s$(NC)"; echo "$(RED)--- collection output (root cause) ---$(NC)"; printf '%s\n' "$$out" | tail -20; exit 1; fi; \
		echo "  $(GREEN)ok$(NC) $$s ($$n tests)"; \
	done
	@echo "$(GREEN)Running gated test suites (Unit_Tier: -m 'not integration')...$(NC)"
	$(PYTHON) -m pytest $(TEST_SUITES) -m "not integration" -q --timeout=60 --junitxml=test-results.xml -p no:cacheprovider

.PHONY: coverage
coverage: ## Non-gating per-module Python coverage (visibility only; no threshold — GitLab #11 Req 11)
	-$(PYTHON) -m pytest $(TEST_SUITES) -q --timeout=60 -p no:cacheprovider \
		--cov=source/lambda --cov=source/mock_data_generator \
		--cov-report=term-missing --cov-report=xml:coverage.xml --cov-report=html:htmlcov

INTEGRATION_SUITES := tests/integration tests/content_management tests/api_consistency tests/auth tests/taxonomy

test-integration: ## Run the Integration_Tier (-m integration) against a deployed environment
	$(PYTHON) -m pytest $(INTEGRATION_SUITES) -m integration -v --timeout=120 --junitxml=integration-results.xml -p no:cacheprovider

test-pipeline-integration: ## Run ingest→classify→index→search integration test (live AWS)
	RUN_INTEGRATION=1 AWS_REGION=$(or $(AWS_REGION),us-west-2) $(PYTHON) -m pytest tests/integration/test_ingest_to_search.py -v

test-auth-flows: ## Test dual authentication flows
	$(PYTHON) -m pytest tests/auth/test_dual_auth_integration.py -v

test-lambda-deployment: ## Test deployed Lambda functions via API Gateway
	@$(eval API_URL := $(shell aws cloudformation describe-stacks --stack-name $(STACK_NAME) $(PROFILE_FLAG) --query 'Stacks[0].Outputs[?OutputKey==`ApiGatewayUrl`].OutputValue' --output text 2>/dev/null))
	@if [ -n "$(API_URL)" ]; then \
		echo "$(GREEN)API: $(API_URL)$(NC)"; \
		curl -s "$(API_URL)/info" | python3 -m json.tool; \
	else \
		echo "$(RED)❌ Could not retrieve API Gateway URL$(NC)"; \
	fi

# ─────────────────────────────────────────────────────────────────────────────
# Run (local development)
# ─────────────────────────────────────────────────────────────────────────────

run-local: ## Run MCP server locally (stdio for Kiro)
	cd mcp_server && MCP_TRANSPORT=stdio $(PYTHON) mcp_server.py

run-http: ## Run MCP server in HTTP mode (for curl/testing)
	cd mcp_server && MCP_HTTP_MODE=true MCP_SERVER_PORT=$(MCP_SERVER_PORT) $(PYTHON) mcp_server.py

# ─────────────────────────────────────────────────────────────────────────────
# Publish (OSS)
# ─────────────────────────────────────────────────────────────────────────────

create-oss-dist: ## Create open-source distribution in dist/
	./ci/create-oss-distribution.sh $(or $(VERSION),dev)

publish-github: ## Publish distribution to GitHub
	./ci/publish-to-github.sh --version $(or $(VERSION),dev) $(PROFILE_FLAG)

# ─────────────────────────────────────────────────────────────────────────────
# CDK Nag
# ─────────────────────────────────────────────────────────────────────────────

cdk-nag-report: ## Generate CDK Nag JUnit report
	$(PYTHON) ci/generate_cdk_nag_junit.py

# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

status: ## Show deployment status
	@echo "$(GREEN)Deployment Status$(NC)"
	@aws cloudformation describe-stacks --stack-name $(STACK_NAME) $(PROFILE_FLAG) --query 'Stacks[0].StackStatus' --output text 2>/dev/null | sed 's/^/  Main Stack: /' || echo "  Main Stack: Not found"
	@aws cloudformation describe-stacks --stack-name $(COGNITO_STACK_NAME) $(PROFILE_FLAG) --query 'Stacks[0].StackStatus' --output text 2>/dev/null | sed 's/^/  Cognito Stack: /' || echo "  Cognito Stack: Not found"
	@aws cloudformation describe-stacks --stack-name $(UI_STACK_NAME) $(PROFILE_FLAG) --query 'Stacks[0].StackStatus' --output text 2>/dev/null | sed 's/^/  UI Stack: /' || echo "  UI Stack: Not found"
	@aws cloudformation describe-stacks --stack-name $(UI_STACK_NAME) $(PROFILE_FLAG) --query 'Stacks[0].Outputs[?OutputKey==`UIUrl`].OutputValue' --output text 2>/dev/null | sed 's/^/  UI URL: /' || echo "  UI URL: Not available"

clean: ## Remove generated files and caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f source/lambda/*.zip 2>/dev/null || true
	rm -rf cdk.out/ 2>/dev/null || true
