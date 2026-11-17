/**
 * WordPress Integration Page
 *
 * Content-connection view for WordPress. As of the WordPress re-home
 * (task 11.1), WordPress is surfaced as a content-domain Connection driven by
 * connection-activation state rather than the retired
 * `enableWordPressIntegration` build flag. This page now renders the
 * content-connection surface (ContentConnections), which resolves the active
 * WordPress content connection(s) and renders the preserved connector — one
 * per brand (multi-brand, Req 6.3a).
 *
 * The full Connections page is owned by task 12.1; this page is a thin content
 * view over the same connectionsService seam.
 */

import React from 'react';
import { ContentLayout, Container, Header } from '@cloudscape-design/components';
import ContentConnections from '../components/ContentConnections';

const WordPressIntegration: React.FC = () => {
  return (
    <ContentLayout>
      <Container>
        <Header
          variant="h1"
          description="Connect and synchronize content from your WordPress website to the IAB framework."
        >
          WordPress Integration
        </Header>
        <ContentConnections />
      </Container>
    </ContentLayout>
  );
};

export default WordPressIntegration;
