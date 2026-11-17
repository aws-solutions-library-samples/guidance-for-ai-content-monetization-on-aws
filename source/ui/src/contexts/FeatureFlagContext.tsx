/**
 * Feature Flag Context
 * 
 * Provides feature flag configuration throughout the React application
 */

import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import {
  FeatureFlags,
  featureFlags,
  isDemoModeEnabled,
  readInitial,
  DEMO_DATA_STORAGE_KEY,
} from '../config/featureFlags';

interface FeatureFlagContextType {
  flags: FeatureFlags;
  isDemoMode: boolean;
  isFeatureEnabled: (feature: keyof FeatureFlags) => boolean;
  /**
   * Runtime demo-data toggle. When `true`, service Mock_Sites are allowed to
   * substitute mock data (behind the demo banner); when `false`, services
   * return real data only (empty-state on empty, error on failure).
   */
  demoDataEnabled: boolean;
  /**
   * Flip the runtime demo-data toggle. Persists to sessionStorage and
   * re-renders consumers immediately — no page reload.
   */
  setDemoData: (enabled: boolean) => void;
}

const FeatureFlagContext = createContext<FeatureFlagContextType | undefined>(undefined);

interface FeatureFlagProviderProps {
  children: ReactNode;
  overrides?: Partial<FeatureFlags>;
}

export const FeatureFlagProvider: React.FC<FeatureFlagProviderProps> = ({ 
  children, 
  overrides = {} 
}) => {
  // Seed the runtime demo-data toggle. An explicit `overrides.enableMockData`
  // (used by tests) wins; otherwise read sessionStorage → env → false.
  const [demoDataEnabled, setDemoDataState] = useState<boolean>(() =>
    overrides.enableMockData ?? readInitial(DEMO_DATA_STORAGE_KEY, featureFlags.enableMockData)
  );

  const setDemoData = useCallback((enabled: boolean) => {
    try {
      sessionStorage.setItem(DEMO_DATA_STORAGE_KEY, String(enabled));
    } catch {
      // storage unavailable — keep the in-memory state anyway
    }
    setDemoDataState(enabled);
  }, []);

  // `enableMockData` in the exposed flags reflects the live runtime toggle,
  // not the frozen build-time module constant. Other flags keep their
  // build-time / override values.
  const effectiveFlags: FeatureFlags = {
    ...featureFlags,
    ...overrides,
    enableMockData: demoDataEnabled,
  };

  const contextValue: FeatureFlagContextType = {
    flags: effectiveFlags,
    isDemoMode: isDemoModeEnabled(),
    isFeatureEnabled: (feature: keyof FeatureFlags) => effectiveFlags[feature],
    demoDataEnabled,
    setDemoData,
  };

  return (
    <FeatureFlagContext.Provider value={contextValue}>
      {children}
    </FeatureFlagContext.Provider>
  );
};

/**
 * Hook to access feature flags in components
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook is intentionally co-located with its provider
export const useFeatureFlags = (): FeatureFlagContextType => {
  const context = useContext(FeatureFlagContext);
  if (context === undefined) {
    throw new Error('useFeatureFlags must be used within a FeatureFlagProvider');
  }
  return context;
};

/**
 * Higher-order component to conditionally render components based on feature flags
 */
interface WithFeatureFlagProps {
  feature: keyof FeatureFlags;
  fallback?: ReactNode;
  children: ReactNode;
}

export const WithFeatureFlag: React.FC<WithFeatureFlagProps> = ({ 
  feature, 
  fallback = null, 
  children 
}) => {
  const { isFeatureEnabled } = useFeatureFlags();
  
  return isFeatureEnabled(feature) ? <>{children}</> : <>{fallback}</>;
};

/**
 * Component to conditionally render content in demo mode only
 */
interface DemoOnlyProps {
  fallback?: ReactNode;
  children: ReactNode;
}

export const DemoOnly: React.FC<DemoOnlyProps> = ({ fallback = null, children }) => {
  const { isDemoMode } = useFeatureFlags();
  
  return isDemoMode ? <>{children}</> : <>{fallback}</>;
};

/**
 * Component to conditionally render content in production mode only
 */
interface ProductionOnlyProps {
  fallback?: ReactNode;
  children: ReactNode;
}

export const ProductionOnly: React.FC<ProductionOnlyProps> = ({ fallback = null, children }) => {
  const { isDemoMode } = useFeatureFlags();
  
  return !isDemoMode ? <>{children}</> : <>{fallback}</>;
};