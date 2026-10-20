# Analytics Agent Components

This directory contains the intelligent analytics agent that provides context-aware analytics assistance through natural language conversations. The agent uses advanced intent analysis and strategic tool selection to provide relevant insights.

## 🏗️ Architecture

The analytics agent is deployed in **Amazon Bedrock AgentCore** and integrates with the MCP server for data access:

```
UI → API Gateway → Analytics Lambda → AgentCore Analytics Agent → MCP Server → IAB APIs
```

## 📁 File Structure

### 🧠 Core Agent Components
- **`analytics_agent_strands.py`** - Main intelligent analytics agent using Strands framework with Claude 3.7 Sonnet
- **`agentcore_wrapper_template.py`** - Template for AgentCore deployment wrapper (static template)
- **`agentcore_wrapper.py`** - Runtime wrapper (generated from template during deployment)

### 📂 File Structure Overview

```
analytics_agent/
├── 🧠 Core Agent
│   ├── analytics_agent_strands.py      # ✅ Main Strands agent with environment-based tool selection
│   ├── agentcore_wrapper_template.py   # ✅ AgentCore deployment template
│   └── agentcore_wrapper.py           # ✅ Generated runtime wrapper
│
├── 🔌 MCP Client Integration
│   ├── agentcore_mcp_client.py         # ✅ ACTIVE: Correct MCP protocol implementation
│   ├── mcp_client_deprecated.py        # ❌ DEPRECATED: HTTP-based approach (renamed)
│   └── fixed_mcp_client.py            # 🧪 TESTING: Mock data for local development
│
├── 🚀 Deployment & Configuration
│   ├── deploy.py                       # ✅ AgentCore deployment script
│   ├── deploy_memory.py               # ✅ AgentCore Memory deployment
│   ├── .bedrock_agentcore.yaml        # ✅ AgentCore configuration
│   └── requirements_agentcore.txt      # ✅ AgentCore dependencies
│
├── ⚙️ Configuration
│   ├── .env / .env.example            # ✅ Environment configuration
│   ├── deployment_config.json         # ✅ Deployment settings
│   ├── gateway_config.py              # ✅ AgentCore Gateway config
│   └── identity_config.py             # ✅ AgentCore Identity config
│
├── 🧪 Testing & Development
│   ├── start_local_testing.py         # ✅ Local testing utilities
│   ├── local_api_server.py           # ✅ Local development server
│   └── test_*.py / test_*.json        # ✅ Test scripts and samples
│
└── 📊 Legacy Components
    ├── analytics_agent.py             # ❌ Legacy agent (superseded)
    ├── async_safe_agent.py           # ❌ Legacy async implementation
    └── context_tools.py              # ❌ Legacy tools (now integrated)
```

### 🔌 MCP Client Implementations
- **`agentcore_mcp_client.py`** - **✅ ACTIVE**: Correct MCP protocol implementation for AgentCore integration
- **`mcp_client_deprecated.py`** - **❌ DEPRECATED**: HTTP-based approach (renamed from mcp_client.py)
- **`fixed_mcp_client.py`** - **🧪 TESTING**: Mock data client for local development and testing

#### MCP Client Status and Usage
- **CORRECT APPROACH**: Use `agentcore_mcp_client.py` which implements proper MCP protocol with `ClientSession` and `streamablehttp_client`
- **DEPRECATED APPROACH**: `mcp_client_deprecated.py` uses custom HTTP calls instead of MCP protocol - DO NOT USE
- **INTEGRATION STATUS**: Analytics agent now integrates with `AgentCoreMCPClient` via Strands `@tool` decorators

### 🚀 Deployment & Configuration
- **`deploy.py`** - Main deployment script for AgentCore
- **`deploy_memory.py`** - AgentCore Memory deployment
- **`DEPLOYMENT_APPROACH.md`** - Deployment methodology documentation
- **`.bedrock_agentcore.yaml`** - AgentCore configuration
- **`requirements_agentcore.txt`** - AgentCore-specific dependencies
- **`requirements.txt`** - General Python dependencies

