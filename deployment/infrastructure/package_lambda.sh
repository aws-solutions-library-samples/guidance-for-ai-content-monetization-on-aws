#!/bin/bash
set -e

# Lambda packaging script for AI Content Monetization API
# Supports packaging individual Lambda functions and shared services

# Resolve project root and pip from venv
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIP="${PROJECT_ROOT}/.venv/bin/pip"

require_pip() {
    if [ ! -f "$PIP" ]; then
        echo "Error: Virtual environment pip not found at $PIP"
        echo "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS] <source_path>"
    echo ""
    echo "Options:"
    echo "  --function <name>    Package specific function"
    echo "  --help              Show this help message"
    echo ""
    echo "Available functions:"
    echo "  partner-api         Package Partner API Lambda"
    echo "  ui-backend          Package UI Backend Lambda"
    echo "  shared-services     Package shared services (auth, analytics, etc.)"
    echo "  content-pipeline    Package GEO content pipeline Lambda"
    echo "  llms-txt-worker     Package managed llms.txt worker Lambda"
    echo "  search-projection-backfill  Package explicit search projection migration Lambda"
    echo "  content-deindexer   Package the content delete deindexer Lambda"
    echo "  all                 Package all functions"
    echo ""
    echo "Examples:"
    echo "  $0 --function partner-api lambda"
    echo "  $0 --function ui-backend lambda"
    echo "  $0 --function shared-services lambda"
    echo "  $0 --function all lambda"
}

# Parse command line arguments
FUNCTION_NAME=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --function)
            FUNCTION_NAME="$2"
            shift 2
            ;;
        --help)
            show_usage
            exit 0
            ;;
        -*)
            echo "Unknown option $1"
            show_usage
            exit 1
            ;;
        *)
            SOURCE_PATH="$1"
            shift
            ;;
    esac
done

# Validate source path
if [ -z "$SOURCE_PATH" ]; then
    echo "Error: source_path is required"
    show_usage
    exit 1
fi

if [ ! -d "$SOURCE_PATH" ]; then
    echo "Error: Source path '$SOURCE_PATH' does not exist"
    exit 1
fi

