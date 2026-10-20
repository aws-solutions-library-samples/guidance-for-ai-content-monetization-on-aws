/**
 * WordPress Connector Component Tests
 *
 * Post WordPress re-home (task 11.1): the connector no longer self-gates on the
 * retired `enableWordPressIntegration` build flag. Rendering/visibility is
 * driven by connection-activation state at the parent (ContentConnections);
 * the connector itself renders whenever it is mounted. These tests reflect that
 * behavior and continue to exercise the preserved MediaLake / mock-UI paths.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import WordPressConnector from '../WordPressConnector';
import { FeatureFlagProvider } from '../../contexts/FeatureFlagContext';

// Mock the MediaLake service
vi.mock('../../services/mediaLakeService', () => ({
  getMediaLakeIntegration: vi.fn().mockResolvedValue({ enabled: false }),
  checkMediaLakeAvailability: vi.fn().mockResolvedValue(false),
  getMediaLakeConnectorUrl: vi.fn().mockResolvedValue(null),
}));

// Mock the hook
vi.mock('../../hooks/useMediaLakeIntegration', () => ({
  useMediaLakeIntegration: vi.fn().mockReturnValue({
    integration: { enabled: false },
    isAvailable: false,
    isLoading: false,
    error: null,
    openConnectorConfig: vi.fn(),
    refreshIntegration: vi.fn(),
  }),
}));

const renderWithFeatureFlags = (component: React.ReactElement, flags = {}) => {
  return render(
    <FeatureFlagProvider overrides={flags}>
      {component}
    </FeatureFlagProvider>
  );
};

describe('WordPressConnector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders (no longer self-gated by a build flag; parent drives visibility via connection state)', () => {
    // The mock/demo interface always renders when mounted — the connector is no
    // longer hidden by any build flag (both `enableWordPressIntegration` and the
    // `enableWordPressMockUI` mock-UI toggle have been retired).
    const { container } = renderWithFeatureFlags(<WordPressConnector />);

    expect(container.firstChild).not.toBeNull();
    expect(screen.getByText(/WordPress Connector/)).toBeInTheDocument();
  });

  it('always shows the mock UI when MediaLake is not available', () => {
    renderWithFeatureFlags(<WordPressConnector />);

    expect(screen.getByText(/WordPress Connector/)).toBeInTheDocument();
    expect(screen.getByText('Demo')).toBeInTheDocument();
    expect(screen.getByText('Demo Mode')).toBeInTheDocument();
  });

  it('should show connection status in mock mode', () => {
    renderWithFeatureFlags(<WordPressConnector />);

    expect(screen.getByText('Connection Status')).toBeInTheDocument();
    expect(screen.getByText(/Connected to/)).toBeInTheDocument();
  });

  it('should show WordPress posts in mock mode', () => {
    renderWithFeatureFlags(<WordPressConnector />);

    expect(screen.getByText('Recent WordPress Content')).toBeInTheDocument();
    expect(screen.getByText('WordPress Posts')).toBeInTheDocument();
  });

  it('renders a brand label when a brand scope is provided (multi-brand)', () => {
    renderWithFeatureFlags(<WordPressConnector brand="acme" />);

    expect(screen.getByText(/brand: acme/)).toBeInTheDocument();
  });
});
