/**
 * Pricing Service
 * 
 * Service for managing pricing configuration API calls
 */

import { fetchAuthSession } from 'aws-amplify/auth';

export interface ResolvedCategoryMultiplier {
  multiplier: number;
  category_name: string;
  full_path: string;
  tier_level: number;
}

export interface PricingRules {
  per_access: {
    [contentType: string]: string;
  };
  multipliers?: {
    iab_category?: { [category: string]: string };
    // Server-resolved (SSOT #26): category ID -> { multiplier, category_name, full_path,
    // tier_level }. Populated by GET /pricing/rules so the UI needs no client-side ID->name join.
    iab_category_resolved?: { [category: string]: ResolvedCategoryMultiplier };
    recency?: { [period: string]: string };
    demand?: { [level: string]: string };
  };
  subscription_tiers?: {
    [tier: string]: {
      monthly_fee: string;
      included_queries: number;
      overage_rate: string;
    };
  };
}

export interface CreatePricingRulesRequest {
  pricing_rules: PricingRules;
  effective_date: string;
  change_reason: string;
  activate?: boolean;
  rule_set_id?: string;
}

export interface UpdatePricingRulesRequest {
  pricing_rules: PricingRules;
  effective_date: string;
  change_reason: string;
  rule_set_id?: string;
}

export interface PricingRule {
  rule_set_id: string;
  version_timestamp: string;
  effective_date: string;
  status: 'draft' | 'active' | 'inactive';
  created_by: string;
  created_at: string;
  pricing_rules: PricingRules;
  audit_trail: {
    version: number;
    change_reason: string;
    previous_version?: string;
  };
}

class PricingService {
  private apiBaseUrl: string;

  constructor() {
    this.apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://example123.execute-api.us-west-2.amazonaws.com/v1/';
    if (!this.apiBaseUrl.endsWith('/')) {
      this.apiBaseUrl += '/';
    }
  }

  private async getAuthHeaders(): Promise<HeadersInit> {
    try {
      const session = await fetchAuthSession();
      const token = session.tokens?.idToken?.toString();
      
      return {
        'Content-Type': 'application/json',
        'Authorization': token ? `Bearer ${token}` : '',
      };
    } catch (error) {
      console.error('Failed to get auth headers:', error);
      return {
        'Content-Type': 'application/json',
      };
    }
  }

  async getCurrentRules(ruleSetId: string = 'default-pricing'): Promise<PricingRule | null> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/pricing/rules?rule_set_id=${ruleSetId}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        if (response.status === 404) {
          return null; // No rules found
        }
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      return data.pricing_rule || null;
    } catch (error) {
      console.error('Failed to get current pricing rules:', error);
      throw error;
    }
  }

  async createRules(request: CreatePricingRulesRequest): Promise<PricingRule> {
    try {
      console.log('Creating pricing rules:', request);
      console.log('API URL:', `${this.apiBaseUrl}pricing/rules`);
      
      const headers = await this.getAuthHeaders();
      console.log('Request headers:', headers);
      
      const response = await fetch(`${this.apiBaseUrl}pricing/rules`, {
        method: 'POST',
        headers,
        body: JSON.stringify(request),
      });

      console.log('Response status:', response.status);
      console.log('Response headers:', Object.fromEntries(response.headers.entries()));

      if (!response.ok) {
        const errorText = await response.text();
        console.error('Error response:', errorText);
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }

      const data = await response.json();
      console.log('Success response:', data);
      return data.pricing_rule;
    } catch (error) {
      console.error('Failed to create pricing rules:', error);
      throw error;
    }
  }

  async updateRules(request: UpdatePricingRulesRequest): Promise<PricingRule> {
    try {
      console.log('Updating pricing rules:', request);
      console.log('API URL:', `${this.apiBaseUrl}pricing/rules`);
      
      const headers = await this.getAuthHeaders();
      console.log('Request headers:', headers);
      
      const response = await fetch(`${this.apiBaseUrl}pricing/rules`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(request),
      });

      console.log('Response status:', response.status);
      console.log('Response headers:', Object.fromEntries(response.headers.entries()));

      if (!response.ok) {
        const errorText = await response.text();
        console.error('Error response:', errorText);
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }

      const data = await response.json();
      console.log('Success response:', data);
      return data.pricing_rule;
    } catch (error) {
      console.error('Failed to update pricing rules:', error);
      throw error;
    }
  }

  async getPricingHistory(ruleSetId: string = 'default-pricing', limit: number = 10): Promise<PricingRule[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/pricing/history?rule_set_id=${ruleSetId}&limit=${limit}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      return data.history || [];
    } catch (error) {
      console.error('Failed to get pricing history:', error);
      throw error;
    }
  }

  async calculatePricing(params: {
    content_type: string;
    access_type: string;
    transaction_timestamp?: string;
    partner_id?: string;
    content_id?: string;
    rule_set_id?: string;
  }): Promise<{ success: boolean; message?: string; updated?: number }> {
    try {
      const headers = await this.getAuthHeaders();
      const requestBody = {
        transaction_timestamp: new Date().toISOString(),
        partner_id: 'test_partner',
        content_id: 'test_content',
        rule_set_id: 'default-pricing',
        ...params
      };

      const response = await fetch(`${this.apiBaseUrl}/pricing/calculate`, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error('Failed to calculate pricing:', error);
      throw error;
    }
  }

  async getTaxonomyCategories(): Promise<{ categories: Array<{ value: string; label: string }>, mappings: { [key: string]: string } }> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/taxonomy/categories`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      return {
        categories: data.categories || [],
        mappings: data.mappings || {}
      };
    } catch (error) {
      console.error('Failed to get taxonomy categories:', error);
      // Return fallback data
      return {
        categories: [
          { value: "1", label: "Automotive", full_path: "Automotive", tier_level: 1 },
          { value: "52", label: "Business and Finance", full_path: "Business and Finance", tier_level: 1 },
          { value: "111", label: "Medical Health", full_path: "Medical Health", tier_level: 1 },
          { value: "126", label: "Technology & Computing", full_path: "Technology & Computing", tier_level: 1 },
          { value: "150", label: "Attractions", full_path: "Attractions", tier_level: 1 }
        ],
        mappings: {
          "1": "Automotive",
          "52": "Business and Finance",
          "111": "Medical Health", 
          "126": "Technology & Computing",
          "150": "Attractions"
        }
      };
    }
  }
}

export const pricingService = new PricingService();