### ⚙️ Configuration Files
- **`.env`** - Environment configuration
- **`.env.example`** - Configuration template
- **`deployment_config.json`** - Deployment configuration
- **`gateway_config.py`** - AgentCore Gateway configuration
- **`identity_config.py`** - AgentCore Identity configuration

### 🧪 Testing & Development
- **`start_local_testing.py`** - Local testing utilities
- **`local_api_server.py`** - Local development server
- **`test_*.py`** - Various test scripts
- **`test_*.json`** - Test response samples

### 📊 Legacy Components
- **`analytics_agent.py`** - Legacy analytics agent (superseded by Strands implementation)
- **`async_safe_agent.py`** - Legacy async implementation
- **`context_tools.py`** - Legacy context tools (now integrated into main agent)

## 🚀 Deployment Architecture

The analytics agent uses a **template-based deployment approach** for maintainability:

### Deployment Flow
1. **Template Copy**: `agentcore_wrapper_template.py` → `agentcore_wrapper.py`
2. **Environment Configuration**: Dynamic values set via environment variables
3. **AgentCore Deployment**: Agent deployed to Amazon Bedrock AgentCore
4. **Lambda Integration**: UI Lambda function calls deployed AgentCore agent
5. **MCP Integration**: Agent uses appropriate MCP client based on environment

### MCP Client Selection Logic
- **Demo Mode** (`USE_MOCK_ANALYTICS=true`) → Uses built-in mock analytics tools (preserved with `_mock` suffix)
- **Production Mode** (`USE_MOCK_ANALYTICS=false`) → Uses `agentcore_mcp_client.py` to connect to deployed MCP server
- **Local Development** → Uses `fixed_mcp_client.py` for testing (when MCP server unavailable)
- **Deprecated** → `mcp_client_deprecated.py` (HTTP-based approach, not used)

### Environment Variables
```bash
# MCP Integration
MCP_SERVER_URL=arn:aws:bedrock-agentcore:us-west-2:...:runtime/mcp_server_name
MCP_CLIENT_TYPE=internal

# Publisher Configuration
PUBLISHER_NAME=Sample Publisher
ANALYTICS_MEMORY_ID=PublisherAnalyticsMemory-...
ANALYTICS_MEMORY_SSM_PARAM=/app/analytics/agentcore/memory_id

# Demo Mode Configuration
USE_MOCK_ANALYTICS=false        # Set to "true" to use mock analytics tools instead of MCP client
DEMO_MODE=false                 # Set to "true" to enable all mock modes
USE_MOCK_DATA=false             # Controls MCP server data source (affects analytics data quality)

# System Configuration
LOG_LEVEL=INFO
AWS_DEFAULT_REGION=us-west-2
```

## 🎭 Demo Mode Configuration

The analytics agent supports flexible demo and production modes through environment variables:

### Configuration Modes

#### 1. **Production Mode** (Default)
```bash
USE_MOCK_ANALYTICS=false
DEMO_MODE=false
```
- Uses `AgentCoreMCPClient` to connect to deployed MCP server
- Provides real analytics data from IAB APIs
- Recommended for production deployments

#### 2. **Demo Mode** 
```bash
USE_MOCK_ANALYTICS=true
# OR
DEMO_MODE=true
```
- Uses preserved mock analytics tools with `_mock` suffix
- Provides consistent, predictable demo data
- Ideal for demonstrations and testing

#### 3. **Hybrid Mode**
```bash
USE_MOCK_ANALYTICS=true
USE_MOCK_DATA=false
```
- Analytics agent uses mock tools
- MCP server uses real IAB APIs
- Useful for testing MCP server independently

### Demo Mode Benefits

