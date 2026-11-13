/**
 * Authenticated App Component
 * 
 * This component wraps the main application and provides authentication context.
 * It handles the authenticated state and provides user information throughout the app.
 */

import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { TopNavigation, SideNavigation, SideNavigationProps } from '@cloudscape-design/components';
import { useAuthenticator } from '@aws-amplify/ui-react';

// Import our page components
import HomePage from '../pages/HomePage';
import UnifiedDashboard from '../pages/UnifiedDashboard';
import AddContent from '../pages/AddContent';
import ContentUpload from '../pages/ContentUpload';
import TestConsole from '../pages/TestConsole';
import ScrapeVsApiComparison from '../pages/testing/ScrapeVsApiComparison';
import DemoController from '../pages/DemoController';
import UserProfile from '../pages/UserProfile';
import WordPressIntegration from '../pages/WordPressIntegration';
import SystemStatus from '../pages/SystemStatus';
import SystemManagement from '../pages/SystemManagement';
import BotControl from '../pages/BotControl';
import PricingConfiguration from '../pages/PricingConfiguration';
import ApiDocumentation from '../pages/ApiDocumentation';
import ConnectionsPage from '../pages/ConnectionsPage';

// Partner management ("Onboard & Manage") — promoted from a Bot Control tab to its own destination
import BotRegistry from './BotRegistry';
// Licensing ("License") — the publisher licence management surface (#3)
import LicensesPage from '../pages/LicensesPage';

// Import Global Analytics Assistant
import GlobalAnalyticsAssistant from './GlobalAnalyticsAssistant';

// Import the demo-data banner
import DemoDataBanner from './DemoDataBanner';

// Import feature flag context
import { useFeatureFlags } from '../contexts/FeatureFlagContext';
// AppLayout tools/split-panel slots a page can fill (help panel, side panels)
import { AppLayoutSlotsProvider, SlottedAppLayout } from '../contexts/AppLayoutSlotsContext';
import { FeatureFlags } from '../config/featureFlags';
import { SOLUTION_NAME } from '../config/solutionName';

// Navigation items with feature flag filtering
const getNavigationItems = (isFeatureEnabled: (feature: keyof FeatureFlags) => boolean) => {
  const items = [
    {
      type: 'section',
      text: 'Overview',
      items: [
        { type: 'link', text: 'Home', href: '/' },
      ]
    },
    {
      type: 'section',
      text: 'Publisher Tools',
      items: [
        { type: 'link', text: 'Ingest', href: '/content/add' },
        { type: 'link', text: 'Enrich', href: '/system-status' },
        { type: 'link', text: 'Price', href: '/pricing' },
        { type: 'link', text: 'License', href: '/licenses' },
        { type: 'link', text: 'Onboard & Manage', href: '/partners' },
        { type: 'link', text: 'Control', href: '/bot-control' },
        { type: 'link', text: 'Test', href: '/test' },
        { type: 'link', text: 'Measure', href: '/dashboard' },
      ]
    }
  ];

  // Note: AI Agent Simulator is now integrated as "Test Bot Rules" in Publisher Tools

  // Add System Management section
  const systemManagementItems = [];
  
  // Add System Management for technical users
  systemManagementItems.push({ type: 'link', text: 'System Management', href: '/system-management' });

  // Connections page (integration-adapters #35). NAV PLACEMENT SEAM (#34):
  // placed under System Management as a reasonable default; where it ultimately
  // sits in the portal IA is a #34 decision — do not overhaul nav here.
  systemManagementItems.push({ type: 'link', text: 'Connections', href: '/connections' });
  
  // Add demo controller if enabled
  if (isFeatureEnabled('enableDemoController')) {
    systemManagementItems.push({ type: 'link', text: 'Demo Controller', href: '/demo' });
  }
  
  if (systemManagementItems.length > 0) {
    items.push({
      type: 'section',
      text: 'System Management',
      items: systemManagementItems
    });
  }

  return items;
};

