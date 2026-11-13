/**
 * Bot Control Service
 * 
 * Service for managing bot control rules and retrieving WAF metrics
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

// Types for bot control data
export interface BotRule {
  rule_id: string;
  rule_name: string;
  description: string;
  action: 'block' | 'allow' | 'count' | 'rate_limit';
  priority: number;
  conditions: Record<string, unknown>;
  status: 'active' | 'inactive';
  created_at: string;
  updated_at: string;
  waf_rule_id?: string;
  last_synced?: string;
  sync_status: 'synced' | 'pending' | 'error';
}

export interface BotActivity {
  blocked_requests: number;
  allowed_requests: number;
  total_requests: number;
  block_rate: number;
  time_period: string;
  bot_id?: string;
  datapoints: Array<{
    timestamp: string;
    blocked: number;
    allowed: number;
  }>;
  bot_specific?: {
    bot_id: string;
    blocked_requests: number;
    datapoints: Array<{ timestamp: string; blocked: number; allowed: number }>;
  };
}

export interface BotStats {
  time_period: string;
  rule_summary: {
    total_rules: number;
    active_rules: number;
    inactive_rules: number;
  };
  sync_status: {
    synced_rules: number;
    pending_rules: number;
    error_rules: number;
    sync_health: number;
  };
  traffic_summary: {
    total_requests: number;
    blocked_requests: number;
    allowed_requests: number;
    block_rate: number;
  };
  rule_effectiveness: Array<{
    rule_name: string;
    rule_id: string;
    action: string;
    blocked_requests: number;
    effectiveness_score: number;
  }>;
  waf_health: 'healthy' | 'degraded';
}

export interface CreateBotRuleRequest {
  rule_name: string;
  description: string;
  action: 'block' | 'allow' | 'count' | 'rate_limit';
  priority: number;
  conditions: Record<string, unknown>;
  status?: 'active' | 'inactive';
}

export interface UpdateBotRuleRequest {
  rule_name?: string;
  description?: string;
  action?: 'block' | 'allow' | 'count' | 'rate_limit';
  priority?: number;
  conditions?: Record<string, unknown>;
  status?: 'active' | 'inactive';
}

class BotControlService {
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
   * Get all bot control rules
   */
  async getBotRules(filters?: { status?: string; rule_type?: string }): Promise<BotRule[]> {
    try {
      const headers = await this.getAuthHeaders();
      const queryParams = new URLSearchParams();
      
      if (filters?.status) {
        queryParams.append('status', filters.status);
      }
      if (filters?.rule_type) {
        queryParams.append('rule_type', filters.rule_type);
      }

      const url = `${this.apiBaseUrl}/bot-control/rules${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch bot rules: ${response.statusText}`);
      }

      const data = await response.json();
      return data.rules || [];
    } catch (error) {
      console.error('Error fetching bot rules:', error);
      throw error;
    }
  }

  /**
   * Create a new bot control rule
   */
  async createBotRule(ruleData: CreateBotRuleRequest): Promise<BotRule> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/rules`, {
        method: 'POST',
        headers,
        body: JSON.stringify(ruleData),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error?.message || `Failed to create bot rule: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error creating bot rule:', error);
      throw error;
    }
  }

  /**
   * Update an existing bot control rule
   */
  async updateBotRule(ruleId: string, updates: UpdateBotRuleRequest): Promise<BotRule> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/rules/${ruleId}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(updates),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error?.message || `Failed to update bot rule: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error updating bot rule:', error);
      throw error;
    }
  }

  /**
   * Delete a bot control rule
   */
  async deleteBotRule(ruleId: string): Promise<void> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/rules/${ruleId}`, {
        method: 'DELETE',
        headers,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error?.message || `Failed to delete bot rule: ${response.statusText}`);
      }
    } catch (error) {
      console.error('Error deleting bot rule:', error);
      throw error;
    }
  }

  /**
   * Get bot activity and blocking statistics
   */
  async getBotActivity(filters?: { time_period?: string; bot_id?: string }): Promise<BotActivity> {
    try {
      const headers = await this.getAuthHeaders();
      const queryParams = new URLSearchParams();
      
      if (filters?.time_period) {
        queryParams.append('time_period', filters.time_period);
      }
      if (filters?.bot_id) {
        queryParams.append('bot_id', filters.bot_id);
      }

      const url = `${this.apiBaseUrl}/bot-control/activity${queryParams.toString() ? `?${queryParams.toString()}` : ''}`;
      const response = await fetch(url, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch bot activity: ${response.statusText}`);
      }

      const data = await response.json();
      return data.activity;
    } catch (error) {
      console.error('Error fetching bot activity:', error);
      throw error;
    }
  }

  /**
   * Get overall bot control statistics
   */
  async getBotStats(timePeriod: string = 'last_24_hours'): Promise<BotStats> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/stats?time_period=${timePeriod}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch bot stats: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Error fetching bot stats:', error);
      throw error;
    }
  }

  /**
   * Get WAF metrics for dashboard display
   */
  async getWAFMetrics(timePeriod: string = 'last_24_hours'): Promise<{
    blocked_requests: number;
    allowed_requests: number;
    total_requests: number;
    block_rate: number;
    datapoints: Array<{
      timestamp: string;
      blocked: number;
      allowed: number;
    }>;
  }> {
    try {
      const activity = await this.getBotActivity({ time_period: timePeriod });
      return {
        blocked_requests: activity.blocked_requests,
        allowed_requests: activity.allowed_requests,
        total_requests: activity.total_requests,
        block_rate: activity.block_rate,
        datapoints: activity.datapoints,
      };
    } catch (error) {
      console.error('Error fetching WAF metrics:', error);
      throw error;
    }
  }

  /**
   * Validate rule conditions format
   */
  validateRuleConditions(conditions: Record<string, unknown>): { valid: boolean; errors: string[] } {
    const errors: string[] = [];
    
    // Basic validation for common condition types
    if (!conditions || typeof conditions !== 'object') {
      errors.push('Conditions must be a valid object');
      return { valid: false, errors };
    }

    // Validate user agent conditions
    if (conditions.user_agent) {
      if (typeof conditions.user_agent !== 'string' && !Array.isArray(conditions.user_agent)) {
        errors.push('User agent condition must be a string or array of strings');
      }
    }

    // Validate IP conditions
    if (conditions.ip_address) {
      if (typeof conditions.ip_address !== 'string' && !Array.isArray(conditions.ip_address)) {
        errors.push('IP address condition must be a string or array of strings');
      }
    }

    // Validate country conditions
    if (conditions.country) {
      if (typeof conditions.country !== 'string' && !Array.isArray(conditions.country)) {
        errors.push('Country condition must be a string or array of strings');
      }
    }

    // Validate rate limit conditions
    if (conditions.rate_limit) {
      const rateLimit = conditions.rate_limit;
      if (!rateLimit.requests_per_minute || typeof rateLimit.requests_per_minute !== 'number') {
        errors.push('Rate limit must specify requests_per_minute as a number');
      }
    }

    return { valid: errors.length === 0, errors };
  }

  /**
   * Get predefined rule templates
   */
  getRuleTemplates(): Array<{
    name: string;
    description: string;
    action: 'block' | 'allow' | 'count' | 'rate_limit';
    priority: number;
    conditions: Record<string, unknown>;
  }> {
    return [
      {
        name: 'Block AI Training Bots',
        description: 'Block known AI training bots (GPTBot, ChatGPT, CCBot, Claude-Web, Bard)',
        action: 'block',
        priority: 100,
        conditions: {
          user_agent: ['GPTBot', 'ChatGPT', 'CCBot', 'Claude-Web', 'Bard', 'OpenAI']
        }
      },
      {
        name: 'Allow Search Engines',
        description: 'Allow legitimate search engines (Google, Bing, DuckDuckGo, Baidu, Yandex)',
        action: 'allow',
        priority: 200,
        conditions: {
          user_agent: ['Googlebot', 'Bingbot', 'DuckDuckBot', 'Baiduspider', 'YandexBot']
        }
      },
      {
        name: 'Block Content Scrapers',
        description: 'Block content scraping tools (Scrapy, curl, wget, python-requests)',
        action: 'block',
        priority: 150,
        conditions: {
          user_agent: ['Scrapy', 'curl', 'wget', 'python-requests', 'requests']
        }
      },
      {
        name: 'Rate Limit API Access',
        description: 'Rate limit API endpoints to prevent abuse',
        action: 'rate_limit',
        priority: 300,
        conditions: {
          rate_limit: {
            requests_per_minute: 100
          }
        }
      },
      {
        name: 'Allow Social Media',
        description: 'Allow social media platforms (Facebook, Twitter, LinkedIn, WhatsApp)',
        action: 'allow',
        priority: 250,
        conditions: {
          user_agent: ['facebookexternalhit', 'Twitterbot', 'LinkedInBot', 'WhatsApp']
        }
      }
    ];
  }
}

export const botControlService = new BotControlService();