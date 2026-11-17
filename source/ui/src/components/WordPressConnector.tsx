/**
 * WordPress Connector Component
 *
 * @deprecated RETIRED from the Connections surface (frontend refinement).
 * As of this change, `ContentConnections` renders a COMPACT SUMMARY
 * (status + high-level metrics + Test connection) for an active WordPress
 * content connection instead of this heavy connector. The full
 * content-management UI (post list, sync modal, scan/sync buttons) does not
 * belong on the Connections surface and its real form is deferred to #14.
 *
 * This file is intentionally kept in place (not deleted) to preserve history
 * and its tests (refactoring-safety), but it is no longer imported or wired
 * anywhere in the app. Do not re-wire it onto the Connections surface; the
 * content-management surface will be rebuilt under #14.
 *
 * Provides WordPress integration UI with MediaLake integration support
 * and mock UI capabilities for demonstration purposes.
 */

import React, { useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Alert,
  StatusIndicator,
  ColumnLayout,
  Cards,
  Badge,
  Link,
  Modal,
  ProgressBar
} from '@cloudscape-design/components';
import { useMediaLakeIntegration } from '../hooks/useMediaLakeIntegration';

interface WordPressPost {
  id: number;
  title: string;
  date: string;
  categories: string[];
  tags: string[];
  iabRelevant: boolean;
  status: 'available' | 'synced' | 'syncing' | 'error';
  excerpt?: string;
}

interface WordPressConnectorProps {
  className?: string;
  /**
   * Optional brand/tenant scope for this connector instance. Set by
   * ContentConnections when rendering one connector per active brand-keyed
   * WordPress content connection (multi-brand, Req 6.3a). Null/undefined for
   * single-brand deployments.
   */
  brand?: string;
}

const mockWordPressContent: WordPressPost[] = [
  {
    id: 1,
    title: "AI Revolution in Healthcare: Latest Developments",
    date: "2025-01-15",
    categories: ["Technology", "Healthcare"],
    tags: ["AI", "Machine Learning", "Healthcare"],
    iabRelevant: true,
    status: "synced",
    excerpt: "Exploring the latest developments in AI applications for healthcare..."
  },
  {
    id: 2,
    title: "Sustainable Energy Investment Trends 2025",
    date: "2025-01-14",
    categories: ["Business", "Environment"],
    tags: ["Sustainability", "Investment", "Energy"],
    iabRelevant: true,
    status: "available",
    excerpt: "Analysis of sustainable energy investment trends and opportunities..."
  },
  {
    id: 3,
    title: "Machine Learning Applications in Finance",
    date: "2025-01-13",
    categories: ["Technology", "Finance"],
    tags: ["Machine Learning", "Finance", "FinTech"],
    iabRelevant: true,
    status: "available",
    excerpt: "How machine learning is transforming the financial services industry..."
  },
  {
    id: 4,
    title: "Hello World Test Post",
    date: "2025-01-12",
    categories: ["Test"],
    tags: ["Test"],
    iabRelevant: false,
    status: "available",
    excerpt: "This is a test post..."
  }
];

