/**
 * Connections Service
 *
 * Thin client for the internal, Cognito-authenticated Connections API
 * (`/connections*`) — the runtime capability that discovers built-in
 * integration adapters by domain and resolves activated Connection instances.
 *
 * SCOPE (task 11.1 — WordPress re-home): this file intentionally implements
 * only the slice needed to surface the WordPress connector as a
 * content-domain Connection driven by connection-activation state (superseding
 * the retired `enableWordPressIntegration` build flag). The full Connections
 * page + service layer (adapter catalog CRUD, test/status/mirror actions) is
 * owned by task 12.1, which extends this service. New methods added by 12.1
 * SHOULD follow the same typed-contract + Demo_Toggle conventions established
 * here — this is the deliberate seam between 11.1 and 12.1.
 *
 * Contract source of truth: `openapi-spec.yaml` (`/connections*`,
 * `x-audience: internal`). Types below mirror the `Connection` /
 * `AdapterDescriptor` schemas.
 *
 * Demo_Toggle / mock-data honesty (#27): read getters take `allowMock = false`.
 * With the default (fail-safe) a real-source failure rethrows and an
 * empty-but-successful response returns an empty array (never a mock
 * substitution). Mock is reachable only when a caller passes
 * `allowMock={demoDataEnabled}` from `useFeatureFlags()`, and is surfaced
 * behind the shared `DemoDataBanner`.
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

/** Integration domains the Connections capability groups adapters by. */
export type ConnectionDomain = 'content' | 'marketplace' | 'telemetry' | 'access';

/** The built-in adapter that surfaces the WordPress content connector. */
export const WORDPRESS_ADAPTER = 'wordpress';

/**
 * A persisted Connection (activated adapter instance). Mirrors the
 * `Connection` schema in `openapi-spec.yaml` — non-secret config + a Secrets
 * Manager reference only; the secret value is never present.
 */
export interface Connection {
  connection_id: string;
  domain: ConnectionDomain | string;
  adapter: string;
  /** Tenant/brand scope; null for single-brand deployments (Req 6.3a). */
  brand: string | null;
  config?: Record<string, unknown>;
  secret_ref?: string;
  active: boolean;
  created_at?: string;
  updated_at?: string;
}

/** Request body to activate a Connection (mirrors `ConnectionCreateRequest`). */
export interface ConnectionCreateRequest {
  domain: ConnectionDomain | string;
  adapter: string;
  brand?: string | null;
  config?: Record<string, unknown>;
  /** Write-only: forwarded to Secrets Manager; never persisted or returned. */
  secret?: string;
  active?: boolean;
}

/**
 * Request body to edit a Connection / rotate its secret (mirrors
 * `ConnectionUpdateRequest`). All fields optional; a supplied `secret` is
 * write-only (forwarded to Secrets Manager, never returned).
 */
export interface ConnectionUpdateRequest {
  domain?: ConnectionDomain | string;
  adapter?: string;
  brand?: string | null;
  config?: Record<string, unknown>;
  /** Write-only: forwarded to Secrets Manager; never persisted or returned. */
  secret?: string;
  active?: boolean;
}

/**
 * A catalog entry describing a built-in adapter (mirrors `AdapterDescriptor`).
 * `available` is always true for built-in adapters; `has_active_connection`
 * reflects whether an active Connection exists in this deployment.
 */
export interface AdapterDescriptor {
  domain: ConnectionDomain | string;
  adapter: string;
  available?: boolean;
  has_active_connection?: boolean;
}

/**
 * The built-in adapter catalog grouped by domain (mirrors
 * `AdapterCatalogResponse.adapters`): a map of domain name → descriptors.
 */
export type AdapterCatalog = Record<string, AdapterDescriptor[]>;

/**
 * Result of an adapter's safe, read-only `test()` check (mirrors
 * `ConnectionTestResponse`). Fail-informative: `ok=false` carries a diagnostic
 * `message` rather than throwing.
 */
export interface TestResult {
  ok: boolean;
  message?: string;
}

/**
 * Ongoing health of an active Connection via the adapter's `status()`
 * (mirrors `ConnectionStatusResponse`).
 */
export interface HealthStatus {
  healthy: boolean;
  detail?: string;
}

/**
 * Read-only mirrored external config for a domain (mirrors
 * `ConnectionMirrorResponse`) — e.g. the AWS Marketplace listing config
 * mirror. Authored externally, surfaced read-only here (federation rule,
 * Req 6.9).
 */
export interface ConnectionMirror {
  domain: ConnectionDomain | string;
  mirror: Record<string, unknown>;
}