# Function to package a specific Lambda function
package_function() {
    local func_name=$1
    local func_dir=$2
    local output_zip=$3

    require_pip
    
    echo "📦 Packaging $func_name Lambda function..."
    
    # Store the original working directory
    local original_cwd=$(pwd)
    
    # Create temporary directory for packaging
    local temp_dir=$(mktemp -d)
    local python_dir="$temp_dir/python"
    
    # Install shared dependencies first
    if [ -f "$SOURCE_PATH/shared/requirements.txt" ]; then
        echo "  Installing shared dependencies..."
        cd "$original_cwd"
        $PIP install --target "$python_dir" -r "$SOURCE_PATH/shared/requirements.txt" --quiet --upgrade
    fi
    
    # Install function-specific dependencies
    if [ -f "$func_dir/requirements.txt" ]; then
        echo "  Installing $func_name dependencies..."
        cd "$original_cwd"
        $PIP install --target "$python_dir" -r "$func_dir/requirements.txt" --quiet --upgrade
    fi
    
    # Create the deployment package
    cd "$temp_dir"
    
    # Add Python dependencies
    if [ -d "$python_dir" ]; then
        # Clean up __pycache__ directories before zipping
        find "$python_dir" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find "$python_dir" -name "*.pyc" -delete 2>/dev/null || true
        
        cd python
        zip -r "../$output_zip" . --quiet
        cd ..
    fi
    
    # Add shared modules
    if [ -d "$SOURCE_PATH/shared" ]; then
        echo "  Adding shared modules..."
        cd "$SOURCE_PATH"
        zip -r "$temp_dir/$output_zip" shared/ -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
        cd "$temp_dir"
    fi
    
    # Add the root-level services/ package (needed by many handlers). There is no
    # source/lambda/utils directory — the former "services utils" loop iterated a ghost of the
    # fixed phantom-utils bug (GitLab #11 Req 8). Shared utilities live under shared/utils/ and are
    # bundled via the shared/ tree, not a root-level utils/.
    echo "  Adding root-level services..."
    cd "$temp_dir"
    if [ -d "$SOURCE_PATH/services" ]; then
        cd "$SOURCE_PATH"
        zip -r "$temp_dir/$output_zip" "services/" -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
        cd "$temp_dir"
    fi

    # Add the root-level integrations/ package (the integration-adapters layer, GitLab #35). It
    # must sit at the zip root so `from integrations...` resolves at runtime (Requirement 1.5) —
    # e.g. ui_backend/routes/connections.py imports `from integrations.registry import Registry`.
    # The ports depend on nothing external; the adapters/registry are pure-Python + stdlib/boto3,
    # so no extra pip deps are needed here.
    echo "  Adding root-level integrations..."
    cd "$temp_dir"
    if [ -d "$SOURCE_PATH/integrations" ]; then
        cd "$SOURCE_PATH"
        zip -r "$temp_dir/$output_zip" "integrations/" -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
        cd "$temp_dir"
    fi
    
    # Add function-specific code
    echo "  Adding $func_name handler code..."
    if [ -d "$func_dir" ]; then
        cd "$func_dir"
        # Exclude deprecated files when adding Python files
        for py_file in *.py; do
            if [[ -f "$py_file" && "$py_file" != *"_deprecated"* ]]; then
                zip -r "$temp_dir/$output_zip" "$py_file" --quiet
            fi
        done
        cd "$temp_dir"
    else
        echo "  Warning: Function directory not found: $func_dir"
    fi
    
    # Add commonly imported root-level Python files
    echo "  Adding commonly imported root-level modules..."
    cd "$temp_dir"
    common_modules=(
        "analytics_api.py"
        "authentication_service.py"
        "access_logger.py"
        "access_rules_engine.py"
        "analytics_aggregation.py"
    )
    
    for module in "${common_modules[@]}"; do
        if [ -f "$SOURCE_PATH/$module" ] && [[ "$module" != *"_deprecated"* ]]; then
            cd "$SOURCE_PATH"
            zip -r "$temp_dir/$output_zip" "$module" --quiet
            cd "$temp_dir"
        fi
    done
    
    # Special handling for UI Backend - include the routes/ package
    if [[ "$func_name" == *"UI Backend"* ]]; then
        # Include the routes/ package (thin dispatcher route modules) recursively.
        # handler.py imports `from routes import resolve_route`, so routes/ must sit at the
        # zip root alongside handler.py. Zipping from within the function dir stores the
        # package as routes/... at the archive root, preserving nested subpackages and
        # __init__.py. Exclude __pycache__ dirs and *.pyc files.
        echo "  Adding routes/ package for UI Backend..."
        if [ -d "$func_dir/routes" ]; then
            cd "$func_dir"
            zip -r "$temp_dir/$output_zip" routes/ -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" --quiet
            cd "$temp_dir"
        else
            echo "  Warning: routes/ directory not found in $func_dir"
        fi

        # Bundle the OpenAPI SSOT at the zip root so the /openapi meta-route (#31) can serve the
        # committed contract at runtime (no drift; single source of truth). routes/openapi.py
        # locates it relative to the zip root.
        echo "  Adding openapi-spec.yaml for UI Backend /openapi route..."
        if [ -f "$PROJECT_ROOT/openapi-spec.yaml" ]; then
            cd "$PROJECT_ROOT"
            zip "$temp_dir/$output_zip" openapi-spec.yaml --quiet
            cd "$temp_dir"
        else
            echo "  Warning: openapi-spec.yaml not found at $PROJECT_ROOT"
        fi
    fi
    
    # Move the final package to the source directory
    mv "$temp_dir/$output_zip" "$SOURCE_PATH/"
    
    # Return to original directory before cleanup
    cd "$original_cwd"
    
    # Clean up
    rm -rf "$temp_dir"
    
    echo "  ✅ $func_name package created: $SOURCE_PATH/$output_zip"
}