const WordPressConnector: React.FC<WordPressConnectorProps> = ({ className, brand }) => {
  const { integration: mediaLakeIntegration, isAvailable: mediaLakeAvailable, isLoading: loading, openConnectorConfig } = useMediaLakeIntegration();
  const [posts, setPosts] = useState<WordPressPost[]>(mockWordPressContent);
  const [selectedPosts, setSelectedPosts] = useState<number[]>([]);
  const [showSyncModal, setShowSyncModal] = useState(false);
  const [syncProgress, setSyncProgress] = useState(0);
  const [syncing, setSyncing] = useState(false);

  // Mock WordPress configuration
  const [mockConfig] = useState({
    siteUrl: 'https://example-publisher.com',
    username: 'api-user',
    status: 'connected',
    lastSync: '2025-01-15T10:30:00Z',
    availablePosts: 156,
    syncedPosts: 23
  });

  const handleOpenMediaLakeConnector = () => {
    openConnectorConfig('wordpress');
  };

  const handleMetadataScan = () => {
    // Mock metadata scan
    console.log('Scanning WordPress metadata...');
    // In real implementation, this would trigger a metadata scan
  };

  const handleSelectiveSync = () => {
    setShowSyncModal(true);
  };

  const handleSyncPosts = async () => {
    if (selectedPosts.length === 0) return;

    setSyncing(true);
    setSyncProgress(0);

    // Mock sync process
    for (let i = 0; i <= 100; i += 10) {
      await new Promise(resolve => setTimeout(resolve, 200));
      setSyncProgress(i);
    }

    // Update post statuses
    setPosts(prevPosts => 
      prevPosts.map(post => 
        selectedPosts.includes(post.id) 
          ? { ...post, status: 'synced' as const }
          : post
      )
    );

    setSyncing(false);
    setShowSyncModal(false);
    setSelectedPosts([]);
  };

  // NOTE: rendering is driven by connection-activation state (the parent
  // ContentConnections only mounts this connector for an active WordPress
  // content connection), superseding the retired `enableWordPressIntegration`
  // build flag (same pattern as the retired `enableTestConsole` flag).

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="xl">
          <StatusIndicator type="loading">Loading WordPress connector...</StatusIndicator>
        </Box>
      </Container>
    );
  }

  // MediaLake is available - show deep link
  if (mediaLakeIntegration.enabled && mediaLakeAvailable) {
    return (
      <Container className={className}>
        <Header
          variant="h2"
          description={brand
            ? `Configure your WordPress connector through MediaLake (brand: ${brand})`
            : 'Configure your WordPress connector through MediaLake'}
        >
          WordPress Connector Configuration
        </Header>
        
        <SpaceBetween size="l">
          <Alert
            statusIconAriaLabel="Info"
            header="MediaLake Integration Available"
          >
            Your WordPress connector is managed through MediaLake. Click the button below to configure your WordPress integration.
          </Alert>

          <Box textAlign="center">
            <Button
              variant="primary"
              iconName="external"
              onClick={handleOpenMediaLakeConnector}
            >
              Open MediaLake Connector Configuration
            </Button>
          </Box>

          <Box variant="small" textAlign="center" color="text-body-secondary">
            This will open MediaLake's connector configuration interface in a new tab.
          </Box>
        </SpaceBetween>
      </Container>
    );
  }

  // MediaLake not available - show the demo/mock interface.
  //
  // NOTE: this deprecated connector previously gated this branch behind the
  // `enableWordPressMockUI` feature flag, which has since been retired from the
  // env plumbing along with the WordPressConnector's role on the Connections
  // surface. The connector is no longer wired anywhere; when mounted it simply
  // renders its mock/demo UI.
  return (
    <Container className={className}>
      <Header
        variant="h2"
        description={brand
          ? `WordPress integration for content synchronization (brand: ${brand})`
          : 'WordPress integration for content synchronization'}
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={handleMetadataScan}>Scan for New Content</Button>
            <Button variant="primary" onClick={handleSelectiveSync}>
              Select Content to Sync
            </Button>
          </SpaceBetween>
        }
      >
        WordPress Connector <Badge color="blue">Demo</Badge>
      </Header>

      <SpaceBetween size="l">
        <Alert
          statusIconAriaLabel="Info"
          type="info"
          header="Demo Mode"
          dismissible
        >
          This is a mock interface for demonstration purposes. In production, WordPress connector 
          configuration is handled through MediaLake UI.
        </Alert>

        {/* Connection Status */}
        <Container>
          <Header variant="h3">Connection Status</Header>
          <Box>
            <StatusIndicator type="success">
              Connected to {mockConfig.siteUrl}
            </StatusIndicator>
          </Box>
        </Container>

        {/* Statistics */}
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Available Posts</Box>
            <Box variant="awsui-value-large">{mockConfig.availablePosts}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Synced Posts</Box>
            <Box variant="awsui-value-large">{mockConfig.syncedPosts}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Last Sync</Box>
            <Box variant="awsui-value-large">
              {new Date(mockConfig.lastSync).toLocaleDateString()}
            </Box>
          </div>
        </ColumnLayout>

        {/* Recent Content */}
        <Container>
          <Header variant="h3">Recent WordPress Content</Header>
          <Cards
            cardDefinition={{
              header: item => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Link href="#" fontSize="heading-m">{item.title}</Link>
                  <Badge 
                    color={item.iabRelevant ? "green" : "grey"}
                  >
                    {item.iabRelevant ? "IAB Relevant" : "Not Relevant"}
                  </Badge>
                  <Badge 
                    color={
                      item.status === 'synced' ? 'green' :
                      item.status === 'syncing' ? 'blue' :
                      item.status === 'error' ? 'red' : 'grey'
                    }
                  >
                    {item.status === 'synced' ? 'Synced' :
                     item.status === 'syncing' ? 'Syncing...' :
                     item.status === 'error' ? 'Error' : 'Available'}
                  </Badge>
                </SpaceBetween>
              ),
              sections: [
                {
                  id: "description",
                  content: item => (
                    <SpaceBetween size="s">
                      <Box variant="small" color="text-body-secondary">
                        {item.date} • {item.categories.join(', ')}
                      </Box>
                      {item.excerpt && (
                        <Box>{item.excerpt}</Box>
                      )}
                      <Box>
                        <strong>Tags:</strong> {item.tags.join(', ')}
                      </Box>
                    </SpaceBetween>
                  )
                }
              ]
            }}
            cardsPerRow={[
              { cards: 1 },
              { minWidth: 500, cards: 2 }
            ]}
            items={posts}
            loadingText="Loading content"
            empty={
              <Box textAlign="center" color="inherit">
                <b>No WordPress content found</b>
                <Box variant="p" color="inherit">
                  No content available to display.
                </Box>
              </Box>
            }
            header={
              <Header
                counter={`(${posts.length})`}
                actions={
                  <Button
                    onClick={handleSelectiveSync}
                    disabled={posts.filter(p => p.iabRelevant && p.status === 'available').length === 0}
                  >
                    Sync Selected Content
                  </Button>
                }
              >
                WordPress Posts
              </Header>
            }
          />
        </Container>

        {/* Sync Modal */}
        <Modal
          onDismiss={() => setShowSyncModal(false)}
          visible={showSyncModal}
          closeAriaLabel="Close modal"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setShowSyncModal(false)}>
                  Cancel
                </Button>
                <Button 
                  variant="primary" 
                  onClick={handleSyncPosts}
                  disabled={selectedPosts.length === 0 || syncing}
                  loading={syncing}
                >
                  Sync {selectedPosts.length} Posts
                </Button>
              </SpaceBetween>
            </Box>
          }
          header="Select Content to Sync"
        >
          <SpaceBetween size="m">
            <Box>
              Select WordPress posts to sync to the IAB framework. Only IAB-relevant content is recommended for synchronization.
            </Box>

            {syncing && (
              <Box>
                <Box variant="awsui-key-label">Sync Progress</Box>
                <ProgressBar
                  value={syncProgress}
                  additionalInfo={`${syncProgress}% complete`}
                  description="Syncing selected posts to IAB framework..."
                />
              </Box>
            )}

            <Cards
              cardDefinition={{
                header: item => (
                  <SpaceBetween direction="horizontal" size="xs">
                    <input
                      type="checkbox"
                      checked={selectedPosts.includes(item.id)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedPosts([...selectedPosts, item.id]);
                        } else {
                          setSelectedPosts(selectedPosts.filter(id => id !== item.id));
                        }
                      }}
                      disabled={!item.iabRelevant || item.status !== 'available'}
                    />
                    <Box variant="strong">{item.title}</Box>
                    <Badge color={item.iabRelevant ? "green" : "grey"}>
                      {item.iabRelevant ? "IAB Relevant" : "Not Relevant"}
                    </Badge>
                  </SpaceBetween>
                ),
                sections: [
                  {
                    id: "details",
                    content: item => (
                      <Box variant="small" color="text-body-secondary">
                        {item.date} • {item.categories.join(', ')}
                      </Box>
                    )
                  }
                ]
              }}
              cardsPerRow={[{ cards: 1 }]}
              items={posts.filter(p => p.iabRelevant && p.status === 'available')}
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No content available for sync</b>
                  <Box variant="p" color="inherit">
                    All IAB-relevant content has already been synced.
                  </Box>
                </Box>
              }
            />
          </SpaceBetween>
        </Modal>
      </SpaceBetween>
    </Container>
  );
};

export default WordPressConnector;