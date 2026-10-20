/**
 * Main App Component with AWS Amplify Authentication
 * 
 * This component provides the authentication wrapper for the entire application
 * using AWS Amplify and Cognito for secure user authentication.
 */

import React, { useEffect } from 'react';
import { BrowserRouter } from 'react-router-dom';
import { Authenticator } from '@aws-amplify/ui-react';
import { applyMode, Mode } from '@cloudscape-design/global-styles';

// Import authentication configuration
import { configureAmplify, isAuthConfigured } from './config/amplifyConfig';

// Import feature flag context
import { FeatureFlagProvider } from './contexts/FeatureFlagContext';

// Import the authenticated app component
import AuthenticatedApp from './components/AuthenticatedApp';

// Solution display name (manifest-driven, with fallback)
import { SOLUTION_NAME } from './config/solutionName';

// Import Amplify UI styles
import '@aws-amplify/ui-react/styles.css';

// Apply light mode by default
applyMode(Mode.Light);

// Custom authentication components
const components = {
  Header() {
    return (
      <div style={{ textAlign: 'center', padding: '2rem 0' }}>
        <h1>{SOLUTION_NAME}</h1>
        <p>Secure access to the content monetization capabilities</p>
      </div>
    );
  },
  Footer() {
    return (
      <div style={{ textAlign: 'center', padding: '1rem 0', fontSize: '0.875rem', color: '#666' }}>
        <p>Powered by AWS Cognito • Secure Authentication</p>
      </div>
    );
  }
};

// Custom form fields for sign up
const formFields = {
  signUp: {
    given_name: {
      label: 'First Name',
      placeholder: 'Enter your first name',
      required: false,
      order: 1
    },
    family_name: {
      label: 'Last Name', 
      placeholder: 'Enter your last name',
      required: false,
      order: 2
    },
    email: {
      label: 'Email Address',
      placeholder: 'Enter your email address',
      required: true,
      order: 3
    },
    'custom:organization': {
      label: 'Organization',
      placeholder: 'Enter your organization name',
      required: false,
      order: 4
    },
    password: {
      label: 'Password',
      placeholder: 'Enter your password',
      required: true,
      order: 5
    },
    confirm_password: {
      label: 'Confirm Password',
      placeholder: 'Confirm your password',
      required: true,
      order: 6
    }
  }
};

function App() {
  const [amplifyConfigured, setAmplifyConfigured] = React.useState(false);
  const [configError, setConfigError] = React.useState<string | null>(null);

  useEffect(() => {
    // Configure Amplify on app startup
    if (isAuthConfigured()) {
      const success = configureAmplify();
      setAmplifyConfigured(success);
      if (!success) {
        setConfigError('Failed to configure authentication. Please check your environment variables.');
      }
    } else {
      setConfigError('Authentication is not properly configured. Please set the required environment variables.');
      console.error('Missing required environment variables:');
      console.error('- VITE_USER_POOL_ID');
      console.error('- VITE_USER_POOL_CLIENT_ID');
      console.error('- VITE_AWS_REGION (optional, defaults to us-west-2)');
    }
  }, []);

  // Show error state if configuration failed
  if (configError) {
    return (
      <div style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center', 
        height: '100vh',
        flexDirection: 'column',
        padding: '2rem',
        textAlign: 'center'
      }}>
        <h1>Configuration Error</h1>
        <p style={{ color: '#d13212', marginBottom: '1rem' }}>{configError}</p>
        <p style={{ fontSize: '0.875rem', color: '#666' }}>
          Please contact your administrator or check the deployment configuration.
        </p>
      </div>
    );
  }

  // Show loading state while configuring
  if (!amplifyConfigured) {
    return (
      <div style={{ 
        display: 'flex', 
        justifyContent: 'center', 
        alignItems: 'center', 
        height: '100vh' 
      }}>
        <div style={{ textAlign: 'center' }}>
          <h2>Loading...</h2>
          <p>Configuring authentication...</p>
        </div>
      </div>
    );
  }

  // Check if user registration is enabled
  const enableUserRegistration = import.meta.env.VITE_ENABLE_USER_REGISTRATION === 'true';

  return (
    <FeatureFlagProvider>
      <BrowserRouter>
        <Authenticator
          components={components}
          formFields={formFields}
          signUpAttributes={enableUserRegistration ? ['email', 'given_name', 'family_name'] : []}
          socialProviders={[]}
          hideSignUp={!enableUserRegistration}
        >
          {() => (
            <AuthenticatedApp />
          )}
        </Authenticator>
      </BrowserRouter>
    </FeatureFlagProvider>
  );
}

export default App;