1. **Preserved Mock Data**: All existing mock implementations are preserved with `_mock` suffix
2. **Easy Switching**: Environment variables allow instant switching between real and mock data
3. **Demo Consistency**: Mock data provides consistent, predictable responses for demonstrations
4. **Development Testing**: Developers can test without requiring MCP server deployment

### Configuration Examples

```bash
# Full Demo Environment
export USE_MOCK_ANALYTICS=true
export USE_MOCK_DATA=true
export DEMO_MODE=true

# Production Environment
export USE_MOCK_ANALYTICS=false
export USE_MOCK_DATA=false
export DEMO_MODE=false

# Testing Environment (real MCP server, mock analytics)
export USE_MOCK_ANALYTICS=true
export USE_MOCK_DATA=false
```

## 🧪 Testing

### Agent Intelligence Testing
```bash
# Test agent intelligence with mock data (recommended for development)
python ../test_analytics_agent.py

# Test MCP client selection logic
python ../test_agentcore_mcp_client.py

# Test local agent functionality
python analytics_agent_strands.py
```

### Deployment Testing
```bash
# Deploy and test memory
python deploy_memory.py

# Deploy analytics agent
python deploy.py

# Test deployed agent
python start_local_testing.py
```

### Integration Testing
```bash
# Test complete integration chain
python ../tests/mcp_server_tests/test_integration.py

# Test UI integration
python ../tests/test_analytics_agent_integration.py
```

## 🔧 Configuration

The analytics agent is configured through environment variables in the AgentCore deployment:

### AgentCore Environment Variables
```bash
# Set in mcp_server/.env or deployment configuration
MCP_SERVER_URL=http://localhost:8000
IAB_API_BASE_URL=https://example123.execute-api.us-west-2.amazonaws.com/v1
PARTNER_API_KEY=your_api_key_here
USE_MOCK_DATA=true  # For development/testing
```

### Local Development Environment
```bash
# Copy the example configuration for local testing
cp .env.example .env

# For local development (recommended):
echo "USE_MOCK_DATA=true" >> .env
```

## 🧠 LLM Provider

The analytics agent uses **AWS Bedrock Claude 3.7 Sonnet** as configured in the AgentCore deployment:

```python
# Model configuration in analytics_agent_direct.py
MODEL_ID = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
```

AWS credentials are managed through the AgentCore IAM roles and don't require local configuration.

## 🎯 Key Features

### 🧠 Intelligence & Analysis
- **Advanced Intent Analysis**: Automatically identifies query types (revenue, bot behavior, content performance, help)
- **Strategic Tool Selection**: Chooses appropriate MCP tools based on user intent
- **Claude 3.7 Sonnet Integration**: Uses advanced LLM reasoning for insights and recommendations
- **Context Awareness**: Understands current page context and user workflow
- **Conversation Memory**: Maintains session context using AgentCore Memory

### 🔧 Technical Capabilities
- **Multi-Environment Support**: Automatically adapts to local testing vs deployed environments
- **MCP Protocol Integration**: Native integration with deployed MCP servers through AgentCore
- **Robust Error Handling**: Graceful fallbacks and comprehensive error management
- **Rate Limiting & Backpressure**: Built-in resilience patterns for production use
- **Session Management**: Persistent conversation state across page navigation

### 📊 Analytics Integration
- **Real-Time Data Access**: Connects to live IAB APIs through MCP server
- **Mock Data Support**: Realistic test data for development and demonstrations
- **Multi-Tool Orchestration**: Intelligently combines multiple analytics tools for comprehensive insights
- **Business Intelligence**: Provides actionable recommendations and strategic insights

### 🎨 User Experience
- **Natural Language Interface**: Conversational interaction with complex analytics data
- **Contextual Suggestions**: Page-specific action recommendations
- **Distinct Responses**: Different query types receive relevant, specific responses
- **Progressive Disclosure**: Asks clarifying questions to provide better assistance

## 🔧 API Integration

The analytics agent is accessed through the Lambda function API:

- `POST /api/analytics-agent/chat` - Main chat interface (via Lambda → AgentCore)
- Authentication: Cognito JWT tokens
- Context: Automatic page context detection
- Response: Structured JSON with analytics insights

## 📊 Usage Examples

### Via UI (Recommended)
1. Open the Publisher UI
2. Navigate to any page (analytics, content, bot-control)
3. Click the Analytics Assistant button
4. Ask questions like:
   - "Show me my revenue analytics"
   - "What's my top performing content?"
   - "How are my bots behaving?"

### Direct API Call (for testing)
```bash
# Call the Lambda function endpoint
curl -X POST https://your-api-gateway-url/v1/analytics-agent/chat \
  -H "Authorization: Bearer YOUR_COGNITO_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Show me my analytics",
    "page_context": {"page_type": "analytics"},
    "session_id": "test-session"
  }'
```

### Local Development Testing
```python
# Test the direct analytics agent (for development)
from mcp_server.analytics_agent_direct import analytics_agent

response = analytics_agent.chat(
    message="Show me my revenue analytics",
    page_context={"page_type": "analytics"},
    user_context={"publisher_id": "test_user"},
    session_id="test-session",
)
print(response["response"])
```

## 🎉 Success Indicators

You'll know the system is working when:
1. ✅ `python ../tests/mcp_server_tests/test_analytics_agent_direct.py` passes
2. ✅ AgentCore deployment shows healthy status
3. ✅ Lambda function can call AgentCore successfully
4. ✅ UI shows the Analytics Assistant and connects to backend
5. ✅ Assistant provides intelligent, context-aware responses
6. ✅ Mock data provides realistic analytics insights
7. ✅ Context-aware suggestions appear based on current page

## 🚨 Troubleshooting

### Common Issues

**AgentCore Not Responding**:
- Check AgentCore deployment status in AWS console
- Verify IAM permissions for Lambda to call AgentCore
- Check AgentCore logs for errors

**Lambda Function Errors**:
- Check Lambda function logs in CloudWatch
- Verify environment variables are set correctly
- Ensure AgentCore ARN is correct in Lambda configuration

**UI Can't Connect**:
- Verify API Gateway endpoints are deployed
- Check Cognito authentication configuration
- Ensure CORS is properly configured

**No Analytics Responses**:
- Check that analytics agent is properly deployed in AgentCore
- Verify mock data is enabled for testing
- Check AgentCore wrapper logs for import errors

### Mock vs Real Data Issues

**Analytics Agent Returns Mock Data in Production**:
```bash
# Check environment variables
echo $USE_MOCK_ANALYTICS  # Should be "false" for production
echo $DEMO_MODE          # Should be "false" for production

# Verify MCP client integration
python -c "from analytics_agent_strands import PublisherAnalyticsAgent; agent = PublisherAnalyticsAgent(); print('Mock tools:', agent.use_mock_tools)"
```

**MCP Client Connection Failures**:
```bash
# Test MCP server connectivity
python -c "from agentcore_mcp_client import AgentCoreMCPClient; client = AgentCoreMCPClient('your-mcp-server-arn'); print('Connection test')"

# Check bearer token authentication
echo $AGENTCORE_AUTH_TOKEN  # Should be set in AgentCore runtime
```

**Inconsistent Data Between Mock and Real**:
- Mock data is static and designed for demos
- Real data comes from live IAB APIs and will vary
- Use `DEMO_MODE=true` for consistent demo experiences
- Use `USE_MOCK_ANALYTICS=false` for production accuracy

**Environment Variable Conflicts**:
```bash
# Check all relevant environment variables
env | grep -E "(MOCK|DEMO|MCP_SERVER)"

# Reset to production mode
export USE_MOCK_ANALYTICS=false
export USE_MOCK_DATA=false
export DEMO_MODE=false
```