# Function to package shared services (authentication, analytics, etc.)
package_shared_services() {
    echo "📦 Packaging shared services (authentication, analytics, utilities)..."
    require_pip
    
    # Ensure we're in the right directory
    cd "$SOURCE_PATH"
    
    # Clean up previous builds
    rm -rf python
    rm -rf shared_services_deployment.zip
    
    # Install dependencies from root requirements.txt
    if [ -f "./requirements.txt" ]; then
        echo "  Installing dependencies from root requirements.txt..."
        $PIP install --target python -r ./requirements.txt --quiet
    fi
    
    # Create the deployment package
    if [ -d "python" ]; then
        cd python
        zip -r ../shared_services_deployment.zip . -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" --quiet
        cd ..
    fi
    
    # Add shared services files
    echo "  Adding shared service files..."
    shared_service_files=(
        "authentication_service.py"
        "access_logger.py"
        "access_rules_engine.py"
        "analytics_aggregation.py"
        # (content_management.py was removed: the content domain's logic now lives directly in
        # services/content_service.py, so there is no root module to bundle. bot_control_api.py was
        # likewise removed: the bot-control domain's logic now lives directly in
        # services/bot_control_service.py, so there is no root module to bundle. processing_status.py
        # was likewise removed: the processing domain's logic now lives directly in
        # services/processing_jobs_service.py, so there is no root module to bundle.)
        # `shared/bedrock_embeddings.py` ships via the whole shared/ tree zipped below.
    )
    
    for file in "${shared_service_files[@]}"; do
        if [ -f "$file" ] && [[ "$file" != *"_deprecated"* ]]; then
            zip -r shared_services_deployment.zip "$file" --quiet
        fi
    done
    
    # Add shared modules
    if [ -d "shared" ]; then
        echo "  Adding shared modules..."
        zip -r shared_services_deployment.zip shared/ -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
    fi
    
    # Add the services/ package (no source/lambda/utils exists — the former "utils services" loop
    # iterated a ghost of the fixed phantom-utils bug, GitLab #11 Req 8; shared utilities are
    # bundled under shared/utils/ via the shared/ tree above).
    if [ -d "services" ]; then
        echo "  Adding services directory..."
        zip -r shared_services_deployment.zip "services/" -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
    fi

    # The integrations/ package, required because services/usage_emitter.py imports
    # `from integrations.registry import ...` and `from integrations.telemetry.port import ...`.
    # Without it this zip raises ModuleNotFoundError at runtime. The generic package_function
    # path bundles integrations/ the same way -- this branch had been missed.
    if [ -d "integrations" ]; then
        echo "  Adding integrations directory..."
        zip -r shared_services_deployment.zip "integrations/" -x "*.pyc" "*/__pycache__/*" "**/__pycache__/*" "__pycache__/*" "*_deprecated*" --quiet
    fi
    
    echo "  ✅ Shared services package created: $SOURCE_PATH/shared_services_deployment.zip"
}

