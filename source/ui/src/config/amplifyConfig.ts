/**
 * AWS Amplify Configuration for IAB CoMP API
 * 
 * This configuration connects the React app to AWS Cognito for authentication
 * and other AWS services for the demo functionality.
 * Supports integration with MediaLake for shared authentication.
 */

import { Amplify } from 'aws-amplify';
import { getAuthConfigWithMediaLake, MediaLakeIntegration } from '../services/mediaLakeService';

// Extend window interface to track logging state
declare global {
  interface Window {
    __mediaLakeConfigLogged?: boolean;
    __amplifyConfigLogged?: boolean;
    __amplifySuccessLogged?: boolean;
  }
}

export interface AmplifyConfig {
  Auth: {
    Cognito: {
      region: string;
      userPoolId: string;
      userPoolClientId: string;
      identityPoolId?: string;
      loginWith?: {
        oauth?: {
          domain: string;
          scopes: string[];
          redirectSignIn: string[];
          redirectSignOut: string[];
          responseType: string;
        };
      };
    };
  };
  API?: {
    REST: {
      [key: string]: {
        endpoint: string;
        region: string;
      };
    };
  };
  mediaLakeIntegration?: MediaLakeIntegration;
}

/**
 * Get Amplify configuration from environment variables
 * Supports MediaLake integration for shared authentication
 */
export const getAmplifyConfig = async (): Promise<AmplifyConfig> => {
  // Get authentication configuration with MediaLake integration
  const authConfig = await getAuthConfigWithMediaLake();
  
  // Debug environment variables (log only once to avoid console spam)
  if (!window.__amplifyConfigLogged) {
    console.log('Authentication configuration:', {
      region: authConfig.region,
      userPoolId: authConfig.userPoolId ? '***' : 'not set',
      userPoolClientId: authConfig.userPoolClientId ? '***' : 'not set',
      identityPoolId: authConfig.identityPoolId ? '***' : 'not set',
      mediaLakeEnabled: authConfig.mediaLakeIntegration.enabled
    });
    window.__amplifyConfigLogged = true;
  }

  const config: AmplifyConfig = {
    Auth: {
      Cognito: {
        region: authConfig.region,
        userPoolId: authConfig.userPoolId,
        userPoolClientId: authConfig.userPoolClientId,
        identityPoolId: authConfig.identityPoolId,
      }
    },
    mediaLakeIntegration: authConfig.mediaLakeIntegration
  };

  // Add OAuth configuration if domain is provided
  const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN;
  if (cognitoDomain) {
    const currentUrl = window.location.origin;
    config.Auth.Cognito.loginWith = {
      oauth: {
        domain: cognitoDomain,
        scopes: ['email', 'openid', 'profile'],
        redirectSignIn: [currentUrl, `${currentUrl}/`, 'http://localhost:3000', 'https://localhost:5173'],
        redirectSignOut: [currentUrl, `${currentUrl}/`, `${currentUrl}/sign-in`, 'http://localhost:3000', 'https://localhost:5173'],
        responseType: 'code'
      }
    };
  }

  // Add API configuration if available
  const apiEndpoint = import.meta.env.VITE_API_BASE_URL;
  if (apiEndpoint) {
    config.API = {
      REST: {
        'iab-api': {
          endpoint: apiEndpoint,
          region: authConfig.region
        }
      }
    };
  }

  return config;
};

/**
 * Configure Amplify with the current environment settings
 * Supports MediaLake integration for shared authentication
 */
export const configureAmplify = async (): Promise<boolean> => {
  const config = await getAmplifyConfig();
  
  // Validate required configuration
  if (!config.Auth.Cognito.userPoolId || !config.Auth.Cognito.userPoolClientId) {
    console.warn('Cognito configuration is incomplete. Authentication features will be disabled.');
    if (config.mediaLakeIntegration?.enabled) {
      console.warn('Please ensure MediaLake Cognito configuration is properly set.');
    } else {
      console.warn('Please ensure VITE_USER_POOL_ID and VITE_USER_POOL_CLIENT_ID are set.');
    }
    console.log('ℹ️ Running in development mode without authentication');
    return true; // Allow app to run without authentication for development
  }

  try {
    Amplify.configure(config);
    // Only log success message once to avoid console spam
    if (!window.__amplifySuccessLogged) {
      if (config.mediaLakeIntegration?.enabled) {
        console.log('✅ Amplify configured successfully with MediaLake integration');
      } else {
        console.log('✅ Amplify configured successfully with standalone authentication');
      }
      window.__amplifySuccessLogged = true;
    }
    return true;
  } catch (error) {
    console.error('❌ Failed to configure Amplify:', error);
    console.log('ℹ️ Continuing without authentication configuration');
    return true; // Allow app to run even if Amplify config fails
  }
};

/**
 * Check if authentication is properly configured
 */
export const isAuthConfigured = async (): Promise<boolean> => {
  const config = await getAmplifyConfig();
  return !!(config.Auth.Cognito.userPoolId && config.Auth.Cognito.userPoolClientId);
};