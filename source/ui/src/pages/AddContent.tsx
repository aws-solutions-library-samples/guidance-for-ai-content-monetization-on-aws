/**
 * Add Content Page
 *
 * Unified page for content ingestion - combines manual upload and WordPress
 * content connections.
 *
 * WordPress re-home (task 11.1): the WordPress tab is no longer gated by the
 * retired `enableWordPressIntegration` build flag. It is always available and
 * surfaces WordPress as a content-domain Connection; whether a connector
 * actually renders is driven by connection-activation state (an empty state is
 * shown when no WordPress content connection is active).
 */

import React, { useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Tabs
} from '@cloudscape-design/components';
import ContentUpload from './ContentUpload';
import WordPressIntegration from './WordPressIntegration';

const AddContent: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('manual-upload');

  const tabs = [
    {
      id: 'manual-upload',
      label: 'Manual Upload',
      content: <ContentUpload />
    },
    {
      id: 'wordpress',
      label: 'WordPress Integration',
      content: <WordPressIntegration />
    }
  ];

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Choose how you want to add content to the IAB framework. You can upload files directly or connect your WordPress website for automatic synchronization."
        >
          Add Content
        </Header>

        <Tabs
          activeTabId={activeTabId}
          onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
          tabs={tabs}
        />
      </SpaceBetween>
    </Container>
  );
};

export default AddContent;
