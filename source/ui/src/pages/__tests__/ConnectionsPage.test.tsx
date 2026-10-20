/**
 * ConnectionsPage — Vitest UI tests (task 12.2)
 *
 * Validates the Connections page UI for the integration-adapters spec (#35):
 *   - Req 6.2  — the adapter catalog renders BY DOMAIN with available/active flags.
 *   - Req 6.3  — activate / edit / deactivate CRUD flows call the right service
 *                methods; Test connection surfaces the adapter test() ok/diagnostic.
 *   - Req 6.3b — WordPress surfaces as a content-domain connection gated by
 *                connection-activation state (not the retired build flag), behavior
 *                preserved (asserted via the composed ContentConnections surface).
 *   - Req 6.9  — the marketplace-listing mirror panel is read-only (no edit affordance).
 *   - #27 Demo_Toggle — no unconditional mock fallback: with demo OFF (default) a
 *                service failure surfaces an error (not mock data), and the page passes
 *                allowMock={demoDataEnabled} through to the service getters.
 *
 * The connectionsService and FeatureFlagContext are mocked — no real API is hit.
 * The composed ContentConnections → WordPressConnector needs the MediaLake hook /
 * service mocked (same pattern as WordPressConnector.test.tsx).
 *
 * _Requirements: 6.2, 6.3, 6.3b, 6.9_
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// --- Mocks -----------------------------------------------------------------

// Mock the connectionsService so no real API is hit. We re-export the module's
// constants (CONNECTION_DOMAINS, WORDPRESS_ADAPTER) unchanged so the page's
// domain iteration + labels are exercised for real.
vi.mock('../../services/connectionsService', async () => {
  const actual = await vi.importActual<
    typeof import('../../services/connectionsService')
  >('../../services/connectionsService');
  return {
    ...actual,
    connectionsService: {
      listAdapters: vi.fn(),
      listConnections: vi.fn(),
      getMirror: vi.fn(),
      activateConnection: vi.fn(),
      updateConnection: vi.fn(),
      deactivateConnection: vi.fn(),
      testConnection: vi.fn(),
      // Used by the composed ContentConnections surface.
      getActiveWordPressConnections: vi.fn(),
    },
  };
});

// Mock the FeatureFlagContext so we can drive demoDataEnabled directly.
const mockUseFeatureFlags = vi.fn();
vi.mock('../../contexts/FeatureFlagContext', () => ({
  useFeatureFlags: () => mockUseFeatureFlags(),
}));

// The content domain composes ContentConnections → WordPressConnector, which
// uses the MediaLake hook/service. Mock both (same as WordPressConnector.test).
vi.mock('../../services/mediaLakeService', () => ({
  getMediaLakeIntegration: vi.fn().mockResolvedValue({ enabled: false }),
  checkMediaLakeAvailability: vi.fn().mockResolvedValue(false),
  getMediaLakeConnectorUrl: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../hooks/useMediaLakeIntegration', () => ({
  useMediaLakeIntegration: vi.fn().mockReturnValue({
    integration: { enabled: false },
    isAvailable: false,
    isLoading: false,
    error: null,
    openConnectorConfig: vi.fn(),
    refreshIntegration: vi.fn(),
  }),
}));

import ConnectionsPage from '../ConnectionsPage';
import {
  connectionsService,
  WORDPRESS_ADAPTER,
  AdapterCatalog,
  Connection,
  ConnectionMirror,
} from '../../services/connectionsService';

// --- Fixtures --------------------------------------------------------------

const catalogFixture: AdapterCatalog = {
  content: [
    { domain: 'content', adapter: WORDPRESS_ADAPTER, available: true, has_active_connection: true },
  ],
  marketplace: [
    { domain: 'marketplace', adapter: 'aws_marketplace', available: true, has_active_connection: false },
  ],
  telemetry: [
    { domain: 'telemetry', adapter: 'cloudwatch', available: true, has_active_connection: false },
    { domain: 'telemetry', adapter: 'dynamo_analytics', available: true, has_active_connection: false },
  ],
  access: [
    { domain: 'access', adapter: 'aws_waf', available: true, has_active_connection: false },
  ],
};

const marketplaceConnection: Connection = {
  connection_id: 'mp-1',
  domain: 'marketplace',
  adapter: 'aws_marketplace',
  brand: null,
  config: { product_code: 'abc' },
  secret_ref: 'arn:aws:secretsmanager:us-west-2:123:secret:mp',
  active: true,
};

const mirrorFixture: ConnectionMirror = {
  domain: 'marketplace',
  mirror: { product_code: 'demo-code', entitlement_region: 'us-west-2' },
};

/** Default happy-path service responses. */
const primeHappyPath = (connections: Connection[] = [marketplaceConnection]) => {
  vi.mocked(connectionsService.listAdapters).mockResolvedValue(catalogFixture);
  vi.mocked(connectionsService.listConnections).mockResolvedValue(connections);
  vi.mocked(connectionsService.getMirror).mockResolvedValue(mirrorFixture);
  // ContentConnections composition: no active WordPress connections → empty state.
  vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([]);
};

