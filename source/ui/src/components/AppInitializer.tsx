/**
 * App Initializer Component
 * 
 * Handles async initialization of Amplify configuration with MediaLake integration
 */

import React, { useState, useEffect } from 'react';
import { Box, StatusIndicator } from '@cloudscape-design/components';
import { configureAmplify } from '../config/amplifyConfig';
import App from '../App';

const AppInitializer: React.FC = () => {
  const [initialized, setInitialized] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const initializeApp = async () => {
      try {
        const success = await configureAmplify();
        if (success) {
          setInitialized(true);
        } else {
          setError('Failed to configure authentication. Some features may not work properly.');
          // Still allow the app to load even if auth config fails
          setInitialized(true);
        }
      } catch (err) {
        console.error('App initialization error:', err);
        setError('Failed to initialize application. Please refresh the page.');
        // Still allow the app to load
        setInitialized(true);
      }
    };

    initializeApp();
  }, []);

  if (!initialized) {
    return (
      <Box textAlign="center" padding="xxl">
        <StatusIndicator type="loading">
          Initializing IAB CoMP API...
        </StatusIndicator>
        <Box variant="small" color="text-body-secondary" margin={{ top: 's' }}>
          Configuring authentication and MediaLake integration...
        </Box>
      </Box>
    );
  }

  if (error) {
    console.warn('App initialization warning:', error);
    // Show the app anyway, but log the warning
  }

  return <App />;
};

export default AppInitializer;