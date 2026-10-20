#!/bin/bash
# Script to switch between local and AWS backend configurations

set -e

case "$1" in
  "aws")
    echo "🔄 Switching to AWS backend configuration..."
    cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
    cp .env.local.aws .env
    echo "✅ Switched to AWS backend configuration"
    echo "📋 Configuration:"
    echo "   - API: https://example123.execute-api.us-west-2.amazonaws.com/v1"
    echo "   - Analytics: https://example123.execute-api.us-west-2.amazonaws.com/v1/analytics-agent/chat"
    echo "   - Cognito: us-west-2_EXAMPLE01"
    echo "   - Mock Data: Disabled"
    ;;
  "local")
    echo "🔄 Switching to local development configuration..."
    if [ -f .env.backup.* ]; then
      latest_backup=$(ls -t .env.backup.* | head -n1)
      cp "$latest_backup" .env
      echo "✅ Restored from backup: $latest_backup"
    else
      cp .env.example .env
      echo "✅ Restored from .env.example"
    fi
    echo "📋 Configuration:"
    echo "   - Mock Data: Enabled"
    echo "   - Local Analytics Agent: Enabled"
    ;;
  "status")
    echo "📋 Current Configuration:"
    if grep -q "VITE_USE_AWS_BACKEND=true" .env 2>/dev/null; then
      echo "   Mode: AWS Backend"
      echo "   API: $(grep VITE_API_BASE_URL .env | cut -d'=' -f2)"
      echo "   Mock Data: $(grep VITE_ENABLE_MOCK_DATA .env | cut -d'=' -f2)"
    else
      echo "   Mode: Local/Mock"
      echo "   Mock Data: $(grep VITE_ENABLE_MOCK_DATA .env | cut -d'=' -f2 2>/dev/null || echo 'true')"
    fi
    ;;
  *)
    echo "Usage: $0 {aws|local|status}"
    echo ""
    echo "Commands:"
    echo "  aws    - Switch to AWS backend configuration"
    echo "  local  - Switch to local/mock configuration"
    echo "  status - Show current configuration"
    echo ""
    echo "Examples:"
    echo "  $0 aws     # Connect UI to deployed AWS backend"
    echo "  $0 local   # Use local mock data"
    echo "  $0 status  # Check current mode"
    exit 1
    ;;
esac