# Function to package the GEO content pipeline Lambda (lean, own deps)
package_content_pipeline() {
    echo "📦 Packaging Content Pipeline Lambda function..."
    require_pip

    local original_cwd=$(pwd)
    local temp_dir=$(mktemp -d)
    local output_zip="content_pipeline_deployment.zip"

    # Pipeline-specific dependencies (markdownify, opensearch-py)
    $PIP install --target "$temp_dir/python" -r "$SOURCE_PATH/content_pipeline/requirements.txt" --quiet --upgrade

    cd "$temp_dir/python"
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    zip -r "../$output_zip" . --quiet
    cd "$temp_dir"

    # handler.py stays at the zip root (CDK entry `handler.lambda_handler`, unchanged).
    # extractor.py / geo_enricher.py move under a content_pipeline/ package inside the zip so
    # handler.py's canonical-root-qualified imports (`from content_pipeline.extractor import ...`,
    # `from content_pipeline.geo_enricher import ...`) resolve at runtime under the zip-root layout,
    # matching how they resolve against source/lambda/ in dev/test.
    cd "$SOURCE_PATH/content_pipeline"
    zip "$temp_dir/$output_zip" handler.py --quiet
    cd "$SOURCE_PATH"
    zip "$temp_dir/$output_zip" content_pipeline/__init__.py content_pipeline/extractor.py \
        content_pipeline/geo_enricher.py content_pipeline/markdown_renderer.py --quiet

    # Taxonomy TSV next to geo_enricher.py inside the content_pipeline/ package (geo_enricher
    # loads it via a __file__-relative path, so it must sit beside the module in the zip).
    stage_tsv="$temp_dir/tsv_stage/content_pipeline"
    mkdir -p "$stage_tsv"
    cp "$PROJECT_ROOT/assets/iab-content-taxonomy.tsv" "$stage_tsv/"
    cd "$temp_dir/tsv_stage"
    zip "$temp_dir/$output_zip" content_pipeline/iab-content-taxonomy.tsv --quiet

    # Reused modules
    cd "$SOURCE_PATH"
    zip "$temp_dir/$output_zip" services/__init__.py services/opensearch_client.py \
        services/search_projection.py --quiet
    # bedrock_embeddings now lives under shared/ (shared/bedrock_embeddings.py). This function
    # bundles individual shared files by explicit path (it does NOT zip -r shared/), so the moved
    # module is added explicitly below alongside shared/__init__.py so `shared` is a package.
    zip "$temp_dir/$output_zip" shared/__init__.py shared/bedrock_embeddings.py shared/content_delete.py \
        shared/utils/__init__.py shared/utils/models.py \
        shared/utils/canonical_url.py shared/utils/edge_address.py \
        shared/utils/feature_flags.py shared/utils/response_helpers.py shared/utils/response_validator.py \
        shared/utils/safe_datetime_parser.py --quiet

    mv "$temp_dir/$output_zip" "$SOURCE_PATH/"
    cd "$original_cwd"
    rm -rf "$temp_dir"

    echo "  ✅ Content Pipeline package created: $SOURCE_PATH/$output_zip"
}

package_llms_txt_worker() {
    echo "📦 Packaging managed llms.txt worker..."
    local original_cwd=$(pwd)
    local temp_dir=$(mktemp -d)
    local python_dir="$temp_dir/python"
    local output_zip="llms_txt_worker_deployment.zip"

    # Install shared runtime deps (PyYAML etc.) — llms_txt_service imports
    # shared.utils.partner_api_url, which triggers shared/utils/__init__.py; that package eagerly
    # imports response_validator (import yaml), so PyYAML must be present or the worker fails to
    # import. Mirror the shared-services packaging.
    if [ -f "$SOURCE_PATH/shared/requirements.txt" ]; then
        echo "  Installing shared dependencies..."
        $PIP install --target "$python_dir" -r "$SOURCE_PATH/shared/requirements.txt" --quiet --upgrade
    fi
    if [ -d "$python_dir" ]; then
        find "$python_dir" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
        find "$python_dir" -name "*.pyc" -delete 2>/dev/null || true
        cd "$python_dir"
        zip -r "$temp_dir/$output_zip" . --quiet
    fi

    cd "$SOURCE_PATH/llms_txt_worker"
    zip "$temp_dir/$output_zip" handler.py --quiet
    cd "$SOURCE_PATH"
    zip "$temp_dir/$output_zip" services/__init__.py services/llms_txt_service.py --quiet
    # Bundle exactly the shared.utils import closure the worker needs. Importing
    # shared.utils.partner_api_url executes shared/utils/__init__.py, which eagerly imports
    # response_helpers, response_validator and safe_datetime_parser — all must be present. Do NOT
    # bundle the whole shared/ tree: shared/auth/* references the root-level authentication_service
    # module, which the worker does not ship (and the zip-import validator would flag).
    zip "$temp_dir/$output_zip" shared/__init__.py shared/utils/__init__.py \
        shared/utils/partner_api_url.py shared/utils/response_helpers.py \
        shared/utils/response_validator.py shared/utils/safe_datetime_parser.py --quiet

    mv "$temp_dir/$output_zip" "$SOURCE_PATH/"
    cd "$original_cwd"
    rm -rf "$temp_dir"
    echo "  ✅ Managed llms.txt worker package created: $SOURCE_PATH/$output_zip"
}

