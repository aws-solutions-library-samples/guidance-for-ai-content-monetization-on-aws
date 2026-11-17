/**
 * User Profile Page
 * 
 * Displays and allows editing of user profile information
 */

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  Button,
  Form,
  FormField,
  Input,
  Select,
  Alert,
  StatusIndicator
} from '@cloudscape-design/components';
import { useAuthenticator } from '@aws-amplify/ui-react';

const roleOptions = [
  { label: 'Administrator', value: 'admin' },
  { label: 'Publisher', value: 'publisher' },
  { label: 'Viewer', value: 'viewer' }
];

export default function UserProfile() {
  const { user } = useAuthenticator((context) => [context.user]);
  
  // Get user attributes
  const getUserAttribute = (key: string) => {
    return user?.signInDetails?.loginId || user?.[key] || '';
  };

  const getUserRole = () => {
    return user?.['custom:role'] || 'viewer';
  };

  const getVerificationStatus = () => {
    return user?.['email_verified'] === 'true' || user?.['email_verified'] === true;
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Manage your account settings and profile information"
      >
        User Profile
      </Header>

      <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
        <Container
          header={
            <Header variant="h2">
              Account Information
            </Header>
          }
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Email Address">
                <Input
                  value={getUserAttribute('email')}
                  disabled
                  placeholder="Not available"
                />
              </FormField>

              <FormField label="Username">
                <Input
                  value={user?.username || 'Not available'}
                  disabled
                />
              </FormField>

              <FormField label="First Name">
                <Input
                  value={getUserAttribute('given_name')}
                  placeholder="Not provided"
                  disabled
                />
              </FormField>

              <FormField label="Last Name">
                <Input
                  value={getUserAttribute('family_name')}
                  placeholder="Not provided"
                  disabled
                />
              </FormField>

              <FormField label="Organization">
                <Input
                  value={getUserAttribute('custom:organization')}
                  placeholder="Not provided"
                  disabled
                />
              </FormField>

              <FormField label="Role">
                <Select
                  selectedOption={roleOptions.find(opt => opt.value === getUserRole())}
                  options={roleOptions}
                  disabled
                />
              </FormField>
            </SpaceBetween>
          </Form>
        </Container>

        <Container
          header={
            <Header variant="h2">
              Account Status
            </Header>
          }
        >
          <SpaceBetween size="m">
            <Box>
              <Box variant="awsui-key-label">Email Verification</Box>
              <StatusIndicator type={getVerificationStatus() ? 'success' : 'warning'}>
                {getVerificationStatus() ? 'Verified' : 'Not Verified'}
              </StatusIndicator>
            </Box>

            <Box>
              <Box variant="awsui-key-label">Account Status</Box>
              <StatusIndicator type="success">
                Active
              </StatusIndicator>
            </Box>

            <Box>
              <Box variant="awsui-key-label">MFA Status</Box>
              <StatusIndicator type="info">
                Optional
              </StatusIndicator>
            </Box>

            <Box>
              <Box variant="awsui-key-label">Last Sign In</Box>
              <Box variant="p">
                {new Date().toLocaleDateString()}
              </Box>
            </Box>
          </SpaceBetween>
        </Container>
      </Grid>

      <Container
        header={
          <Header variant="h2">
            Security Settings
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info" header="Password Management">
            Password changes are managed through AWS Cognito. Use the "Forgot Password" 
            option on the login page to reset your password.
          </Alert>

          <Alert type="info" header="Multi-Factor Authentication">
            MFA is optional for this demo environment. In production deployments, 
            MFA should be enabled for enhanced security.
          </Alert>

          <SpaceBetween direction="horizontal" size="xs">
            <Button>Change Password</Button>
            <Button>Setup MFA</Button>
          </SpaceBetween>
        </SpaceBetween>
      </Container>

      <Container
        header={
          <Header variant="h2">
            Session Information
          </Header>
        }
      >
        <SpaceBetween size="s">
          <Box>
            <Box variant="awsui-key-label">User ID</Box>
            <Box variant="code">{user?.userId || 'Not available'}</Box>
          </Box>

          <Box>
            <Box variant="awsui-key-label">Sign-In Method</Box>
            <Box variant="p">Cognito User Pool</Box>
          </Box>

          <Box>
            <Box variant="awsui-key-label">Session Status</Box>
            <StatusIndicator type="success">Active</StatusIndicator>
          </Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}