const setDemo = (enabled: boolean) => {
  // Full FeatureFlagContext shape: the page reads `demoDataEnabled`. The
  // composed content surface no longer reads any WordPress build flag (the
  // retired `enableWordPressMockUI` toggle is gone; ContentConnections no longer
  // composes WordPressConnector), so the flags object only carries demo state.
  const flags = {
    enableMockData: enabled,
  } as Record<string, boolean>;
  mockUseFeatureFlags.mockReturnValue({
    flags,
    isDemoMode: enabled,
    isFeatureEnabled: (f: string) => Boolean(flags[f]),
    demoDataEnabled: enabled,
    setDemoData: vi.fn(),
  });
};

// --- Tests -----------------------------------------------------------------

describe('ConnectionsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setDemo(false); // demo OFF is the fail-safe default (#27)
  });

  describe('catalog by domain (Req 6.2)', () => {
    it('renders each domain section with its adapters and available/active flags', async () => {
      primeHappyPath();
      render(<ConnectionsPage />);

      // Every domain section header renders (content/marketplace/telemetry/access).
      // Headers carry a Cloudscape counter suffix, e.g. "Marketplace (1)".
      expect(await screen.findByRole('heading', { name: /Content ingestion/ })).toBeInTheDocument();
      // Anchor to the counter suffix so it doesn't also match the mirror panel
      // header ("Marketplace listing config (read-only)").
      expect(screen.getByRole('heading', { name: /^Marketplace \(1\)/ })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /Telemetry/ })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /Access \/ WAF/ })).toBeInTheDocument();

      // Adapters from every domain appear in the catalog.
      // (aws_marketplace also labels the active connection card, so it appears
      // more than once.) The content domain now renders its adapter catalog
      // card CONSISTENTLY with the other domains, so the `wordpress` adapter
      // surfaces as a catalog card with an Activate control.
      expect(screen.getAllByText('aws_marketplace').length).toBeGreaterThan(0);
      expect(screen.getByText('cloudwatch')).toBeInTheDocument();
      expect(screen.getByText('dynamo_analytics')).toBeInTheDocument();
      expect(screen.getByText('aws_waf')).toBeInTheDocument();

      // Content now has a consistent catalog card for the WordPress adapter:
      // the adapter name, an Available badge, and an Activate control (whose
      // testid follows the shared `activate-${domain}-${adapter}` pattern).
      expect(screen.getByText(WORDPRESS_ADAPTER)).toBeInTheDocument();
      expect(screen.getByTestId(`activate-content-${WORDPRESS_ADAPTER}`)).toBeInTheDocument();

      // available flags render on the catalog cards; non-content adapters have
      // no active connection.
      expect(screen.getAllByText('Available').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Not connected').length).toBeGreaterThan(0);
      // The content wordpress descriptor has an active connection in the
      // fixture, so at least one "Active connection" badge renders.
      expect(screen.getAllByText('Active connection').length).toBeGreaterThan(0);
    });

    it('renders an empty state when the catalog is empty', async () => {
      vi.mocked(connectionsService.listAdapters).mockResolvedValue({});
      vi.mocked(connectionsService.listConnections).mockResolvedValue([]);
      vi.mocked(connectionsService.getMirror).mockResolvedValue(null);
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([]);

      render(<ConnectionsPage />);

      expect(await screen.findByText(/No adapters available/)).toBeInTheDocument();
    });
  });

  describe('CRUD flows (Req 6.3)', () => {
    it('activate → activateConnection with the entered config/secret', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.activateConnection).mockResolvedValue({
        ...marketplaceConnection,
        connection_id: 'new',
      });
      const user = userEvent.setup({ delay: null });
      render(<ConnectionsPage />);

      // Activate from the aws_marketplace catalog card.
      const activateBtn = await screen.findByTestId('activate-marketplace-aws_marketplace');
      await user.click(activateBtn);

      // Fill the secret and save.
      const secretField = within(screen.getByRole('dialog')).getByTestId('field-secret');
      await user.type(secretField.querySelector('input')!, 'super-secret');
      await user.click(screen.getByTestId('save-connection'));

      await waitFor(() => {
        expect(connectionsService.activateConnection).toHaveBeenCalledTimes(1);
      });
      const arg = vi.mocked(connectionsService.activateConnection).mock.calls[0][0];
      expect(arg).toMatchObject({
        domain: 'marketplace',
        adapter: 'aws_marketplace',
        secret: 'super-secret',
        active: true,
      });
    });

    it('edit → updateConnection for the selected connection', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.updateConnection).mockResolvedValue(marketplaceConnection);
      const user = userEvent.setup({ delay: null });
      render(<ConnectionsPage />);

      const editBtn = await screen.findByTestId('edit-mp-1');
      await user.click(editBtn);
      await user.click(screen.getByTestId('save-connection'));

      await waitFor(() => {
        expect(connectionsService.updateConnection).toHaveBeenCalledTimes(1);
      });
      expect(vi.mocked(connectionsService.updateConnection).mock.calls[0][0]).toBe('mp-1');
      // Activate must NOT be used for an edit.
      expect(connectionsService.activateConnection).not.toHaveBeenCalled();
    });

    it('deactivate → deactivateConnection for the selected connection', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.deactivateConnection).mockResolvedValue(undefined);
      const user = userEvent.setup({ delay: null });
      render(<ConnectionsPage />);

      const deactivateBtn = await screen.findByTestId('deactivate-mp-1');
      await user.click(deactivateBtn);

      await waitFor(() => {
        expect(connectionsService.deactivateConnection).toHaveBeenCalledWith('mp-1');
      });
    });

    it('Test connection → testConnection surfaces the ok diagnostic', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.testConnection).mockResolvedValue({
        ok: true,
        message: 'WebACL reachable',
      });
      const user = userEvent.setup({ delay: null });
      render(<ConnectionsPage />);

      const testBtn = await screen.findByTestId('test-mp-1');
      await user.click(testBtn);

      await waitFor(() => {
        expect(connectionsService.testConnection).toHaveBeenCalledWith('mp-1');
      });
      expect(await screen.findByText('WebACL reachable', {}, { timeout: 3000 })).toBeInTheDocument();
    });

    it('Test connection → testConnection surfaces a failing diagnostic', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.testConnection).mockResolvedValue({
        ok: false,
        message: 'auth failed',
      });
      const user = userEvent.setup({ delay: null });
      render(<ConnectionsPage />);

      await user.click(await screen.findByTestId('test-mp-1'));

      expect(await screen.findByText('auth failed', {}, { timeout: 3000 })).toBeInTheDocument();
    });
  });

  describe('WordPress content-domain connection (Req 6.3b)', () => {
    it('composes ContentConnections which is gated by connection state (renders nothing when empty; catalog card is the empty-state affordance)', async () => {
      primeHappyPath();
      // No active WordPress connection → ContentConnections renders NOTHING.
      // The content domain now looks like every other domain: the adapter
      // catalog card conveys the empty / "Activate" state, so there is no
      // bespoke second panel below it.
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([]);

      render(<ConnectionsPage />);

      // The content adapter catalog card (the empty-state affordance) renders.
      expect(await screen.findByTestId(`activate-content-${WORDPRESS_ADAPTER}`)).toBeInTheDocument();
      // No redundant bespoke empty-state panel below the catalog card.
      expect(screen.queryByText(/No content connections active/)).toBeNull();
      // Composition is still driven by connection state (the component fetches).
      expect(connectionsService.getActiveWordPressConnections).toHaveBeenCalled();
    });

    it('renders a COMPACT SUMMARY (status + high-level metrics + Test) when a content connection is active', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([
        {
          connection_id: 'wp-acme',
          domain: 'content',
          adapter: WORDPRESS_ADAPTER,
          brand: 'acme',
          active: true,
          config: { availablePosts: 156, syncedPosts: 23, lastSync: '2025-01-15' },
        },
      ]);

      render(<ConnectionsPage />);

      // Compact summary: status + high-level metrics + a Test connection action,
      // NOT the retired full connector (no post list / sync modal / scan buttons).
      expect(await screen.findByText(/Connected/)).toBeInTheDocument();
      expect(screen.getByText('Available Posts')).toBeInTheDocument();
      expect(screen.getByText('Synced Posts')).toBeInTheDocument();
      expect(screen.getByText('Last Sync')).toBeInTheDocument();
      // Metrics sourced safely from connection.config.
      expect(screen.getByText('156')).toBeInTheDocument();
      expect(screen.getByText('23')).toBeInTheDocument();
      // Brand label surfaced.
      expect(screen.getByText(/Brand: acme/)).toBeInTheDocument();
      // Test connection action present; the retired heavy connector is gone.
      expect(screen.getByTestId('test-content-wp-acme')).toBeInTheDocument();
      expect(screen.queryByText('Recent WordPress Content')).toBeNull();
      expect(screen.queryByText('Scan for New Content')).toBeNull();
    });

    it('metrics show a neutral placeholder (—) when the connection config carries none (mock-data honesty)', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([
        {
          connection_id: 'wp-bare',
          domain: 'content',
          adapter: WORDPRESS_ADAPTER,
          brand: null,
          active: true,
        },
      ]);

      render(<ConnectionsPage />);

      // Neutral placeholder rather than invented data.
      expect(await screen.findByText('Available Posts')).toBeInTheDocument();
      expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    });

    it('Test connection on the compact summary surfaces the ok diagnostic inline', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([
        {
          connection_id: 'wp-acme',
          domain: 'content',
          adapter: WORDPRESS_ADAPTER,
          brand: 'acme',
          active: true,
        },
      ]);
      vi.mocked(connectionsService.testConnection).mockResolvedValue({
        ok: true,
        message: 'WordPress reachable',
      });
      const user = userEvent.setup({ delay: null });

      render(<ConnectionsPage />);

      await user.click(await screen.findByTestId('test-content-wp-acme'));

      await waitFor(() => {
        expect(connectionsService.testConnection).toHaveBeenCalledWith('wp-acme');
      });
      expect(await screen.findByText('WordPress reachable', {}, { timeout: 3000 })).toBeInTheDocument();
    });
  });

  describe('mirror panel is read-only (Req 6.9)', () => {
    it('renders the marketplace mirror panel with no edit/activate affordance inside it', async () => {
      primeHappyPath();
      render(<ConnectionsPage />);

      const panel = await screen.findByTestId('marketplace-mirror-panel');
      expect(panel).toBeInTheDocument();
      // Mirrored config values render read-only.
      expect(within(panel).getByText('demo-code')).toBeInTheDocument();
      // Federation rule: no interactive edit affordance inside the mirror panel.
      expect(within(panel).queryByRole('button')).toBeNull();
      expect(within(panel).queryByRole('textbox')).toBeNull();
    });

    it('omits the mirror panel entirely when no mirror exists', async () => {
      primeHappyPath();
      vi.mocked(connectionsService.getMirror).mockResolvedValue(null);

      render(<ConnectionsPage />);

      // Wait for load to settle (a domain section renders), then assert no panel.
      await screen.findByRole('heading', { name: /^Marketplace/ });
      expect(screen.queryByTestId('marketplace-mirror-panel')).toBeNull();
    });
  });

  describe('Demo_Toggle / mock-data honesty (#27)', () => {
    it('OFF (default): passes allowMock=false through to every read getter', async () => {
      primeHappyPath();
      render(<ConnectionsPage />);

      await waitFor(() => {
        expect(connectionsService.listAdapters).toHaveBeenCalled();
      });
      // allowMock pass-through at the UI level: demoDataEnabled === false.
      expect(connectionsService.listAdapters).toHaveBeenCalledWith(false);
      expect(connectionsService.listConnections).toHaveBeenCalledWith(undefined, false);
      expect(connectionsService.getMirror).toHaveBeenCalledWith('marketplace', false);
    });

    it('ON: passes allowMock=true through to every read getter', async () => {
      setDemo(true);
      primeHappyPath();
      render(<ConnectionsPage />);

      await waitFor(() => {
        expect(connectionsService.listAdapters).toHaveBeenCalled();
      });
      expect(connectionsService.listAdapters).toHaveBeenCalledWith(true);
      expect(connectionsService.listConnections).toHaveBeenCalledWith(undefined, true);
      expect(connectionsService.getMirror).toHaveBeenCalledWith('marketplace', true);
    });

    it('OFF: a real-source failure surfaces an error (no unconditional mock fallback)', async () => {
      // With demo OFF the service rethrows; the page must show an error, not mock data.
      vi.mocked(connectionsService.listAdapters).mockRejectedValue(new Error('backend down'));
      vi.mocked(connectionsService.listConnections).mockRejectedValue(new Error('backend down'));
      vi.mocked(connectionsService.getMirror).mockRejectedValue(new Error('backend down'));
      vi.mocked(connectionsService.getActiveWordPressConnections).mockResolvedValue([]);

      render(<ConnectionsPage />);

      expect(await screen.findByText(/Unable to load connections/)).toBeInTheDocument();
      expect(screen.getByText('backend down')).toBeInTheDocument();
      // No mock catalog leaked in on failure.
      expect(screen.queryByText('aws_marketplace')).toBeNull();
    });
  });
});
