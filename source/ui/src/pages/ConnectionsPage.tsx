/**
 * ConnectionsPage (task 12.1 — Connections page + service layer)
 *
 * The full UI-driven Connections surface for the integration-adapters spec
 * (#35, Req 6.2/6.3/6.5/6.9). It:
 *   - lists the built-in adapter catalog BY DOMAIN (content / marketplace /
 *     telemetry / access) with available vs has-active-connection flags
 *     (Req 6.2) via `GET /connections/adapters`;
 *   - lets an admin activate / edit / deactivate a Connection via UI config
 *     (Req 6.3) via `POST` / `PUT` / `DELETE /connections`;
 *   - provides a per-Connection "Test connection" action calling the adapter
 *     `test()` (Req 6.5) via `POST /connections/{id}/test`, surfacing
 *     ok + diagnostic;
 *   - surfaces a READ-ONLY mirror panel for MP-listing config (Req 6.9) via
 *     `GET /connections/mirrors/{domain}` — no edit affordance.
 *
 * Builds on task 11.1: the content-domain section composes the existing
 * `ContentConnections` component (which re-homes the WordPress connector) —
 * this page does NOT duplicate or replace that behavior. All API access goes
 * through the `connectionsService` extended in this task (the deliberate 11.1
 * → 12.1 seam).
 *
 * Demo_Toggle / mock-data honesty (#27, MANDATORY): every read getter is
 * called with `allowMock={demoDataEnabled}` from `useFeatureFlags()`. With the
 * demo toggle OFF (default, fail-safe) a real-source failure surfaces an error
 * and an empty-but-successful catalog renders an empty state — never a silent
 * mock substitution. The shared `DemoDataBanner` (mounted once in the app
 * chrome) is automatic; this page hand-rolls no notice. The Connections page
 * surfaces REAL connection state.
 *
 * NAV PLACEMENT SEAM (#34): this page is wired at `/connections` and linked
 * under System Management as a reasonable default. Where it ultimately sits in
 * the portal IA is a #34 decision — do not overhaul portal navigation here.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Cards,
  Container,
  ContentLayout,
  Form,
  FormField,
  ColumnLayout,
  Header,
  Input,
  Modal,
  SpaceBetween,
  StatusIndicator,
} from '@cloudscape-design/components';

import ContentConnections from '../components/ContentConnections';
import { useFeatureFlags } from '../contexts/FeatureFlagContext';
import {
  AdapterCatalog,
  AdapterDescriptor,
  Connection,
  ConnectionDomain,
  ConnectionMirror,
  CONNECTION_DOMAINS,
  ConnectionCreateRequest,
  connectionsService,
  TestResult,
  WORDPRESS_ADAPTER,
} from '../services/connectionsService';

/** Human-friendly labels for each integration domain. */
const DOMAIN_LABELS: Record<string, string> = {
  content: 'Content ingestion',
  marketplace: 'Marketplace',
  telemetry: 'Telemetry',
  access: 'Access / WAF',
};

/** Form state for the activate/edit Connection modal. */
interface ConnectionFormState {
  domain: ConnectionDomain | string;
  adapter: string;
  brand: string;
  /** Non-secret config entered as JSON text. */
  configJson: string;
  /** Write-only secret value — forwarded to Secrets Manager, never persisted. */
  secret: string;
}

const emptyForm: ConnectionFormState = {
  domain: 'marketplace',
  adapter: '',
  brand: '',
  configJson: '',
  secret: '',
};

