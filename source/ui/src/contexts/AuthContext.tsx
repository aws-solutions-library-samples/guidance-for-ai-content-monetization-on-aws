/**
 * Authentication Context
 * 
 * Provides shared authentication state between IAB and MediaLake UIs
 * Supports MediaLake integration for seamless user experience
 */

import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { getCurrentUser, signOut, AuthUser } from 'aws-amplify/auth';
import { getMediaLakeIntegration, MediaLakeIntegration } from '../services/mediaLakeService';

interface AuthContextType {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  signOut: () => Promise<void>;
  refreshAuth: () => Promise<void>;
  mediaLakeIntegration: {
    enabled: boolean;
    baseUrl?: string;
  };
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mediaLakeIntegration, setMediaLakeIntegration] = useState<MediaLakeIntegration>({ enabled: false });

  const refreshAuth = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const currentUser = await getCurrentUser();
      setUser(currentUser);
    } catch (err) {
      setUser(null);
      // Don't set error for unauthenticated users or missing configuration
      if (err instanceof Error && 
          !err.message.includes('not authenticated') && 
          !err.message.includes('not configured') &&
          !err.message.includes('UserPool')) {
        setError(err.message);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
      setUser(null);
      setError(null);
    } catch (err) {
      // Handle sign out gracefully even if auth is not configured
      setUser(null);
      setError(null);
      if (err instanceof Error && !err.message.includes('not configured')) {
        console.error('Sign out error:', err);
      }
    }
  };

  useEffect(() => {
    const initializeAuth = async () => {
      // Initialize MediaLake integration
      try {
        const integration = await getMediaLakeIntegration();
        setMediaLakeIntegration(integration);
      } catch (err) {
        console.warn('Failed to initialize MediaLake integration:', err);
      }
      
      // Initialize authentication
      await refreshAuth();
    };

    initializeAuth();
  }, []);

  const contextValue: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    error,
    signOut: handleSignOut,
    refreshAuth,
    mediaLakeIntegration: {
      enabled: mediaLakeIntegration.enabled,
      baseUrl: mediaLakeIntegration.config?.baseUrl
    }
  };

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
};

/**
 * Hook to access authentication context
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook is intentionally co-located with its provider
export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

/**
 * Component to conditionally render content for authenticated users
 */
interface AuthenticatedOnlyProps {
  fallback?: ReactNode;
  children: ReactNode;
}

export const AuthenticatedOnly: React.FC<AuthenticatedOnlyProps> = ({ 
  fallback = null, 
  children 
}) => {
  const { isAuthenticated, isLoading } = useAuth();
  
  if (isLoading) {
    return <>{fallback}</>;
  }
  
  return isAuthenticated ? <>{children}</> : <>{fallback}</>;
};

/**
 * Component to conditionally render content for unauthenticated users
 */
interface UnauthenticatedOnlyProps {
  fallback?: ReactNode;
  children: ReactNode;
}

export const UnauthenticatedOnly: React.FC<UnauthenticatedOnlyProps> = ({ 
  fallback = null, 
  children 
}) => {
  const { isAuthenticated, isLoading } = useAuth();
  
  if (isLoading) {
    return <>{fallback}</>;
  }
  
  return !isAuthenticated ? <>{children}</> : <>{fallback}</>;
};