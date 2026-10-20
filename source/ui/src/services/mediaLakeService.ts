/**
 * MediaLake Integration Service
 * 
 * Handles detection and integration with MediaLake deployment
 */

// Extend window interface to track logging state
declare global {
  interface Window {
    __mediaLakeConfigLogged?: boolean;
    __amplifyConfigLogged?: boolean;
    __amplifySuccessLogged?: boolean;
  }
}

export interface MediaLakeConfig {
  region: string;
  userPoolId: string;
  userPoolClientId: string;
  identityPoolId?: string;
  baseUrl: string;
  stackName: string;
}

export interface MediaLakeIntegration {
  enabled: boolean;
  config?: MediaLakeConfig;
  connectorConfigUrl?: string;
}

/**
 * Detect if MediaLake is deployed and available
 */
export const detectMediaLakeConfig = async (): Promise<MediaLakeConfig | null> => {
  try {
    // Check for MediaLake environment variables
    const stackName = import.meta.env.VITE_MEDIALAKE_STACK_NAME;
    const region = import.meta.env.VITE_MEDIALAKE_REGION;
    const userPoolId = import.meta.env.VITE_MEDIALAKE_USER_POOL_ID;
    const userPoolClientId = import.meta.env.VITE_MEDIALAKE_USER_POOL_CLIENT_ID;
    const identityPoolId = import.meta.env.VITE_MEDIALAKE_IDENTITY_POOL_ID;
    const baseUrl = import.meta.env.VITE_MEDIALAKE_UI_URL;

    // If we have the essential configuration, MediaLake is available
    if (stackName && region && userPoolId && userPoolClientId && baseUrl) {
      console.log('✅ MediaLake configuration detected');
      return {
        stackName,
        region,
        userPoolId,
        userPoolClientId,
        identityPoolId,
        baseUrl
      };
    }

    // Try to detect MediaLake through API call (fallback)
    // This could be implemented to check for MediaLake API endpoints
    // Only log once to avoid console spam
    if (!window.__mediaLakeConfigLogged) {
      console.log('ℹ️ MediaLake configuration not found in environment variables');
      window.__mediaLakeConfigLogged = true;
    }
    return null;

  } catch (error) {
    console.warn('⚠️ Failed to detect MediaLake configuration:', error);
    return null;
  }
};

/**
 * Get MediaLake integration configuration
 */
export const getMediaLakeIntegration = async (): Promise<MediaLakeIntegration> => {
  const config = await detectMediaLakeConfig();
  
  if (config) {
    return {
      enabled: true,
      config,
      connectorConfigUrl: `${config.baseUrl}/settings/connectors`
    };
  }

  return {
    enabled: false
  };
};

/**
 * Check if MediaLake authentication should be used
 */
export const shouldUseMediaLakeAuth = async (): Promise<boolean> => {
  const integration = await getMediaLakeIntegration();
  return integration.enabled && !!integration.config;
};

/**
 * Get authentication configuration with MediaLake integration
 */
export const getAuthConfigWithMediaLake = async () => {
  const mediaLakeIntegration = await getMediaLakeIntegration();
  
  if (mediaLakeIntegration.enabled && mediaLakeIntegration.config) {
    // Use MediaLake Cognito configuration
    return {
      region: mediaLakeIntegration.config.region,
      userPoolId: mediaLakeIntegration.config.userPoolId,
      userPoolClientId: mediaLakeIntegration.config.userPoolClientId,
      identityPoolId: mediaLakeIntegration.config.identityPoolId,
      mediaLakeIntegration: mediaLakeIntegration
    };
  }

  // Fallback to standalone Cognito configuration
  return {
    region: import.meta.env.VITE_AWS_REGION || 'us-west-2',
    userPoolId: import.meta.env.VITE_USER_POOL_ID || '',
    userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID || '',
    identityPoolId: import.meta.env.VITE_IDENTITY_POOL_ID,
    mediaLakeIntegration: {
      enabled: false
    }
  };
};

/**
 * Generate deep link to MediaLake connector configuration
 */
export const getMediaLakeConnectorUrl = async (connectorType: string = 'wordpress'): Promise<string | null> => {
  const integration = await getMediaLakeIntegration();
  
  if (integration.enabled && integration.connectorConfigUrl) {
    return `${integration.connectorConfigUrl}/${connectorType}`;
  }
  
  return null;
};

/**
 * Check MediaLake availability with timeout
 */
export const checkMediaLakeAvailability = async (timeoutMs: number = 5000): Promise<boolean> => {
  const integration = await getMediaLakeIntegration();
  
  if (!integration.enabled || !integration.config) {
    return false;
  }

  try {
    // Create a timeout promise
    const timeoutPromise = new Promise<boolean>((_, reject) => {
      setTimeout(() => reject(new Error('Timeout')), timeoutMs);
    });

    // Create a fetch promise to check MediaLake UI availability
    const fetchPromise = fetch(`${integration.config.baseUrl}/health`, {
      method: 'GET',
      mode: 'no-cors' // Avoid CORS issues for availability check
    }).then(() => true).catch(() => false);

    // Race between timeout and fetch
    return await Promise.race([fetchPromise, timeoutPromise]);
  } catch (error) {
    console.warn('MediaLake availability check failed:', error);
    return false;
  }
};