/** The four integration domains the catalog groups adapters by (Req 6.1). */
export const CONNECTION_DOMAINS: ConnectionDomain[] = [
  'content',
  'marketplace',
  'telemetry',
  'access',
];

class ConnectionsService {
  private apiBaseUrl: string;

  constructor() {
    this.apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://api.example.com';
  }

  /**
   * Cognito auth headers for the internal Connections API. The API is
   * `x-audience: internal` (Cognito only), so a bearer id-token is required.
   */
  private async getAuthHeaders(): Promise<Record<string, string>> {
    try {
      const user = await getCurrentUser();
      if (user) {
        const session = await fetchAuthSession();
        const token = session.tokens?.idToken?.toString();
        if (token) {
          return {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          };
        }
      }
    } catch (error) {
      console.warn('No authentication available:', error);
    }

    return { 'Content-Type': 'application/json' };
  }

  /**
   * List active Connections, optionally scoped to a single domain.
   *
   * @param domain optional domain filter (content / marketplace / telemetry / access)
   * @param allowMock when `true` (demo mode) a failed real request falls back
   *   to mock data. Defaults to `false` (fail-safe): failures rethrow and an
   *   empty result stays empty (#27).
   */
  async listConnections(domain?: ConnectionDomain | string, allowMock = false): Promise<Connection[]> {
    try {
      const headers = await this.getAuthHeaders();
      const query = domain ? `?domain=${encodeURIComponent(domain)}` : '';
      const response = await fetch(`${this.apiBaseUrl}/connections${query}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch connections: ${response.statusText}`);
      }

      const data = await response.json();
      return (data.connections || []) as Connection[];
    } catch (error) {
      console.error('Error fetching connections:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock data (surfaced behind the banner).
        return this.getMockContentConnections(domain);
      }
      throw error;
    }
  }

  /**
   * Resolve the content-domain Connections. Convenience wrapper over
   * {@link listConnections} used by the content-connection surface.
   */
  async getContentConnections(allowMock = false): Promise<Connection[]> {
    return this.listConnections('content', allowMock);
  }

  /**
   * Resolve the active WordPress content Connections (one per brand, multi-brand
   * per Req 6.3a). Returns only `active` WordPress content adapters.
   */
  async getActiveWordPressConnections(allowMock = false): Promise<Connection[]> {
    const connections = await this.getContentConnections(allowMock);
    return connections.filter(
      (c) => c.adapter === WORDPRESS_ADAPTER && c.active,
    );
  }

  /**
   * Activate a Connection (mirrors `POST /connections`). A supplied `secret`
   * is write-only and forwarded to Secrets Manager by the handler; the
   * response carries only the non-secret config + `secret_ref`.
   */
  async activateConnection(request: ConnectionCreateRequest): Promise<Connection> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(`${this.apiBaseUrl}/connections`, {
      method: 'POST',
      headers,
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      throw new Error(`Failed to activate connection: ${response.statusText}`);
    }

    const data = await response.json();
    return data.connection as Connection;
  }

  /**
   * Deactivate (delete) a Connection (mirrors `DELETE /connections/{id}`).
   */
  async deactivateConnection(connectionId: string): Promise<void> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/connections/${encodeURIComponent(connectionId)}`,
      { method: 'DELETE', headers },
    );

    if (!response.ok) {
      throw new Error(`Failed to deactivate connection: ${response.statusText}`);
    }
  }

  /**
   * List the built-in adapter catalog grouped by domain (mirrors
   * `GET /connections/adapters`). Each descriptor carries `available` (built-in)
   * and `has_active_connection` flags (Req 6.1, 6.2).
   *
   * @param allowMock when `true` (demo mode) a failed real request falls back
   *   to a mock catalog (surfaced behind the banner). Defaults to `false`
   *   (fail-safe): failures rethrow; an empty-but-successful response returns an
   *   empty catalog (#27).
   */
  async listAdapters(allowMock = false): Promise<AdapterCatalog> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/connections/adapters`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch adapter catalog: ${response.statusText}`);
      }

      const data = await response.json();
      return (data.adapters || {}) as AdapterCatalog;
    } catch (error) {
      console.error('Error fetching adapter catalog:', error);
      if (allowMock) {
        // Demo mode only: fall back to a mock catalog (behind the banner).
        return this.getMockAdapterCatalog();
      }
      throw error;
    }
  }

  /**
   * Edit a Connection / rotate its secret (mirrors `PUT /connections/{id}`).
   * A supplied `secret` is write-only and forwarded to Secrets Manager; the
   * response carries only the non-secret config + `secret_ref`.
   */
  async updateConnection(
    connectionId: string,
    request: ConnectionUpdateRequest,
  ): Promise<Connection> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/connections/${encodeURIComponent(connectionId)}`,
      { method: 'PUT', headers, body: JSON.stringify(request) },
    );

    if (!response.ok) {
      throw new Error(`Failed to update connection: ${response.statusText}`);
    }

    const data = await response.json();
    return data.connection as Connection;
  }

  /**
   * Invoke the adapter's safe, read-only `test()` connectivity/auth check for a
   * Connection (mirrors `POST /connections/{id}/test`). The API is
   * fail-informative — a failed check returns `{ok: false, message}` (HTTP 200)
   * rather than an error status — so this surfaces the diagnostic without
   * throwing on a failed-but-reachable check (Req 6.5, 7.3).
   */
  async testConnection(connectionId: string): Promise<TestResult> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/connections/${encodeURIComponent(connectionId)}/test`,
      { method: 'POST', headers },
    );

    if (!response.ok) {
      throw new Error(`Failed to test connection: ${response.statusText}`);
    }

    const data = await response.json();
    return { ok: Boolean(data.ok), message: data.message } as TestResult;
  }

  /**
   * Read the ongoing health of an active Connection via the adapter's
   * `status()` (mirrors `GET /connections/{id}/status`).
   */
  async getConnectionStatus(connectionId: string): Promise<HealthStatus> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(
      `${this.apiBaseUrl}/connections/${encodeURIComponent(connectionId)}/status`,
      { method: 'GET', headers },
    );

    if (!response.ok) {
      throw new Error(`Failed to get connection status: ${response.statusText}`);
    }

    const data = await response.json();
    return { healthy: Boolean(data.healthy), detail: data.detail } as HealthStatus;
  }

  /**
   * Read the read-only mirrored external config for a domain (mirrors
   * `GET /connections/mirrors/{domain}`) — e.g. the AWS Marketplace listing
   * config mirror. Authored externally, surfaced read-only here (Req 6.9).
   *
   * Returns `null` when no mirror exists for the domain (HTTP 404) so the page
   * can simply omit the panel. Other failures rethrow unless `allowMock`.
   *
   * @param allowMock when `true` (demo mode) a failed real request falls back
   *   to a mock mirror (behind the banner). Defaults to `false` (fail-safe).
   */
  async getMirror(
    domain: ConnectionDomain | string,
    allowMock = false,
  ): Promise<ConnectionMirror | null> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(
        `${this.apiBaseUrl}/connections/mirrors/${encodeURIComponent(domain)}`,
        { method: 'GET', headers },
      );

      // No mirror for this domain — a valid, non-error "empty" outcome.
      if (response.status === 404) {
        return null;
      }

      if (!response.ok) {
        throw new Error(`Failed to fetch mirror: ${response.statusText}`);
      }

      const data = await response.json();
      return { domain: data.domain ?? domain, mirror: data.mirror || {} } as ConnectionMirror;
    } catch (error) {
      console.error('Error fetching mirror:', error);
      if (allowMock) {
        return this.getMockMirror(domain);
      }
      throw error;
    }
  }

  /**
   * Mock adapter catalog for demo mode only — the four built-in domains with a
   * representative adapter each. Never returned unless `allowMock` is `true`.
   */
  private getMockAdapterCatalog(): AdapterCatalog {
    return {
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
  }

  /**
   * Mock read-only mirror for demo mode only (the marketplace listing config
   * mirror). Never returned unless `allowMock` is `true`.
   */
  private getMockMirror(domain: ConnectionDomain | string): ConnectionMirror | null {
    if (domain !== 'marketplace') {
      return null;
    }
    return {
      domain: 'marketplace',
      mirror: {
        product_code: 'demo-product-code',
        entitlement_region: 'us-west-2',
        dimensions: ['requests', 'bandwidth'],
      },
    };
  }

  /**
   * Mock content Connections for demo mode only. A single-brand active
   * WordPress content connection — enough to render the re-homed connector in
   * a demo/offline session. Never returned unless `allowMock` is `true`.
   */
  private getMockContentConnections(domain?: ConnectionDomain | string): Connection[] {
    if (domain && domain !== 'content') {
      return [];
    }
    return [
      {
        connection_id: 'wordpress-demo',
        domain: 'content',
        adapter: WORDPRESS_ADAPTER,
        brand: null,
        config: { site_url: 'https://example-publisher.com' },
        secret_ref: 'demo-secret-ref',
        active: true,
        created_at: '2025-01-15T10:30:00Z',
        updated_at: '2025-01-15T10:30:00Z',
      },
    ];
  }
}

export const connectionsService = new ConnectionsService();