**MCP Client Authentication Issues**:
- Verify `AGENTCORE_AUTH_TOKEN` is set in AgentCore runtime
- Check AWS Secrets Manager for `mcp_server/cognito/credentials`
- Ensure IAM permissions for Secrets Manager access
- Test bearer token retrieval: `python -c "from agentcore_mcp_client import AgentCoreMCPClient; client = AgentCoreMCPClient(); print(client._get_bearer_token())"`

**Tool Selection Problems**:
```bash
# Verify correct tool selection
python -c "
from analytics_agent_strands import PublisherAnalyticsAgent
agent = PublisherAnalyticsAgent()
print('Using mock tools:', agent.use_mock_tools)
print('Available tools:', [tool.name for tool in agent.tools])
"
```

## 🔄 Development Workflow

### Local Development
1. **Use Mock Data**: Set up with `fixed_mcp_client.py` for fast iteration
2. **Test Intelligence**: Verify intent analysis and response differentiation
3. **Validate Logic**: Ensure proper tool selection and context awareness

### Deployment Process
1. **Template Updates**: Modify `agentcore_wrapper_template.py` if needed
2. **Environment Config**: Set deployment-specific environment variables
3. **Deploy Memory**: Run `python deploy_memory.py` if memory features needed
4. **Deploy Agent**: Run `python deploy.py` to deploy to AgentCore
5. **Integration Test**: Verify end-to-end functionality through UI

### MCP Client Integration Approach

#### Correct Implementation Pattern

The analytics agent uses the **Strands + MCP Client** pattern for proper integration:

```python
# ✅ CORRECT: Strands @tool decorators wrapping MCP client calls
from agentcore_mcp_client import AgentCoreMCPClient


@tool
async def get_usage_analytics(date_range: str = "last_7_days") -> str:
    """Get usage analytics from MCP server via AgentCoreMCPClient."""
    result = await self.mcp_client.get_usage_analytics(date_range=date_range)
    return json.dumps(result.get("data", result), indent=2)
```

#### Architecture Components

1. **AgentCoreMCPClient** (`agentcore_mcp_client.py`):
   - Uses proper MCP protocol with `ClientSession` and `streamablehttp_client`
   - Handles AgentCore authentication and URL formatting
   - Provides async methods for all MCP tools

2. **Strands Integration** (`analytics_agent_strands.py`):
   - Wraps MCP client calls in `@tool` decorators
   - Provides environment-based tool selection (mock vs real)
   - Integrates with Claude 3.7 Sonnet for intelligent responses

3. **Mock Tool Preservation**:
   - Original mock tools renamed with `_mock` suffix
   - Available when `USE_MOCK_ANALYTICS=true`
   - Provides consistent demo data

#### Integration Flow

```
User Question → Strands Agent → @tool Decorator → AgentCoreMCPClient → MCP Server → IAB API
                     ↓
UI Response ← Agent Analysis ← Tool Response ← MCP Protocol ← Server Processing ← Real Data
```

#### Environment-Based Tool Selection

```python
class PublisherAnalyticsAgent:
    def __init__(self):
        self.use_mock_tools = os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true"

        if self.use_mock_tools:
            self.tools = self._create_mock_analytics_tools()  # Preserved mock tools
        else:
            self.mcp_client = AgentCoreMCPClient(mcp_server_url)
            self.tools = self._create_mcp_client_tools()  # Real MCP integration
```

### MCP Client Evolution
- **Phase 1**: `mcp_client.py` → `mcp_client_deprecated.py` (HTTP-based, deprecated)
- **Phase 2**: `fixed_mcp_client.py` - Mock data for testing (still used for local dev)
- **Phase 3**: `agentcore_mcp_client.py` - Production AgentCore integration (current standard)
- **Phase 4**: Strands integration with environment-based tool selection (current implementation)

## 🏗️ MCP Client Integration Guide for Developers

### Correct Implementation Pattern

When integrating with MCP servers, follow this proven pattern:

#### 1. Use Proper MCP Protocol Implementation

