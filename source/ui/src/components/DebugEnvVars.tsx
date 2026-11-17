/**
 * Debug Environment Variables Component
 * 
 * Displays current environment variables and feature flags for debugging
 */

import React from 'react';
import { Container, Header, Box, ColumnLayout } from '@cloudscape-design/components';
import { useFeatureFlags } from '../contexts/FeatureFlagContext';

const DebugEnvVars: React.FC = () => {
  const { flags } = useFeatureFlags();

  const envVars = {
    VITE_ENABLE_DEMO: import.meta.env.VITE_ENABLE_DEMO,
    VITE_AWS_REGION: import.meta.env.VITE_AWS_REGION,
    VITE_USER_POOL_ID: import.meta.env.VITE_USER_POOL_ID ? '***' : 'not set',
    VITE_USER_POOL_CLIENT_ID: import.meta.env.VITE_USER_POOL_CLIENT_ID ? '***' : 'not set',
    VITE_IDENTITY_POOL_ID: import.meta.env.VITE_IDENTITY_POOL_ID ? '***' : 'not set',
    VITE_COGNITO_DOMAIN: import.meta.env.VITE_COGNITO_DOMAIN ? '***' : 'not set',
  };

  return (
    <Container>
      <Header variant="h2">Debug: Environment Variables & Feature Flags</Header>
      
      <ColumnLayout columns={2}>
        <div>
          <Header variant="h3">Environment Variables</Header>
          <Box>
            {Object.entries(envVars).map(([key, value]) => (
              <Box key={key} margin={{ bottom: 'xs' }}>
                <strong>{key}:</strong> {String(value)}
              </Box>
            ))}
          </Box>
        </div>
        
        <div>
          <Header variant="h3">Feature Flags</Header>
          <Box>
            {Object.entries(flags).map(([key, value]) => (
              <Box key={key} margin={{ bottom: 'xs' }}>
                <strong>{key}:</strong> {String(value)}
              </Box>
            ))}
          </Box>
        </div>
      </ColumnLayout>
    </Container>
  );
};

export default DebugEnvVars;