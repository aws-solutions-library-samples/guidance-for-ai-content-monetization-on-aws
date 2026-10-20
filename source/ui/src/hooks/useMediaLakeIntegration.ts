/**
 * MediaLake Integration Hook
 * 
 * Provides MediaLake integration state and utilities throughout the application
 */

import { useState, useEffect, useCallback } from 'react';
import { 
  getMediaLakeIntegration, 
  checkMediaLakeAvailability,
  getMediaLakeConnectorUrl,
  MediaLakeIntegration 
} from '../services/mediaLakeService';

interface UseMediaLakeIntegrationReturn {
  integration: MediaLakeIntegration;
  isAvailable: boolean;
  isLoading: boolean;
  error: string | null;
  openConnectorConfig: (connectorType?: string) => Promise<void>;
  refreshIntegration: () => Promise<void>;
}

export const useMediaLakeIntegration = (): UseMediaLakeIntegrationReturn => {
  const [integration, setIntegration] = useState<MediaLakeIntegration>({ enabled: false });
  const [isAvailable, setIsAvailable] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshIntegration = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const mediaLakeIntegration = await getMediaLakeIntegration();
      setIntegration(mediaLakeIntegration);

      if (mediaLakeIntegration.enabled) {
        const available = await checkMediaLakeAvailability();
        setIsAvailable(available);
      } else {
        setIsAvailable(false);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error occurred';
      setError(errorMessage);
      console.error('MediaLake integration error:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const openConnectorConfig = useCallback(async (connectorType: string = 'wordpress') => {
    try {
      const connectorUrl = await getMediaLakeConnectorUrl(connectorType);
      if (connectorUrl) {
        window.open(connectorUrl, '_blank');
      } else {
        throw new Error('MediaLake connector URL not available');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to open connector configuration';
      setError(errorMessage);
      console.error('Failed to open MediaLake connector:', err);
    }
  }, []);

  useEffect(() => {
    refreshIntegration();
  }, []);

  return {
    integration,
    isAvailable,
    isLoading,
    error,
    openConnectorConfig,
    refreshIntegration
  };
};

export default useMediaLakeIntegration;