```python
# ✅ CORRECT: Use AgentCoreMCPClient with proper MCP protocol
from agentcore_mcp_client import AgentCoreMCPClient


class PublisherAnalyticsAgent:
    def __init__(self, mcp_server_url: str):
        self.mcp_client = AgentCoreMCPClient(mcp_server_url)
```

#### 2. Wrap MCP Calls in Strands Tools

```python
# ✅ CORRECT: Strands @tool decorators wrapping MCP client calls
@tool
async def get_usage_analytics(date_range: str = "last_7_days", content_type: Optional[str] = None) -> str:
    """Get usage analytics from MCP server."""
    result = await self.mcp_client.get_usage_analytics(date_range=date_range, content_type=content_type)
    return json.dumps(result.get("data", result), indent=2)
```

#### 3. Implement Environment-Based Tool Selection

```python
# ✅ CORRECT: Environment-based switching between mock and real tools
def __init__(self):
    self.use_mock_tools = os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true"
    
    if self.use_mock_tools:
        self.tools = self._create_mock_analytics_tools()
    else:
        self.mcp_client = AgentCoreMCPClient(mcp_server_url)
        self.tools = self._create_mcp_client_tools()
```

#### 4. Preserve Mock Implementations

```python
# ✅ CORRECT: Preserve existing mock tools with _mock suffix
@tool
async def get_usage_analytics_mock(date_range: str = "last_7_days") -> str:
    """Mock implementation for demo purposes."""
    # Original mock logic preserved here
    return json.dumps(mock_data, indent=2)
```

### ❌ Common Mistakes to Avoid

#### 1. Don't Use HTTP-Based Approaches
```python
# ❌ WRONG: Custom HTTP calls instead of MCP protocol
import httpx


async def call_mcp_tool(url: str, tool_name: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{url}/tools/{tool_name}")
        return response.json()
```

#### 2. Don't Hardcode Tool Selection
```python
# ❌ WRONG: Hardcoded tool selection without environment variables
def __init__(self):
    # Always uses mock tools - no flexibility
    self.tools = self._create_mock_analytics_tools()
```

#### 3. Don't Remove Mock Implementations
```python
# ❌ WRONG: Removing mock implementations breaks demo capabilities
# Mock tools should be preserved with _mock suffix, not deleted
```

### 🔧 Integration Checklist

When integrating a new MCP client:

- [ ] Use `AgentCoreMCPClient` with proper MCP protocol
- [ ] Wrap MCP calls in Strands `@tool` decorators
- [ ] Implement environment-based tool selection
- [ ] Preserve existing mock implementations with `_mock` suffix
- [ ] Add proper error handling for MCP connection failures
- [ ] Test both mock and real data modes
- [ ] Document environment variables for configuration
- [ ] Add authentication handling (bearer tokens, Secrets Manager)

### 🚀 Deployment Considerations

1. **AgentCore Runtime**: Use `AGENTCORE_AUTH_TOKEN` environment variable
2. **Secrets Manager**: Fallback to AWS Secrets Manager for authentication
3. **Error Handling**: Graceful degradation when MCP server unavailable
4. **Performance**: Consider caching for frequently accessed data
5. **Monitoring**: Add logging for MCP client operations

## 📚 Related Documentation

- **`DEPLOYMENT_APPROACH.md`** - Detailed deployment methodology
- **`../mcp_server/README.md`** - MCP server documentation
- **`../.kiro/specs/fix-mcp-mock-data-integration/`** - Mock data integration specifications
- **`../.kiro/specs/publisher-content-mcp-server/`** - Complete system specifications
- **AgentCore Documentation** - Amazon Bedrock AgentCore guides

## 🎉 Success Metrics

The analytics agent is working correctly when:
- ✅ Different queries produce distinct, relevant responses
- ✅ Intent analysis correctly identifies query types
- ✅ Appropriate MCP tools are selected and called
- ✅ Real data flows through in deployed environment
- ✅ Mock data provides realistic responses in development
- ✅ Context awareness adapts to current page
- ✅ Memory persists conversation state across sessions