# Main packaging logic
# Convert SOURCE_PATH to absolute path
SOURCE_PATH=$(cd "$SOURCE_PATH" && pwd)

case "$FUNCTION_NAME" in
    "llms-txt-worker")
        package_llms_txt_worker
        ;;
    "search-projection-backfill")
        if [ ! -d "$SOURCE_PATH/search_projection_backfill" ]; then
            echo "Error: search_projection_backfill directory not found in $SOURCE_PATH"
            exit 1
        fi
        package_function \
            "Search Projection Backfill" \
            "$SOURCE_PATH/search_projection_backfill" \
            "search_projection_backfill_deployment.zip"
        ;;
    "content-deindexer")
        if [ ! -d "$SOURCE_PATH/content_deindexer" ]; then
            echo "Error: content_deindexer directory not found in $SOURCE_PATH"
            exit 1
        fi
        package_function \
            "Content Deindexer" \
            "$SOURCE_PATH/content_deindexer" \
            "content_deindexer_deployment.zip"
        ;;
    "content-pipeline")
        if [ ! -d "$SOURCE_PATH/content_pipeline" ]; then
            echo "Error: content_pipeline directory not found in $SOURCE_PATH"
            exit 1
        fi
        package_content_pipeline
        ;;
    "partner-api")
        if [ ! -d "$SOURCE_PATH/partner_api" ]; then
            echo "Error: partner_api directory not found in $SOURCE_PATH"
            exit 1
        fi
        package_function "Partner API" "$SOURCE_PATH/partner_api" "partner_api_deployment.zip"
        ;;
    "ui-backend")
        if [ ! -d "$SOURCE_PATH/ui_backend" ]; then
            echo "Error: ui_backend directory not found in $SOURCE_PATH"
            exit 1
        fi
        package_function "UI Backend" "$SOURCE_PATH/ui_backend" "ui_backend_deployment.zip"
        ;;
    "shared-services")
        # Package shared services (authentication, analytics, etc.)
        SOURCE_PATH=$(cd "$SOURCE_PATH" && pwd)
        package_shared_services
        ;;
    "all")
        # Package all functions
        if [ -d "$SOURCE_PATH/partner_api" ]; then
            package_function "Partner API" "$SOURCE_PATH/partner_api" "partner_api_deployment.zip"
        fi
        if [ -d "$SOURCE_PATH/ui_backend" ]; then
            package_function "UI Backend" "$SOURCE_PATH/ui_backend" "ui_backend_deployment.zip"
        fi
        if [ -d "$SOURCE_PATH/search_projection_backfill" ]; then
            package_function \
                "Search Projection Backfill" \
                "$SOURCE_PATH/search_projection_backfill" \
                "search_projection_backfill_deployment.zip"
        fi
        if [ -d "$SOURCE_PATH/content_deindexer" ]; then
            package_function \
                "Content Deindexer" \
                "$SOURCE_PATH/content_deindexer" \
                "content_deindexer_deployment.zip"
        fi
        SOURCE_PATH=$(cd "$SOURCE_PATH" && pwd)
        package_shared_services
        echo "✅ All packages created successfully"
        ;;
    "")
        # Default behavior - show usage
        echo "Please specify which function to package:"
        echo "  $0 --function partner-api lambda        # Package Partner API Lambda"
        echo "  $0 --function ui-backend lambda         # Package UI Backend Lambda"
        echo "  $0 --function shared-services lambda    # Package shared services"
        echo "  $0 --function all lambda                # Package all functions"
        echo ""
        exit 1
        ;;
    *)
        echo "Error: Unknown function name '$FUNCTION_NAME'"
        echo "Valid options: partner-api, ui-backend, shared-services, content-pipeline, llms-txt-worker, search-projection-backfill, all"
        exit 1
        ;;
esac

echo "🎉 Lambda packaging completed successfully!"
