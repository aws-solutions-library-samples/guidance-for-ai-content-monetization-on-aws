/**
 * MediaLake Service Tests
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  detectMediaLakeConfig,
  getMediaLakeIntegration,
  checkMediaLakeAvailability
} from '../mediaLakeService';

describe('MediaLake Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('detectMediaLakeConfig', () => {
    it('should return null when no MediaLake env vars are present', async () => {
      const config = await detectMediaLakeConfig();
      // Since we can't easily mock import.meta.env in tests, 
      // we expect null when no MediaLake config is present
      expect(config).toBeNull();
    });
  });

  describe('getMediaLakeIntegration', () => {
    it('should return disabled integration when MediaLake config is not available', async () => {
      const integration = await getMediaLakeIntegration();
      // Without proper MediaLake config, integration should be disabled
      expect(integration.enabled).toBe(false);
      expect(integration.config).toBeUndefined();
      expect(integration.connectorConfigUrl).toBeUndefined();
    });
  });

  describe('checkMediaLakeAvailability', () => {
    it('should return false when MediaLake integration is disabled', async () => {
      const available = await checkMediaLakeAvailability();
      expect(available).toBe(false);
    });

    it('should handle fetch errors gracefully', async () => {
      // Mock fetch to reject
      global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const available = await checkMediaLakeAvailability(100); // Short timeout

      expect(available).toBe(false);
    });
  });
});