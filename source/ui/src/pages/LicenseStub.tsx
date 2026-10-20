/**
 * License ("License" workflow stage) — placeholder surface.
 *
 * The licensing/entitlement management UI is not built yet; it lands with the RSL license
 * lifecycle work (GitLab #3). This page exists so the full publisher workflow
 * (Ingest → Enrich → Price → License → Onboard & Manage → Control → Measure) is visible and
 * navigable today.
 *
 * IMPORTANT (mock-data honesty, per steering): this is an explicit "not yet available"
 * placeholder. It renders NO data and MUST NOT be mistaken for working functionality — no
 * mock/sample licenses are shown. When #3 lands, this file is replaced by the real surface.
 */

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Alert,
  Box,
} from '@cloudscape-design/components';

export default function LicenseStub() {
  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Define license terms and entitlements governing how AI systems may use your content."
        >
          License
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info" header="Coming soon">
          Licensing and entitlement management is not yet available. This surface will let you
          author RSL license terms, assign licenses to content tiers, and manage partner
          entitlements. Tracked in <b>#3</b> (Partner licensing &amp; usage metering).
        </Alert>
        <Box variant="p" color="text-body-secondary">
          This is a placeholder page that shows where licensing fits in the publisher workflow
          (Ingest → Enrich → Price → <b>License</b> → Onboard &amp; Manage → Control → Measure).
          It intentionally displays no data.
        </Box>
      </SpaceBetween>
    </Container>
  );
}
