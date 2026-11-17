#!/usr/bin/env node
/**
 * Test script to verify UI can connect to AWS backend
 */

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Read environment configuration
let envConfig = {};
try {
  const envContent = readFileSync(join(__dirname, '.env'), 'utf8');
  envContent.split('\n').forEach(line => {
    const [key, value] = line.split('=');
    if (key && value) {
      envConfig[key.trim()] = value.trim();
    }
  });
} catch (error) {
  console.error('❌ Could not read .env file');
  process.exit(1);
}

console.log('🧪 Testing AWS Backend Connection');
console.log('================================');

// Test API Gateway endpoint
const apiUrl = envConfig.VITE_API_BASE_URL;
if (!apiUrl || apiUrl.includes('localhost') || apiUrl.includes('example.com')) {
  console.log('❌ API URL not configured for AWS backend');
  console.log('   Current:', apiUrl);
  console.log('   Expected: https://example123.execute-api.us-west-2.amazonaws.com/v1');
  console.log('');
  console.log('💡 Run: ./switch-env.sh aws');
  process.exit(1);
}

console.log('✅ API URL configured:', apiUrl);

// Test Cognito configuration
const userPoolId = envConfig.VITE_USER_POOL_ID;
const clientId = envConfig.VITE_USER_POOL_CLIENT_ID;

if (!userPoolId) {
  console.log('❌ Cognito User Pool not configured');
  console.log('   Current:', userPoolId);
  console.log('   Expected: VITE_USER_POOL_ID set to your deployed user pool ID');
  process.exit(1);
}

if (!clientId) {
  console.log('❌ Cognito Client ID not configured');
  console.log('   Current:', clientId);
  console.log('   Expected: VITE_USER_POOL_CLIENT_ID set to your deployed app client ID');
  process.exit(1);
}

console.log('✅ Cognito User Pool configured:', userPoolId);
console.log('✅ Cognito Client ID configured:', clientId);

// Test mock data setting
const mockData = envConfig.VITE_ENABLE_MOCK_DATA;
if (mockData === 'true') {
  console.log('⚠️  Mock data is enabled - you may see test data instead of real AWS data');
} else {
  console.log('✅ Mock data disabled - will use real AWS backend');
}

// Test analytics endpoint
const analyticsEndpoint = envConfig.VITE_ANALYTICS_AGENT_ENDPOINT;
if (analyticsEndpoint && analyticsEndpoint.includes('example123.execute-api.us-west-2.amazonaws.com')) {
  console.log('✅ Analytics Agent endpoint configured:', analyticsEndpoint);
} else {
  console.log('⚠️  Analytics Agent endpoint not configured or incorrect');
  console.log('   Current:', analyticsEndpoint);
}

console.log('');
console.log('🎉 Configuration looks good for AWS backend!');
console.log('');
console.log('📋 Next steps:');
console.log('1. npm install (if not done)');
console.log('2. npm run dev');
console.log('3. Open http://localhost:5173');
console.log('4. Test the Analytics Assistant');
console.log('');
console.log('🔧 Troubleshooting:');
console.log('- If authentication fails, clear browser cache');
console.log('- If API calls fail, check AWS services are running');
console.log('- If analytics agent doesn\'t work, run: cd ../ && make test-agentcore');