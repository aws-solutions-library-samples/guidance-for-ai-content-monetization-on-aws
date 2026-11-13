/**
 * Partner Service
 * 
 * Service for managing registered partners/bots and their access information
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

// Types for partner (licensee) data — the `Partner` / `PartnerDetail` / `PartnerKey` contract shapes.
export type PricingTier = 'basic' | 'standard' | 'premium' | 'enterprise';
export type PartnerStatus = 'active' | 'suspended' | 'deleted';
export type IdentitySource = 'direct' | 'marketplace';

/** Key-gated partner-plane operations a partner may be limited to (empty = all). */
export const KEY_GATED_OPERATIONS: ReadonlyArray<{ id: string; label: string }> = [
  { id: 'queryContent', label: 'queryContent — POST /query' },
  { id: 'submitLog', label: 'submitLog — POST /log' },
  { id: 'validateLicense', label: 'validateLicense — POST /licenses/validate' },
];

export interface RegisteredPartner {
  partner_id: string;
  partner_name: string;
  partner_type?: string | null;
  contact_name?: string | null;
  contact_email: string;
  pricing_tier: PricingTier;
  status: PartnerStatus;
  allowed_endpoints: string[];
  identity_source: IdentitySource;
  marketplace_type?: string | null;
  marketplace_customer_id?: string | null;
  active_key_count: number;
  created_at: string;
  updated_at?: string | null;
}

export interface KeyUsage {
  period_start: string;
  period_end: string;
  used: number;
  remaining: number;
}

export interface PartnerKey {
  key_id: string;
  status: 'active' | 'disabled';
  usage_plan_id: string;
  created_at?: string;
  disabled_at?: string | null;
  usage?: KeyUsage | null;
}

export interface TierLimits {
  quota_limit: number;
  quota_period: 'DAY' | 'WEEK' | 'MONTH';
  rate_limit: number;
  burst_limit: number;
}

export interface PartnerDetail extends RegisteredPartner {
  keys: PartnerKey[];
  tier_limits?: TierLimits | null;
}

/** Returned by create and rotate: the only time a key value is ever visible. */
export interface PartnerKeyCreated {
  partner: RegisteredPartner;
  key: PartnerKey | null;
  key_value: string | null;
}

export interface CreatePartnerRequest {
  partner_name: string;
  pricing_tier: PricingTier;
  partner_type?: string;
  contact_name?: string;
  contact_email?: string;
  allowed_endpoints?: string[];
  identity_source?: IdentitySource;
  marketplace_type?: string;
  marketplace_customer_id?: string;
}

export interface AccessLogEntry {
  log_id: string;
  partner_id: string;
  partner_name: string;
  timestamp: string;
  endpoint: string;
  request_type: string;
  response_code: number;
  ip_address: string;
  user_agent: string;
  content_accessed?: string;
  price_charged?: number;
  processing_time_ms?: number;
}

export interface PartnerStats {
  total_partners: number;
  active_partners: number;
  suspended_partners: number;
  total_active_keys: number;
}

/** Quota is a property of the pricing tier (usage plan); change `pricing_tier` to change it. */
export interface UpdatePartnerRequest {
  partner_name?: string;
  partner_type?: string;
  contact_name?: string;
  contact_email?: string;
  pricing_tier?: PricingTier;
  allowed_endpoints?: string[];
  status?: 'active' | 'suspended';
}

class PartnerService {
  private apiBaseUrl: string;

  constructor() {
    this.apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://api.example.com';
  }

  /**
   * Get authentication headers for API requests
   */
  private async getAuthHeaders(): Promise<Record<string, string>> {
    try {
      const user = await getCurrentUser();
      if (user) {
        const session = await fetchAuthSession();
        const token = session.tokens?.idToken?.toString();
        if (token) {
          return {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
          };
        }
      }
    } catch (error) {
      console.warn('No authentication available:', error);
    }
    
    return {
      'Content-Type': 'application/json',
    };
  }

  /**
   * Get all registered partners/bots.
   *
   * @param allowMock When `true` (demo mode), a failed real request falls back
   *   to mock partners. Defaults to `false` (fail-safe): failures rethrow (#27).
   */
  async getRegisteredPartners(
    filters?: { status?: string; pricing_tier?: string },
    allowMock = false
  ): Promise<RegisteredPartner[]> {
    try {
      const headers = await this.getAuthHeaders();
      const queryParams = new URLSearchParams();
      
      if (filters?.status) {
        queryParams.append('status', filters.status);
      }
      if (filters?.pricing_tier) {
        queryParams.append('pricing_tier', filters.pricing_tier);
      }

      const url = `${this.apiBaseUrl}/partners${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch registered partners: ${response.statusText}`);
      }

      const data = await response.json();
      
