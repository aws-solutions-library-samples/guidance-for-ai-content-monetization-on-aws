/**
 * Feature Flags Configuration
 * 
 * This module manages feature flags for the implementation on AWS Guidance UI.
 * Feature flags allow enabling/disabling demo functionality for production deployments.
 */

export interface FeatureFlags {
  enableDemoController: boolean;
  enableDemoScenarios: boolean;
  enableMockData: boolean;
  enableDemoNavigation: boolean;
  // NOTE: `enableWordPressIntegration` and `enableWordPressMockUI` were both
  // retired by the WordPress re-home. WordPress is now surfaced as a
  // content-domain Connection driven by connection-activation state, not a
  // build-time flag — the same pattern as the previously retired
  // `enableTestConsole` flag. The mock-UI toggle was tied to the retired
  // WordPressConnector, which is no longer wired anywhere.
}

/**
 * sessionStorage key backing the runtime demo-data toggle (`enableMockData`).
 * Kept in one place so the provider and this module agree.
 */
export const DEMO_DATA_STORAGE_KEY = 'cm.demoDataEnabled';

/**
 * Read the initial value of a runtime-toggleable flag.
 *
 * Precedence: sessionStorage (a prior in-app toggle this session) → the
 * build-time env default → `false` (production-safe fallback). Any storage
 * access failure (private mode, disabled storage) falls back to `envDefault`.
 *
 * Only `enableMockData` is runtime-toggleable; all other flags remain
 * build-time.
 */
export function readInitial(key: string, envDefault: boolean): boolean {
  try {
    const stored =
      typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(key) : null;
    if (stored !== null) {
      return stored === 'true';
    }
  } catch {
    // storage unavailable — fall through to the env default
  }
  return envDefault;
}

/**
 * Get feature flags from environment variables
 * Defaults to false for production safety
 */
function getFeatureFlagsFromEnv(): FeatureFlags {
  const isDemoEnabled = import.meta.env.VITE_ENABLE_DEMO === 'true';

  return {
    enableDemoController: isDemoEnabled,
    enableDemoScenarios: isDemoEnabled,
    // Runtime-toggleable: seed from sessionStorage, else the env default.
    enableMockData: readInitial(DEMO_DATA_STORAGE_KEY, isDemoEnabled),
    enableDemoNavigation: isDemoEnabled,
  };
}

/**
 * Feature flags configuration
 * Can be overridden for testing or specific deployments
 */
export const featureFlags: FeatureFlags = getFeatureFlagsFromEnv();

/**
 * Utility function to check if demo mode is enabled
 */
export const isDemoModeEnabled = (): boolean => {
  return featureFlags.enableDemoController;
};

/**
 * Utility function to check if a specific feature is enabled
 */
export const isFeatureEnabled = (feature: keyof FeatureFlags): boolean => {
  return featureFlags[feature];
};

/**
 * Get demo-specific configuration
 */
export const getDemoConfig = () => {
  if (!isDemoModeEnabled()) {
    return null;
  }

  return {
    mockDataEnabled: featureFlags.enableMockData,
    scenariosEnabled: featureFlags.enableDemoScenarios,
  };
};

/**
 * Development helper to override feature flags
 * Only works in development mode
 */
export const overrideFeatureFlags = (overrides: Partial<FeatureFlags>): void => {
  if (import.meta.env.DEV) {
    Object.assign(featureFlags, overrides);
    console.log('Feature flags overridden:', featureFlags);
  } else {
    console.warn('Feature flag overrides are only allowed in development mode');
  }
};