const ConnectionsPage: React.FC = () => {
  const { demoDataEnabled } = useFeatureFlags();

  const [catalog, setCatalog] = useState<AdapterCatalog>({});
  const [connections, setConnections] = useState<Connection[]>([]);
  const [marketplaceMirror, setMarketplaceMirror] = useState<ConnectionMirror | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Per-connection test results, keyed by connection_id.
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  // Activate/edit modal state.
  const [modalVisible, setModalVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ConnectionFormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  /**
   * Load the catalog, active connections, and the read-only marketplace mirror.
   * Every read passes `allowMock={demoDataEnabled}` (fail-safe default off).
   */
  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [catalogResult, connectionsResult, mirrorResult] = await Promise.all([
        connectionsService.listAdapters(demoDataEnabled),
        connectionsService.listConnections(undefined, demoDataEnabled),
        connectionsService.getMirror('marketplace', demoDataEnabled),
      ]);
      setCatalog(catalogResult);
      setConnections(connectionsResult);
      setMarketplaceMirror(mirrorResult);
    } catch (err) {
      // Real-source failure surfaces an error — never a silent mock fallback.
      setError(err instanceof Error ? err.message : 'Failed to load connections.');
      setCatalog({});
      setConnections([]);
      setMarketplaceMirror(null);
    } finally {
      setLoading(false);
    }
  }, [demoDataEnabled]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (cancelled) {
        return;
      }
      await loadAll();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAll]);

  /** Active Connections for a given domain. */
  const connectionsByDomain = useCallback(
    (domain: string): Connection[] => connections.filter((c) => c.domain === domain),
    [connections],
  );

  const openActivateModal = (descriptor?: AdapterDescriptor) => {
    setEditingId(null);
    setFormError(null);
    setForm({
      ...emptyForm,
      domain: descriptor?.domain ?? emptyForm.domain,
      adapter: descriptor?.adapter ?? '',
    });
    setModalVisible(true);
  };

  const openEditModal = (connection: Connection) => {
    setEditingId(connection.connection_id);
    setFormError(null);
    setForm({
      domain: connection.domain,
      adapter: connection.adapter,
      brand: connection.brand ?? '',
      configJson: connection.config ? JSON.stringify(connection.config, null, 2) : '',
      secret: '',
    });
    setModalVisible(true);
  };

  const closeModal = () => {
    setModalVisible(false);
    setEditingId(null);
    setForm(emptyForm);
    setFormError(null);
  };

  const handleSave = async () => {
    setFormError(null);

    // Parse the non-secret config JSON (empty is allowed → no config).
    let config: Record<string, unknown> | undefined;
    if (form.configJson.trim()) {
      try {
        config = JSON.parse(form.configJson);
      } catch {
        setFormError('Config must be valid JSON.');
        return;
      }
    }

    if (!form.adapter.trim()) {
      setFormError('Adapter is required.');
      return;
    }

    setSaving(true);
    try {
      const brand = form.brand.trim() ? form.brand.trim() : null;
      const secret = form.secret.trim() ? form.secret.trim() : undefined;

      if (editingId) {
        await connectionsService.updateConnection(editingId, {
          domain: form.domain,
          adapter: form.adapter,
          brand,
          config,
          secret,
        });
      } else {
        const request: ConnectionCreateRequest = {
          domain: form.domain,
          adapter: form.adapter,
          brand,
          config,
          secret,
          active: true,
        };
        await connectionsService.activateConnection(request);
      }
      closeModal();
      await loadAll();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save connection.');
    } finally {
      setSaving(false);
    }
  };

  const handleDeactivate = async (connection: Connection) => {
    setDeletingId(connection.connection_id);
    setError(null);
    try {
      await connectionsService.deactivateConnection(connection.connection_id);
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to deactivate connection.');
    } finally {
      setDeletingId(null);
    }
  };

  const handleTest = async (connection: Connection) => {
    setTestingId(connection.connection_id);
    try {
      const result = await connectionsService.testConnection(connection.connection_id);
      setTestResults((prev) => ({ ...prev, [connection.connection_id]: result }));
    } catch (err) {
      // A hard transport failure (vs. a fail-informative {ok:false}) is shown
      // inline as a failed test result.
      setTestResults((prev) => ({
        ...prev,
        [connection.connection_id]: {
          ok: false,
          message: err instanceof Error ? err.message : 'Test failed.',
        },
      }));
    } finally {
      setTestingId(null);
    }
  };

  const totalAdapters = useMemo(
    () => Object.values(catalog).reduce((sum, list) => sum + list.length, 0),
    [catalog],
  );

  if (loading) {
    return (
      <ContentLayout>
        <Container>
          <Box textAlign="center" padding="xl">
            <StatusIndicator type="loading">Loading connections...</StatusIndicator>
          </Box>
        </Container>
      </ContentLayout>
    );
  }

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          description="Discover built-in integration adapters by domain and activate, configure, and test real connections."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={loadAll} loading={loading}>
                Refresh
              </Button>
            </SpaceBetween>
          }
        >
          Connections
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert type="error" statusIconAriaLabel="Error" header="Unable to load connections">
            {error}
          </Alert>
        )}

        {!error && totalAdapters === 0 && (
          <Alert statusIconAriaLabel="Info" header="No adapters available">
            No integration adapters are available in this deployment.
          </Alert>
        )}

        {/* One section per domain, showing the adapter catalog + active connections. */}
        {CONNECTION_DOMAINS.map((domain) => {
          const descriptors = catalog[domain] ?? [];
          const domainConnections = connectionsByDomain(domain);
          return (
            <Container
              key={domain}
              header={
                <Header variant="h2" counter={`(${descriptors.length})`}>
                  {DOMAIN_LABELS[domain] ?? domain}
                </Header>
              }
            >
              <SpaceBetween size="m">
                {/* Adapter catalog for this domain (available / active flags).
                    Rendered for EVERY domain including content, so the content
                    section shows the WordPress adapter card (Available /
                    connection state / Activate) consistently with the other
                    domains. */}
                <Cards<AdapterDescriptor>
                  cardDefinition={{
                    header: (item) => (
                      <SpaceBetween direction="horizontal" size="xs">
                        <Box variant="strong">{item.adapter}</Box>
                        {item.available && <Badge color="blue">Available</Badge>}
                        {item.has_active_connection ? (
                          <Badge color="green">Active connection</Badge>
                        ) : (
                          <Badge color="grey">Not connected</Badge>
                        )}
                      </SpaceBetween>
                    ),
                    sections: [
                      {
                        id: 'actions',
                        content: (item) => (
                          <Button
                            data-testid={`activate-${item.domain}-${item.adapter}`}
                            onClick={() => openActivateModal(item)}
                          >
                            Activate
                          </Button>
                        ),
                      },
                    ],
                  }}
                  cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }]}
                  items={descriptors}
                  trackBy={(item) => `${item.domain}:${item.adapter}`}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No adapters</b>
                      <Box variant="p" color="inherit">
                        No built-in adapters for this domain.
                      </Box>
                    </Box>
                  }
                />

                {/* Active-connection view for this domain. Content uses the
                    bespoke compact ContentConnections summary (status +
                    high-level metrics + Test); other domains use the generic
                    active-connection cards below. */}
                {domain === 'content' ? (
                  <ContentConnections />
                ) : (
                  domainConnections.length > 0 && (
                  <Cards<Connection>
                    cardDefinition={{
                      header: (item) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Box variant="strong">
                            {item.adapter}
                            {item.brand ? ` (${item.brand})` : ''}
                          </Box>
                          <StatusIndicator type={item.active ? 'success' : 'stopped'}>
                            {item.active ? 'active' : 'inactive'}
                          </StatusIndicator>
                        </SpaceBetween>
                      ),
                      sections: [
                        {
                          id: 'ref',
                          content: (item) => (
                            <Box variant="small" color="text-body-secondary">
                              {item.secret_ref
                                ? `Secret: ${item.secret_ref}`
                                : 'No secret reference'}
                            </Box>
                          ),
                        },
                        {
                          id: 'test-result',
                          content: (item) => {
                            const result = testResults[item.connection_id];
                            if (!result) {
                              return null;
                            }
                            return (
                              <StatusIndicator type={result.ok ? 'success' : 'error'}>
                                {result.message ?? (result.ok ? 'Connection OK' : 'Connection failed')}
                              </StatusIndicator>
                            );
                          },
                        },
                        {
                          id: 'actions',
                          content: (item) => (
                            <SpaceBetween direction="horizontal" size="xs">
                              <Button
                                data-testid={`test-${item.connection_id}`}
                                onClick={() => handleTest(item)}
                                loading={testingId === item.connection_id}
                              >
                                Test connection
                              </Button>
                              <Button
                                data-testid={`edit-${item.connection_id}`}
                                onClick={() => openEditModal(item)}
                              >
                                Edit
                              </Button>
                              <Button
                                data-testid={`deactivate-${item.connection_id}`}
                                onClick={() => handleDeactivate(item)}
                                loading={deletingId === item.connection_id}
                              >
                                Deactivate
                              </Button>
                            </SpaceBetween>
                          ),
                        },
                      ],
                    }}
                    cardsPerRow={[{ cards: 1 }]}
                    items={domainConnections}
                    trackBy={(item) => item.connection_id}
                  />
                  )
                )}
              </SpaceBetween>
            </Container>
          );
        })}

        {/* Read-only mirror panel for MP-listing config (Req 6.9) — federation
            rule: authored on the marketplace side, NO edit affordance here. */}
        {marketplaceMirror && (
          <Container
            data-testid="marketplace-mirror-panel"
            header={
              <Header
                variant="h2"
                description="Authored on the marketplace side and surfaced read-only here (federation rule). Not editable."
              >
                Marketplace listing config (read-only)
              </Header>
            }
          >
            <ColumnLayout columns={2} variant="text-grid">
              {Object.entries(marketplaceMirror.mirror).map(([label, value]) => (
                <div key={label}>
                  <Box variant="awsui-key-label">{label}</Box>
                  <Box>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</Box>
                </div>
              ))}
            </ColumnLayout>
          </Container>
        )}
      </SpaceBetween>

      {/* Activate / edit modal. */}
      <Modal
        visible={modalVisible}
        onDismiss={closeModal}
        header={editingId ? 'Edit connection' : 'Activate connection'}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={closeModal}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleSave}
                loading={saving}
                data-testid="save-connection"
              >
                {editingId ? 'Save changes' : 'Activate'}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="m">
            {formError && (
              <Alert type="error" statusIconAriaLabel="Error">
                {formError}
              </Alert>
            )}

            <FormField label="Domain">
              <Input
                value={form.domain as string}
                onChange={({ detail }) => setForm((f) => ({ ...f, domain: detail.value }))}
                disabled={Boolean(editingId)}
                data-testid="field-domain"
              />
            </FormField>

            <FormField label="Adapter">
              <Input
                value={form.adapter}
                onChange={({ detail }) => setForm((f) => ({ ...f, adapter: detail.value }))}
                disabled={Boolean(editingId)}
                placeholder={WORDPRESS_ADAPTER}
                data-testid="field-adapter"
              />
            </FormField>

            <FormField label="Brand" description="Optional tenant/brand scope; leave blank for single-brand.">
              <Input
                value={form.brand}
                onChange={({ detail }) => setForm((f) => ({ ...f, brand: detail.value }))}
                data-testid="field-brand"
              />
            </FormField>

            <FormField label="Config (JSON)" description="Non-secret configuration (endpoints/region/product-code).">
              <Input
                value={form.configJson}
                onChange={({ detail }) => setForm((f) => ({ ...f, configJson: detail.value }))}
                placeholder='{"site_url": "https://example.com"}'
                data-testid="field-config"
              />
            </FormField>

            <FormField
              label="Secret"
              description="Write-only. Forwarded to AWS Secrets Manager; never persisted or returned."
            >
              <Input
                type="password"
                value={form.secret}
                onChange={({ detail }) => setForm((f) => ({ ...f, secret: detail.value }))}
                data-testid="field-secret"
              />
            </FormField>
          </SpaceBetween>
        </Form>
      </Modal>
    </ContentLayout>
  );
};

export default ConnectionsPage;