export default function AuthenticatedApp() {
  const [navigationOpen, setNavigationOpen] = React.useState(false);
  const { user, signOut } = useAuthenticator((context) => [context.user]);
  const { isFeatureEnabled, demoDataEnabled, setDemoData } = useFeatureFlags();

  // Get navigation items based on feature flags
  const navigationItems = getNavigationItems(isFeatureEnabled);

  // Use HomePage as the default route
  const defaultRoute = <HomePage />;

  // Get user display name
  const getUserDisplayName = () => {
    if (user?.signInDetails?.loginId) {
      return user.signInDetails.loginId;
    }
    return user?.username || 'User';
  };

  // Get user role from attributes
  const getUserRole = () => {
    const attributes = user?.signInDetails?.loginId ? user : null;
    return attributes?.['custom:role'] || 'viewer';
  };

  return (
    <div style={{ height: '100vh' }}>
      <TopNavigation
        identity={{
          href: '/',
          title: SOLUTION_NAME
        }}
        utilities={[
          {
            type: 'button',
            iconName: demoDataEnabled ? 'status-warning' : 'status-info',
            text: demoDataEnabled ? 'Demo data: ON' : 'Demo data: OFF',
            ariaLabel: 'Toggle demo data mode',
            onClick: () => setDemoData(!demoDataEnabled),
          },
          {
            type: 'button',
            text: 'API Docs',
            onClick: () => window.open('/docs', '_blank', 'noopener'),
          },
          {
            type: 'menu-dropdown',
            text: getUserDisplayName(),
            items: [
              {
                id: 'profile',
                text: 'User Profile',
                href: '/profile'
              },
              {
                id: 'role',
                text: `Role: ${getUserRole()}`,
                disabled: true
              },
              { id: 'divider', text: '', itemType: 'divider' },
              {
                id: 'signout',
                text: 'Sign Out'
              }
            ],
            onItemClick: ({ detail }) => {
              console.log('Menu item clicked:', detail.id);
              if (detail.id === 'signout') {
                console.log('Sign out clicked - attempting logout...');
                console.log('Current user:', user);
                console.log('SignOut function:', typeof signOut);
                
                try {
                  const result = signOut();
                  console.log('SignOut result:', result);
                  
                  // If it returns a promise, handle it
                  if (result && typeof result.then === 'function') {
                    result.then(() => {
                      console.log('SignOut promise resolved');
                    }).catch((error) => {
                      console.error('SignOut promise rejected:', error);
                    });
                  }
                } catch (error) {
                  console.error('SignOut threw error:', error);
                }
              }
            }
          }
        ]}
      />
      <AppLayoutSlotsProvider>
      <SlottedAppLayout
        navigationOpen={navigationOpen}
        onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
        notifications={<DemoDataBanner />}
        navigation={
          <SideNavigation
            header={{ text: 'Navigation', href: '/' }}
            items={navigationItems as SideNavigationProps.Item[]}
          />
        }
        content={
          <Routes>
            <Route path="/" element={defaultRoute} />
            <Route path="/dashboard" element={<UnifiedDashboard />} />
            <Route path="/content/add" element={<AddContent />} />
            <Route path="/content/upload" element={<ContentUpload />} />
            <Route path="/system-status" element={<SystemStatus />} />
            <Route path="/system-management" element={<SystemManagement />} />
            {/* Connections page (integration-adapters #35, task 12.1).
                Placement under System Management is a #34 IA decision seam. */}
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/profile" element={<UserProfile />} />
            <Route path="/bot-control" element={<BotControl />} />
            <Route path="/partners" element={<BotRegistry />} />
            <Route path="/licenses" element={<LicensesPage />} />
            <Route path="/pricing" element={<PricingConfiguration />} />
            <Route path="/docs" element={<ApiDocumentation />} />

            {/* WordPress is now a content-domain Connection (task 11.1) —
                the route is always available; whether a connector renders is
                driven by connection-activation state, superseding the retired
                `enableWordPressIntegration` build flag. */}
            <Route path="/wordpress" element={<WordPressIntegration />} />
            <Route path="/test" element={<TestConsole />} />
            <Route path="/test/comparison" element={<ScrapeVsApiComparison />} />
            {isFeatureEnabled('enableDemoController') && (
              <Route path="/demo" element={<DemoController />} />
            )}
          </Routes>
        }
      />
      </AppLayoutSlotsProvider>
      
      {/* Global Analytics Assistant - Available on all pages */}
      <GlobalAnalyticsAssistant position="bottom-right" />
    </div>
  );
}