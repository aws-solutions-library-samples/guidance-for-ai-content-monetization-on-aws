# Local UI Testing with AWS Backend

This guide shows you how to run the UI locally while connecting to the deployed AWS backend services.

## 🚀 Quick Start

### 1. Switch to AWS Backend Configuration
```bash
cd ui
./switch-env.sh aws
```

### 2. Install Dependencies (if not already done)
```bash
npm install
```

### 3. Start the UI Development Server
```bash
npm run dev
```

### 4. Open Browser
- Navigate to `http://localhost:5173`
- The UI will connect to the deployed AWS backend

## 🔧 Configuration Details

### AWS Backend Configuration
When you run `./switch-env.sh aws`, the UI will be configured with:

- **API Gateway**: `https://example123.execute-api.us-west-2.amazonaws.com/v1`
- **Analytics Agent**: `https://example123.execute-api.us-west-2.amazonaws.com/v1/analytics-agent/chat`
- **Cognito User Pool**: `us-west-2_EXAMPLE01`
- **Cognito Client ID**: `EXAMPLECLIENTID0123456789`
- **Mock Data**: Disabled (uses real AWS services)

### What You Can Test

1. **Authentication**: Real Cognito authentication
2. **Content Discovery**: Real IAB API content search and retrieval
3. **Analytics Agent**: Real analytics agent powered by deployed MCP server
4. **Bot Simulation**: Real bot interactions with MCP server
5. **Usage Analytics**: Real usage tracking and analytics

## 🧪 Testing Scenarios

### Test the Analytics Assistant
1. Navigate to any page (Analytics, Content, Bot Control)
2. Click the Analytics Assistant button (floating chat icon)
3. Try context-aware questions:
   - "Show me my revenue analytics"
   - "What's my top performing content?"
   - "How are my bots performing?"
   - "Give me insights about my content"

### Test Content Discovery
1. Go to the Content page
2. Search for content using the search functionality
3. View content details and access controls
4. Test different content types and categories

### Test Bot Control
1. Navigate to Bot Control page
2. View bot analytics and behavior
3. Test bot access controls and permissions

## 🔄 Switching Between Modes

### Switch to AWS Backend
```bash
./switch-env.sh aws
```

### Switch Back to Local/Mock Mode
```bash
./switch-env.sh local
```

### Check Current Configuration
```bash
./switch-env.sh status
```

## 🐛 Troubleshooting

### Authentication Issues
If you encounter authentication issues:
1. Check that Cognito configuration is correct in `.env`
2. Clear browser cache and localStorage
3. Try incognito/private browsing mode

### API Connection Issues
If API calls fail:
1. Verify the API Gateway URL is accessible
2. Check AWS credentials and permissions
3. Look at browser developer tools Network tab for error details

### Analytics Agent Issues
If the analytics agent doesn't respond:
1. Check that the MCP server is deployed and healthy:
   ```bash
   cd ../
   make test-agentcore
   ```
2. Verify the analytics endpoint is accessible
3. Check CloudWatch logs for the Lambda function

## 📊 Monitoring and Debugging

### Check MCP Server Health
```bash
cd ../
make status
make test-agentcore
```

### View AWS Logs
- **Lambda Logs**: Check CloudWatch logs for the analytics agent Lambda
- **MCP Server Logs**: Check AgentCore runtime logs
- **API Gateway Logs**: Check API Gateway access logs

### Browser Developer Tools
- **Network Tab**: Monitor API calls and responses
- **Console Tab**: Check for JavaScript errors
- **Application Tab**: Inspect localStorage and session data

## 🎯 Expected Behavior

### Successful Connection
When properly configured, you should see:
- ✅ Successful authentication with Cognito
- ✅ Real content data from IAB API
- ✅ Working analytics assistant with intelligent responses
- ✅ Real-time bot analytics and metrics
- ✅ Proper error handling and user feedback

### Performance
- Initial page load: ~2-3 seconds
- API responses: ~500ms-2s depending on complexity
- Analytics agent responses: ~3-10s depending on query complexity

## 🔐 Security Notes

- All API calls use proper authentication tokens
- Cognito handles user session management
- MCP server enforces tool access controls
- Analytics agent respects user permissions

## 📝 Development Tips

1. **Hot Reload**: The UI will automatically reload when you make changes
2. **Environment Switching**: Use the switch script to quickly change configurations
3. **Debugging**: Use browser dev tools to inspect API calls and responses
4. **Testing**: Test different user scenarios and edge cases
5. **Performance**: Monitor network requests and response times

This setup gives you the best of both worlds: fast local development with real backend functionality!