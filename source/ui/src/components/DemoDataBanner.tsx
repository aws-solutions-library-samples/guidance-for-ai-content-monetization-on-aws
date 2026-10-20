/**
 * DemoDataBanner (#27)
 *
 * A persistent, app-wide warning banner shown whenever the runtime demo-data
 * toggle is ON. It makes the "this screen may be showing mock data, not live
 * data" state impossible to miss — the trust safeguard that pairs with the
 * service `allowMock` gate.
 *
 * Renders nothing when the toggle is OFF (the production-safe default).
 */

import React from 'react';
import { Flashbar } from '@cloudscape-design/components';
import { useFeatureFlags } from '../contexts/FeatureFlagContext';

export const DEMO_BANNER_TEXT = '⚠️ Demo data — not live';

export default function DemoDataBanner() {
  const { demoDataEnabled } = useFeatureFlags();

  if (!demoDataEnabled) {
    return null;
  }

  return (
    <Flashbar
      items={[
        {
          type: 'warning',
          dismissible: false,
          content: DEMO_BANNER_TEXT,
          id: 'demo-data-banner',
        },
      ]}
    />
  );
}