      // The backend returns the contract `Partner` shape; keep deleted partners out of the registry.
      const partners: RegisteredPartner[] = data.partners || [];
      return partners.filter((p) => p.status !== 'deleted').map((p) => ({
        ...p,
        contact_email: p.contact_email || '',
        allowed_endpoints: Array.isArray(p.allowed_endpoints) ? p.allowed_endpoints : [],
        identity_source: p.identity_source || 'direct',
        active_key_count: p.active_key_count ?? 0,
      }));
    } catch (error) {
      console.error('Error fetching registered partners:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock data (surfaced behind the banner).
        return this.getMockRegisteredPartners();
      }
      throw error;
    }
  }

  /**
   * Get partner statistics.
   *
   * @param allowMock When `true` (demo mode), a failure falls back to mock
   *   stats. Defaults to `false` (fail-safe): failures rethrow (#27).
   */
  async getPartnerStats(allowMock = false): Promise<PartnerStats> {
    try {
      const partners = await this.getRegisteredPartners(undefined, allowMock);
      return {
        total_partners: partners.length,
        active_partners: partners.filter((p) => p.status === 'active').length,
        suspended_partners: partners.filter((p) => p.status === 'suspended').length,
        total_active_keys: partners.reduce((sum, p) => sum + (p.active_key_count || 0), 0),
      };
    } catch (error) {
      console.error('Error computing partner stats:', error);
      if (allowMock) {
        return { total_partners: 0, active_partners: 0, suspended_partners: 0, total_active_keys: 0 };
      }
      throw error;
    }
  }

  /**
   * Update partner information
   */
  async updatePartner(partnerId: string, updates: UpdatePartnerRequest): Promise<RegisteredPartner> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/partners/${partnerId}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(updates),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error?.message || `Failed to update partner: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error updating partner:', error);
      throw error;
    }
  }

  /**
   * Get access logs for a specific partner or all partners
   * Uses bot control activity endpoint since dedicated access logs endpoint doesn't exist yet
   */
  async getAccessLogs(filters?: { 
    partner_id?: string; 
    time_period?: string; 
    limit?: number;
    endpoint?: string;
  }, allowMock = false): Promise<AccessLogEntry[]> {
    try {
      const headers = await this.getAuthHeaders();
      const queryParams = new URLSearchParams();
      
      if (filters?.partner_id) {
        queryParams.append('bot_id', filters.partner_id); // Map to bot_id for bot control endpoint
      }
      if (filters?.time_period) {
        queryParams.append('time_period', filters.time_period);
      }

      // Use bot control activity endpoint as it provides similar data
      const url = `${this.apiBaseUrl}/bot-control/activity${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        if (allowMock) {
          console.warn(`Bot control activity endpoint failed: ${response.statusText}, using mock data`);
          return this.getMockAccessLogs();
        }
        throw new Error(`Failed to fetch access logs: ${response.statusText}`);
      }

      const data = await response.json();
      
      // Transform bot activity data to access log format
      const activity = data.activity || {};
      const accessLogs: AccessLogEntry[] = [];
      
      // Create synthetic access log entries from bot activity datapoints
      if (activity.datapoints && Array.isArray(activity.datapoints)) {
        activity.datapoints.forEach((point: { blocked?: number; allowed?: number; timestamp?: string }, index: number) => {
          if (point.blocked > 0) {
            accessLogs.push({
              log_id: `activity_blocked_${index}`,
              partner_id: filters?.partner_id || 'unknown',
              partner_name: 'Bot Activity',
              timestamp: point.timestamp,
              endpoint: '/query',
              request_type: 'POST',
              response_code: 403,
              ip_address: '0.0.0.0',
              user_agent: 'Bot/1.0',
              content_accessed: null,
              price_charged: 0.00,
              processing_time_ms: 50,
            });
          }
          if (point.allowed > 0) {
            accessLogs.push({
              log_id: `activity_allowed_${index}`,
              partner_id: filters?.partner_id || 'unknown',
              partner_name: 'Bot Activity',
              timestamp: point.timestamp,
              endpoint: '/query',
              request_type: 'POST',
              response_code: 200,
              ip_address: '0.0.0.0',
              user_agent: 'Bot/1.0',
              content_accessed: 'content_123',
              price_charged: 0.05,
              processing_time_ms: 150,
            });
          }
        });
      }
      
      // If no activity data: demo mode returns mock; otherwise an honest empty list.
      if (accessLogs.length === 0) {
        return allowMock ? this.getMockAccessLogs() : [];
      }
      
      return accessLogs.slice(0, filters?.limit || 100);
    } catch (error) {
      console.error('Error fetching access logs:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock data (surfaced behind the banner).
        return this.getMockAccessLogs();
      }
      throw error;
    }
  }

  private async request<T>(path: string, method: string, body?: unknown): Promise<T> {
    const headers = await this.getAuthHeaders();
    const response = await fetch(`${this.apiBaseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const err = await response.json();
        message = err.error || err.message || message;
      } catch {
        /* non-JSON error body */
      }
      throw new Error(message || `Request failed (${response.status})`);
    }
    return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
  }

  /** Create a partner; for a `direct` identity the response carries its first key value, once. */
  async createPartner(request: CreatePartnerRequest): Promise<PartnerKeyCreated> {
    return this.request<PartnerKeyCreated>('/partners', 'POST', request);
  }

  /** A partner with its keys, tier limits and current-period usage per active key. */
  async getPartner(partnerId: string): Promise<PartnerDetail> {
    return this.request<PartnerDetail>(`/partners/${encodeURIComponent(partnerId)}`, 'GET');
  }

  /** Soft-delete the partner and remove all its keys. */
  async deletePartner(partnerId: string): Promise<RegisteredPartner> {
    return this.request<RegisteredPartner>(`/partners/${encodeURIComponent(partnerId)}`, 'DELETE');
  }

  /** Provision a new key and disable the previous ones; the new value is shown once. */
  async rotateKey(partnerId: string): Promise<PartnerKeyCreated> {
    return this.request<PartnerKeyCreated>(`/partners/${encodeURIComponent(partnerId)}/keys`, 'POST');
  }

  /** Permanently delete one key (refused while it is the only active key). */
  async retireKey(partnerId: string, keyId: string): Promise<void> {
    await this.request<void>(
      `/partners/${encodeURIComponent(partnerId)}/keys/${encodeURIComponent(keyId)}`,
      'DELETE'
    );
  }

  /**
   * Enable or disable a partner
   */
  async togglePartnerStatus(partnerId: string, enabled: boolean): Promise<void> {
    // Suspending disables every key at the gateway; reactivating re-enables them (Q9).
    const status = enabled ? 'active' : 'suspended';
    await this.updatePartner(partnerId, { status });
  }

  /**
   * Mock registered partners for fallback
   */
  private getMockRegisteredPartners(): RegisteredPartner[] {
    const mk = (
      partner_id: string, partner_name: string, pricing_tier: PricingTier,
      allowed_endpoints: string[], status: PartnerStatus = 'active', created_at = '2024-01-01T00:00:00Z'
    ): RegisteredPartner => ({
      partner_id, partner_name, contact_email: `api@${partner_id}.example`, pricing_tier, status,
      allowed_endpoints, identity_source: 'direct', active_key_count: status === 'active' ? 1 : 0,
      created_at, updated_at: created_at,
    });
    return [
      mk('news_publisher_001', 'Global News Network', 'premium', ['queryContent', 'submitLog']),
      mk('tech_blog_002', 'TechInsights Blog', 'standard', ['queryContent', 'submitLog'], 'active', '2024-01-15T00:00:00Z'),
      mk('research_org_003', 'AI Research Institute', 'enterprise', [], 'active', '2024-02-01T00:00:00Z'),
      mk('startup_004', 'InnovateTech Startup', 'basic', ['queryContent'], 'active', '2024-03-01T00:00:00Z'),
      mk('suspended_005', 'Suspended Partner', 'standard', ['queryContent'], 'suspended', '2024-04-01T00:00:00Z'),
    ];
  }


  /**
   * Mock access logs for fallback
   */
  private getMockAccessLogs(): AccessLogEntry[] {
    return [
      {
        log_id: 'log_001',
        partner_id: 'news_publisher_001',
        partner_name: 'Global News Network',
        timestamp: '2025-01-15T14:30:15Z',
        endpoint: '/query',
        request_type: 'POST',
        response_code: 200,
        ip_address: '192.168.1.100',
        user_agent: 'NewsBot/1.0',
        content_accessed: 'article_12345',
        price_charged: 0.05,
        processing_time_ms: 245,
      },
      {
        log_id: 'log_002',
        partner_id: 'tech_blog_002',
        partner_name: 'TechInsights Blog',
        timestamp: '2025-01-15T14:29:45Z',
        endpoint: '/query',
        request_type: 'POST',
        response_code: 200,
        ip_address: '10.0.1.50',
        user_agent: 'TechBot/2.1',
        content_accessed: 'tech_report_67890',
        price_charged: 0.25,
        processing_time_ms: 180,
      },
      {
        log_id: 'log_003',
        partner_id: 'research_org_003',
        partner_name: 'AI Research Institute',
        timestamp: '2025-01-15T14:28:30Z',
        endpoint: '/bid',
        request_type: 'POST',
        response_code: 200,
        ip_address: '172.16.0.25',
        user_agent: 'ResearchBot/1.5',
        content_accessed: 'research_paper_11111',
        price_charged: 0.50,
        processing_time_ms: 320,
      },
      {
        log_id: 'log_004',
        partner_id: 'startup_004',
        partner_name: 'InnovateTech Startup',
        timestamp: '2025-01-15T14:27:15Z',
        endpoint: '/query',
        request_type: 'POST',
        response_code: 429,
        ip_address: '203.0.113.10',
        user_agent: 'StartupBot/0.9',
        content_accessed: null,
        price_charged: 0.00,
        processing_time_ms: 50,
      },
      {
        log_id: 'log_005',
        partner_id: 'news_publisher_001',
        partner_name: 'Global News Network',
        timestamp: '2025-01-15T14:26:00Z',
        endpoint: '/log',
        request_type: 'POST',
        response_code: 200,
        ip_address: '192.168.1.100',
        user_agent: 'NewsBot/1.0',
        content_accessed: null,
        price_charged: 0.00,
        processing_time_ms: 25,
      },
    ];
  }
}

export const partnerService = new PartnerService();
