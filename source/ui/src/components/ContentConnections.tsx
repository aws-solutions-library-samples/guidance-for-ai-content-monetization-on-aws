/**
 * ContentConnections (task 11.1 — WordPress re-home; refined)
 *
 * Compact content-domain slice of the Connections surface. It resolves the
 * active WordPress content Connections from the Connections API and renders a
 * COMPACT SUMMARY card for each — one per brand (multi-brand, Req 6.3a).
 *
 * When there is NO active content connection this component renders NOTHING
 * (returns null): the adapter catalog card in ConnectionsPage already conveys
 * the empty / "Activate" state, exactly like every other domain, so a second
 * bespoke empty-state panel here would be redundant. It likewise renders
 * nothing while loading (a transient spinner below the catalog card would
 * flash and be inconsistent with the other domains). The compact summary is
 * shown ONLY when an active WordPress connection exists. A real-source load
 * failure still surfaces an error Alert (mock-data honesty, #27).
 *
 * SCOPE (refined): on the Connections surface an active content connection
 * shows only its *presence + high-level metrics + a Test action* — it is NOT a
 * content-management surface. The full WordPress connector (post list, sync
 * modal, scan/sync buttons) has been retired from here; that content-management
 * UI is deferred to #14. The heavy `WordPressConnector` component is left in
 * place (deprecated) but no longer wired.
 *
 * Each summary card shows:
 *   - connection status (StatusIndicator),
 *   - high-level metrics (Available Posts / Synced Posts / Last Sync) sourced
 *     safely from `connection.config` when present, otherwise a neutral
 *     placeholder ("—") — never invented data (mock-data honesty, #27),
 *   - a "Test connection" action calling
 *     `connectionsService.testConnection(connection_id)` and surfacing the
 *     `{ok, message}` result inline (fail-informative; does not throw on
 *     `{ok:false}`),
 *   - the brand label when a brand is set.
 *
 * Demo_Toggle / mock-data honesty (#27): the connection lookup passes
 * `allowMock={demoDataEnabled}` from `useFeatureFlags()`; with demo off, a
 * real-source failure surfaces an error and an empty catalog renders an empty
 * state (no mock substitution). The shared `DemoDataBanner` (mounted in the app
 * chrome) is automatic.
 */

import React, { useEffect, useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Alert,
  StatusIndicator,
  ColumnLayout,
} from '@cloudscape-design/components';
import { useFeatureFlags } from '../contexts/FeatureFlagContext';
import { connectionsService, Connection, TestResult } from '../services/connectionsService';

interface ContentConnectionsProps {
  className?: string;
}

/** Neutral placeholder for a metric the connection record doesn't carry. */
const METRIC_PLACEHOLDER = '—';

/**
 * Read a high-level metric off the connection's non-secret `config` without
 * inventing data. Returns a neutral placeholder when the field is absent or
 * not a primitive (mock-data honesty, #27).
 */
const readMetric = (
  config: Record<string, unknown> | undefined,
  key: string,
): string => {
  const value = config?.[key];
  if (value === undefined || value === null) {
    return METRIC_PLACEHOLDER;
  }
  if (typeof value === 'string' || typeof value === 'number') {
    return String(value);
  }
  return METRIC_PLACEHOLDER;
};

/** Compact summary card for a single active WordPress content connection. */
const ContentConnectionSummary: React.FC<{ connection: Connection; className?: string }> = ({
  connection,
  className,
}) => {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await connectionsService.testConnection(connection.connection_id);
      setTestResult(result);
    } catch (err) {
      // A hard transport failure (vs. a fail-informative {ok:false}) is shown
      // inline as a failed test result — never thrown.
      setTestResult({
        ok: false,
        message: err instanceof Error ? err.message : 'Test failed.',
      });
    } finally {
      setTesting(false);
    }
  };

  const availablePosts = readMetric(connection.config, 'availablePosts');
  const syncedPosts = readMetric(connection.config, 'syncedPosts');
  const lastSync = readMetric(connection.config, 'lastSync');

  return (
    <Container
      className={className}
      header={
        <Header
          variant="h3"
          description={connection.brand ? `Brand: ${connection.brand}` : undefined}
          actions={
            <Button
              data-testid={`test-content-${connection.connection_id}`}
              onClick={handleTest}
              loading={testing}
            >
              Test connection
            </Button>
          }
        >
          WordPress content connection
        </Header>
      }
    >
      <SpaceBetween size="m">
        <StatusIndicator type={connection.active ? 'success' : 'stopped'}>
          {connection.active ? 'Connected' : 'Inactive'}
        </StatusIndicator>

        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Available Posts</Box>
            <Box variant="awsui-value-large">{availablePosts}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Synced Posts</Box>
            <Box variant="awsui-value-large">{syncedPosts}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Last Sync</Box>
            <Box variant="awsui-value-large">{lastSync}</Box>
          </div>
        </ColumnLayout>

        {testResult && (
          <StatusIndicator type={testResult.ok ? 'success' : 'error'}>
            {testResult.message ?? (testResult.ok ? 'Connection OK' : 'Connection failed')}
          </StatusIndicator>
        )}
      </SpaceBetween>
    </Container>
  );
};

const ContentConnections: React.FC<ContentConnectionsProps> = ({ className }) => {
  const { demoDataEnabled } = useFeatureFlags();
  const [wordpressConnections, setWordpressConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadConnections = async () => {
      setLoading(true);
      setError(null);
      try {
        // allowMock is driven by the runtime demo toggle (fail-safe default off).
        const active = await connectionsService.getActiveWordPressConnections(demoDataEnabled);
        if (!cancelled) {
          setWordpressConnections(active);
        }
      } catch (err) {
        if (!cancelled) {
          // Real-source failure surfaces an error — never a silent mock fallback.
          setError(err instanceof Error ? err.message : 'Failed to load content connections.');
          setWordpressConnections([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    loadConnections();
    return () => {
      cancelled = true;
    };
  }, [demoDataEnabled]);

  // While loading, render nothing — a transient spinner below the catalog card
  // would flash and is inconsistent with the other domains.
  if (loading) {
    return null;
  }

  if (error) {
    return (
      <Container className={className}>
        <Alert type="error" statusIconAriaLabel="Error" header="Unable to load content connections">
          {error}
        </Alert>
      </Container>
    );
  }

  // No active WordPress content connection — render NOTHING. The adapter
  // catalog card in ConnectionsPage already conveys the empty / "Activate"
  // state, consistent with every other domain (which render nothing when they
  // have no active connection).
  if (wordpressConnections.length === 0) {
    return null;
  }

  // One compact summary per active brand-keyed connection (multi-brand,
  // Req 6.3a). Full content-management/sync UI is out of scope here (deferred
  // to #14).
  return (
    <SpaceBetween size="l">
      {wordpressConnections.map((connection) => (
        <ContentConnectionSummary
          key={connection.connection_id}
          connection={connection}
          className={className}
        />
      ))}
    </SpaceBetween>
  );
};

export default ContentConnections;
