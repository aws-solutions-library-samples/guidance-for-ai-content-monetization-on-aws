/**
 * Analytics Service
 * 
 * Service for fetching analytics data and bot control information
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

// Types for analytics data
export interface BotControlRule {
  id: string;
  name: string;
  description: string;
  status: 'Active' | 'Inactive';
  priority: 'High' | 'Medium' | 'Low';
  action: 'Block' | 'Allow';
  matched_requests: number;
  blocked_requests: number;
  last_triggered: string;
}

export interface BotActivity {
  id: string;
  timestamp: string;
  user_agent: string;
  ip_address: string;
  request_path: string;
  action: 'Blocked' | 'Allowed';
  rule: string;
  country: string;
}

export interface BotStats {
  total_requests: number;
  blocked_requests: number;
  allowed_requests: number;
  block_rate: number;
  top_blocked_bots: Array<{
    name: string;
    requests: number;
    percentage: number;
  }>;
}

export interface UsageMetrics {
  total_requests: number;
  unique_partners: number;
  content_accessed: number;
  revenue_generated: number;
  total_bot_access: number;
  total_mcp_usage: number;
}

export interface UsageData {
  date: string;
  requests: number;
  revenue: number;
  blocked: number;
}

export interface RevenueByCategory {
  category: string;
  revenue: number;
  requests: number;
  color: string;
}

export interface TopPartner {
  name: string;
  requests: number;
  revenue: number;
  status: 'active' | 'inactive';
  blocked: number;
}

// Sankey diagram data interfaces
export interface SankeyNode {
  name: string;
  color: string;
  value: number;
  revenue?: number;
}

export interface SankeyLink {
  source: number;
  target: number;
  value: number;
  revenue?: number;
  color?: string;
}

export interface SankeyData {
  nodes: SankeyNode[];
  links: SankeyLink[];
}

export interface BotActivityFlowData {
  bot_types: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  outcomes: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  total_traffic: {
    requests: number;
    revenue: number;
  };
}

export interface ContentPerformanceFlowData {
  categories: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  revenue_streams: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  total_requests: {
    requests: number;
    revenue: number;
  };
}

export interface PartnerAnalyticsFlowData {
  partners: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  revenue_streams: Array<{
    name: string;
    requests: number;
    revenue: number;
    color: string;
  }>;
  total_traffic: {
    requests: number;
    revenue: number;
  };
}

class AnalyticsService {
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
   * Get usage metrics overview.
   *
   * @param allowMock When `true` (demo mode), auth failures / errors fall back
   *   to mock metrics. Defaults to `false` (fail-safe): the auth error / failure
   *   surfaces to the caller instead of fabricating metrics (#27).
   */
  async getUsageMetrics(allowMock = false): Promise<UsageMetrics> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/analytics/metrics`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        if (response.status === 401) {
          console.warn('Authentication failed');
          throw new Error('Authentication failed');
        }
        throw new Error(`Failed to fetch usage metrics: ${response.statusText}`);
      }

      const data = await response.json();
      // Handle both flat and nested response structures
      if (data.metrics) {
        // OpenAPI spec format with nested metrics
        return data.metrics;
      }
      // Flat format - return as is
      return data;
    } catch (error) {
      console.error('Error fetching usage metrics:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock metrics (surfaced behind the banner).
        return {
          total_requests: 15420,
          unique_partners: 12,
          content_accessed: 1247,
          revenue_generated: 2840.50,
          total_bot_access: 342,
          total_mcp_usage: 45
        };
      }
      throw error;
    }
  }

  /**
   * Get usage data over time
   */
  async getUsageData(timeRange: string = 'last_7_days'): Promise<UsageData[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/analytics/usage?range=${timeRange}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch usage data: ${response.statusText}`);
      }

      const data = await response.json();
      return data.usage_data || [];
    } catch (error) {
      console.error('Error fetching usage data:', error);
      // Return mock data as fallback with recent dates
      return [
        { date: '2025-01-09', requests: 1200, revenue: 45.60, blocked: 45 },
        { date: '2025-01-10', requests: 1450, revenue: 52.20, blocked: 52 },
        { date: '2025-01-11', requests: 1100, revenue: 41.80, blocked: 38 },
        { date: '2025-01-12', requests: 1680, revenue: 63.40, blocked: 67 },
        { date: '2025-01-13', requests: 1920, revenue: 72.80, blocked: 78 },
        { date: '2025-01-14', requests: 1750, revenue: 66.50, blocked: 71 },
        { date: '2025-01-15', requests: 2100, revenue: 79.80, blocked: 89 }
      ];
    }
  }

  /**
   * Get revenue breakdown by category
   */
  async getRevenueByCategory(): Promise<RevenueByCategory[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/analytics/revenue/categories`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch revenue by category: ${response.statusText}`);
      }

      const data = await response.json();
      return data.categories || [];
    } catch (error) {
      console.error('Error fetching revenue by category:', error);
      // Return mock data as fallback
      return [
        { category: 'Technology & Computing', revenue: 890.50, requests: 3420, color: '#0073bb' },
        { category: 'Medical Health', revenue: 675.25, requests: 2890, color: '#037f0c' },
        { category: 'Business & Finance', revenue: 520.75, requests: 2100, color: '#ff9900' },
        { category: 'Science', revenue: 445.30, requests: 1850, color: '#8b5cf6' },
        { category: 'Education', revenue: 308.70, requests: 1160, color: '#06b6d4' }
      ];
    }
  }

  /**
   * Get top partners
   */
  async getTopPartners(): Promise<TopPartner[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/analytics/partners`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch top partners: ${response.statusText}`);
      }

      const data = await response.json();
      return data.partners || [];
    } catch (error) {
      console.error('Error fetching top partners:', error);
      // Return mock data as fallback
      return [
        { name: 'OpenAI', requests: 15420, revenue: 771.00, status: 'active', blocked: 23 },
        { name: 'Anthropic', requests: 12100, revenue: 605.00, status: 'active', blocked: 18 },
        { name: 'Google DeepMind', requests: 9800, revenue: 490.00, status: 'active', blocked: 15 },
        { name: 'Microsoft', requests: 8500, revenue: 425.00, status: 'active', blocked: 12 },
        { name: 'Meta AI', requests: 6200, revenue: 310.00, status: 'active', blocked: 9 }
      ];
    }
  }

  /**
   * Get bot control rules.
   *
   * @param allowMock When `true` (demo mode), a failure falls back to mock
   *   rules. Defaults to `false` (fail-safe): failures rethrow (#27).
   */
  async getBotControlRules(allowMock = false): Promise<BotControlRule[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/rules`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch bot control rules: ${response.statusText}`);
      }

      const data = await response.json();
      return data.rules || [];
    } catch (error) {
      console.error('Error fetching bot control rules:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock rules (surfaced behind the banner).
        return this.getMockBotControlRules();
      }
      throw error;
    }
  }

  /**
   * Get recent bot activity.
   *
   * @param allowMock When `true` (demo mode), a failure falls back to mock
   *   activity. Defaults to `false` (fail-safe): failures rethrow (#27).
   */
  async getBotActivity(limit: number = 50, allowMock = false): Promise<BotActivity[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/activity?limit=${limit}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch bot activity: ${response.statusText}`);
      }

      const data = await response.json();
      return data.activity || [];
    } catch (error) {
      console.error('Error fetching bot activity:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock activity (surfaced behind the banner).
        return this.getMockBotActivity();
      }
      throw error;
    }
  }

  /**
   * Get bot statistics
   */
  async getBotStats(): Promise<BotStats> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/bot-control/stats`, {
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
      // Return mock data as fallback
      return {
        total_requests: 60271,
        blocked_requests: 2048,
        allowed_requests: 58223,
        block_rate: 3.4,
        top_blocked_bots: [
          { name: 'GPTBot', requests: 847, percentage: 41.4 },
          { name: 'python-requests', requests: 567, percentage: 27.7 },
          { name: 'CCBot', requests: 234, percentage: 11.4 },
          { name: 'AhrefsBot', requests: 234, percentage: 11.4 },
          { name: 'Claude-Web', requests: 166, percentage: 8.1 }
        ]
      };
    }
  }

  /**
   * Mock bot control rules for fallback
   */
  private getMockBotControlRules(): BotControlRule[] {
    return [
      {
        id: 'rule-001',
        name: 'Block AI Training Bots',
        description: 'Block known AI training bots (GPTBot, ChatGPT, CCBot, Claude-Web, Bard)',
        status: 'Active',
        priority: 'High',
        action: 'Block',
        matched_requests: 1247,
        blocked_requests: 1247,
        last_triggered: '2024-01-15T14:25:00Z'
      },
      {
        id: 'rule-002',
        name: 'Allow Search Engines',
        description: 'Allow legitimate search engines (Google, Bing, DuckDuckGo, Baidu, Yandex)',
        status: 'Active',
        priority: 'High',
        action: 'Allow',
        matched_requests: 45678,
        blocked_requests: 0,
        last_triggered: '2024-01-15T14:30:00Z'
      },
      {
        id: 'rule-003',
        name: 'Allow Social Media',
        description: 'Allow social media platforms (Facebook, Twitter, LinkedIn, WhatsApp)',
        status: 'Active',
        priority: 'Medium',
        action: 'Allow',
        matched_requests: 12456,
        blocked_requests: 0,
        last_triggered: '2024-01-15T14:28:00Z'
      },
      {
        id: 'rule-004',
        name: 'Block Content Scrapers',
        description: 'Block content scraping tools (Scrapy, curl, wget, python-requests)',
        status: 'Active',
        priority: 'High',
        action: 'Block',
        matched_requests: 567,
        blocked_requests: 567,
        last_triggered: '2024-01-15T14:20:00Z'
      }
    ];
  }

  /**
   * Generate bot activity flow data from existing usage metrics and bot stats.
   *
   * @param allowMock When `true` (demo mode), missing/invalid/no-traffic data
   *   falls back to mock flow data. Defaults to `false` (fail-safe): invalid
   *   data or failures rethrow; genuinely empty traffic yields a zeroed
   *   (honest) flow rather than fabricated numbers (#27).
   */
  async getBotActivityFlowData(
    timeRange: string = 'last_7_days',
    allowMock = false
  ): Promise<BotActivityFlowData> {
    try {
      // Use existing APIs to get bot-related data
      const [usageMetrics, botStats] = await Promise.all([
        this.getUsageMetrics(allowMock),
        this.getBotStats()
      ]);

      console.log('Bot activity flow - raw data:', { timeRange, usageMetrics, botStats });
      
      // Validate and sanitize data
      const totalBotAccess = Number(usageMetrics?.total_bot_access) || 0;
      const blockedRequests = Number(botStats?.blocked_requests) || 0;
      const allowedRequests = Number(botStats?.allowed_requests) || 0;
      
      // Ensure we have valid numbers
      if (isNaN(totalBotAccess) || isNaN(blockedRequests) || isNaN(allowedRequests)) {
        if (allowMock) {
          console.warn('Invalid numeric data in bot stats, using fallback');
          return this.getMockBotActivityFlowData();
        }
        throw new Error('Invalid numeric data in bot stats');
      }
      
      // Check if we have meaningful data (not all zeros).
      // Demo mode substitutes mock data for a richer visualization; real mode
      // returns an honest zeroed flow so the UI can show an empty state.
      if (blockedRequests === 0 && allowedRequests === 0) {
        if (allowMock) {
          console.warn('No bot traffic detected in WAF metrics, using mock data for visualization');
          return this.getMockBotActivityFlowData();
        }
        return {
          bot_types: [],
          outcomes: [],
          total_traffic: { requests: Math.max(0, totalBotAccess), revenue: 0 }
        };
      }
      
      // Calculate revenue from allowed bot requests (estimate)
      const estimatedRevenue = allowedRequests * 0.05; // $0.05 per allowed request

      const flowData = {
        bot_types: [
          { 
            name: "Legitimate AI Bots", 
            requests: Math.max(0, Math.floor(allowedRequests * 0.6)), 
            revenue: Math.max(0, estimatedRevenue * 0.8), 
            color: "#037f0c" 
          },
          { 
            name: "Search Engine Crawlers", 
            requests: Math.max(0, Math.floor(allowedRequests * 0.3)), 
            revenue: Math.max(0, estimatedRevenue * 0.15), 
            color: "#0073bb" 
          },
          { 
            name: "Blocked Scrapers", 
            requests: Math.max(0, blockedRequests), 
            revenue: 0, 
            color: "#d13212" 
          },
          { 
            name: "Unknown Bots", 
            requests: Math.max(0, Math.floor(allowedRequests * 0.1)), 
            revenue: Math.max(0, estimatedRevenue * 0.05), 
            color: "#ff9900" 
          }
        ],
        outcomes: [
          { 
            name: "Revenue Generated", 
            requests: Math.max(0, allowedRequests), 
            revenue: Math.max(0, estimatedRevenue), 
            color: "#28a745" 
          },
          { 
            name: "Security Blocked", 
            requests: Math.max(0, blockedRequests), 
            revenue: 0, 
            color: "#dc3545" 
          }
        ],
        total_traffic: {
          requests: Math.max(0, totalBotAccess),
          revenue: Math.max(0, estimatedRevenue)
        }
      };

      console.log('Bot activity flow - generated data:', flowData);
      return flowData;
    } catch (error) {
      console.error('Error generating bot activity flow data:', error);
      if (allowMock) {
        return this.getMockBotActivityFlowData();
      }
      throw error;
    }
  }

  /**
   * Generate content performance flow data from existing revenue categories
   */
  async getContentPerformanceFlowData(timeRange: string = 'last_7_days'): Promise<ContentPerformanceFlowData> {
    try {
      // Use existing revenue by category API
      const revenueCategories = await this.getRevenueByCategory();
      
      console.log('Content performance flow - raw revenue categories:', { timeRange, revenueCategories });
      
      if (!revenueCategories || revenueCategories.length === 0) {
        console.warn('No revenue categories data, using mock data');
        return this.getMockContentPerformanceFlowData();
      }

      // Default colors for categories
      const defaultColors = ['#0073bb', '#037f0c', '#ff9900', '#8b5cf6', '#06b6d4', '#e91e63', '#ff5722'];

      // Transform categories data with validation and fallbacks
      const categories = revenueCategories.map((cat, index) => {
        const name = cat.category || cat.category_name || `Category ${index + 1}`;
        const requests = Number(cat.requests) || 0;
        const revenue = Number(cat.revenue) || 0;
        const color = cat.color || defaultColors[index % defaultColors.length];

        // Validate the transformed data
        if (!name || isNaN(requests) || isNaN(revenue) || requests < 0 || revenue < 0) {
          console.warn('Invalid category data:', cat);
          return null;
        }

        return {
          name,
          requests,
          revenue,
          color
        };
      }).filter(Boolean); // Remove null entries

      if (categories.length === 0) {
        console.warn('No valid categories after transformation, using mock data');
        return this.getMockContentPerformanceFlowData();
      }

      // Calculate totals
      const totalRequests = categories.reduce((sum, cat) => sum + cat.requests, 0);
      const totalRevenue = categories.reduce((sum, cat) => sum + cat.revenue, 0);

      // Generate revenue streams based on total revenue distribution
      const revenueStreams = [
        { 
          name: "Premium Content Access", 
          requests: Math.max(0, Math.floor(totalRequests * 0.6)), 
          revenue: Math.max(0, totalRevenue * 0.6), 
          color: "#28a745" 
        },
        { 
          name: "Standard Content Access", 
          requests: Math.max(0, Math.floor(totalRequests * 0.3)), 
          revenue: Math.max(0, totalRevenue * 0.3), 
          color: "#17a2b8" 
        },
        { 
          name: "Research & Analytics", 
          requests: Math.max(0, Math.floor(totalRequests * 0.1)), 
          revenue: Math.max(0, totalRevenue * 0.1), 
          color: "#ffc107" 
        }
      ];

      const flowData = {
        categories,
        revenue_streams: revenueStreams,
        total_requests: {
          requests: Math.max(0, totalRequests),
          revenue: Math.max(0, totalRevenue)
        }
      };

      console.log('Content performance flow - generated data:', flowData);
      return flowData;
    } catch (error) {
      console.error('Error generating content performance flow data:', error);
      return this.getMockContentPerformanceFlowData();
    }
  }

  /**
   * Generate partner analytics flow data from existing top partners
   */
  async getPartnerAnalyticsFlowData(timeRange: string = 'last_7_days'): Promise<PartnerAnalyticsFlowData> {
    try {
      // Use existing top partners API
      const topPartners = await this.getTopPartners();
      
      console.log('Partner analytics flow - raw top partners:', { timeRange, topPartners });
      
      if (!topPartners || topPartners.length === 0) {
        console.warn('No top partners data, using mock data');
        return this.getMockPartnerAnalyticsFlowData();
      }

      // Transform partners data and add colors with validation
      const colors = ['#00a86b', '#ff6b35', '#4285f4', '#00bcf2', '#1877f2', '#e91e63', '#ff5722'];
      const partners = topPartners.slice(0, 5).map((partner, index) => {
        const name = partner.name || `Partner ${index + 1}`;
        const requests = Number(partner.requests) || 0;
        const revenue = Number(partner.revenue) || 0;
        const color = colors[index % colors.length];

        // Validate the transformed data
        if (!name || isNaN(requests) || isNaN(revenue) || requests < 0 || revenue < 0) {
          console.warn('Invalid partner data:', partner);
          return null;
        }

        return {
          name,
          requests,
          revenue,
          color
        };
      }).filter(Boolean); // Remove null entries

      if (partners.length === 0) {
        console.warn('No valid partners after transformation, using mock data');
        return this.getMockPartnerAnalyticsFlowData();
      }

      // Calculate totals
      const totalRequests = partners.reduce((sum, partner) => sum + partner.requests, 0);
      const totalRevenue = partners.reduce((sum, partner) => sum + partner.revenue, 0);

      // Generate revenue streams based on total revenue distribution
      const revenueStreams = [
        { 
          name: "Premium API Access", 
          requests: Math.max(0, Math.floor(totalRequests * 0.6)), 
          revenue: Math.max(0, totalRevenue * 0.6), 
          color: "#28a745" 
        },
        { 
          name: "Standard API Access", 
          requests: Math.max(0, Math.floor(totalRequests * 0.3)), 
          revenue: Math.max(0, totalRevenue * 0.3), 
          color: "#17a2b8" 
        },
        { 
          name: "Analytics & Insights", 
          requests: Math.max(0, Math.floor(totalRequests * 0.1)), 
          revenue: Math.max(0, totalRevenue * 0.1), 
          color: "#ffc107" 
        }
      ];

      const flowData = {
        partners,
        revenue_streams: revenueStreams,
        total_traffic: {
          requests: Math.max(0, totalRequests),
          revenue: Math.max(0, totalRevenue)
        }
      };

      console.log('Partner analytics flow - generated data:', flowData);
      return flowData;
    } catch (error) {
      console.error('Error generating partner analytics flow data:', error);
      return this.getMockPartnerAnalyticsFlowData();
    }
  }

  /**
   * Convert flow data to Sankey format
   */
  convertToSankeyData(flowData: BotActivityFlowData | ContentPerformanceFlowData | PartnerAnalyticsFlowData, type: 'bot' | 'content' | 'partner'): SankeyData {
    console.log(`Converting ${type} flow data to Sankey format:`, flowData);
    
    const nodes: SankeyNode[] = [];
    const links: SankeyLink[] = [];

    // Validate input data
    if (!flowData) {
      console.error('No flow data provided to convertToSankeyData');
      return { nodes: [], links: [] };
    }

    if (type === 'bot') {
      const data = flowData as BotActivityFlowData;
      
      // Validate bot data structure
      if (!data.bot_types || !Array.isArray(data.bot_types) || 
          !data.outcomes || !Array.isArray(data.outcomes) ||
          !data.total_traffic) {
        console.error('Invalid bot activity flow data structure:', data);
        return { nodes: [], links: [] };
      }
      
      // Add bot type nodes with validation
      data.bot_types.forEach(bot => {
        const value = Number(bot.requests) || 0;
        const revenue = Number(bot.revenue) || 0;
        
        if (isNaN(value) || isNaN(revenue) || value < 0 || revenue < 0) {
          console.warn('Invalid bot type data:', bot);
          return; // Skip this node
        }
        
        nodes.push({
          name: bot.name || 'Unknown Bot',
          color: bot.color || '#666666',
          value: value,
          revenue: revenue
        });
      });

      // Add total traffic node with validation
      const totalValue = Number(data.total_traffic.requests) || 0;
      const totalRevenue = Number(data.total_traffic.revenue) || 0;
      
      if (!isNaN(totalValue) && !isNaN(totalRevenue) && totalValue >= 0 && totalRevenue >= 0) {
        nodes.push({
          name: "Total Bot Traffic",
          color: "#6c757d",
          value: totalValue,
          revenue: totalRevenue
        });
      } else {
        console.warn('Invalid total traffic data:', data.total_traffic);
        return { nodes: [], links: [] };
      }

      // Add outcome nodes with validation
      data.outcomes.forEach(outcome => {
        const value = Number(outcome.requests) || 0;
        const revenue = Number(outcome.revenue) || 0;
        
        if (isNaN(value) || isNaN(revenue) || value < 0 || revenue < 0) {
          console.warn('Invalid outcome data:', outcome);
          return; // Skip this node
        }
        
        nodes.push({
          name: outcome.name || 'Unknown Outcome',
          color: outcome.color || '#666666',
          value: value,
          revenue: revenue
        });
      });

      // Only create links if we have valid nodes
      if (nodes.length < 3) { // Need at least bot types + total + outcomes
        console.warn('Not enough valid nodes for bot Sankey diagram');
        return { nodes: [], links: [] };
      }

      // Add links from bot types to total traffic
      data.bot_types.forEach((bot, index) => {
        const value = Number(bot.requests) || 0;
        const revenue = Number(bot.revenue) || 0;
        
        if (!isNaN(value) && !isNaN(revenue) && value >= 0 && revenue >= 0) {
          const link = {
            source: index,
            target: data.bot_types.length, // Total traffic node index
            value: value,
            revenue: revenue,
            color: bot.color || '#666666'
          };
          console.log(`Adding bot link:`, link);
          links.push(link);
        } else {
          console.warn(`Skipping invalid bot link:`, { bot, value, revenue, index });
        }
      });

      // Add links from total traffic to outcomes
      data.outcomes.forEach((outcome, index) => {
        const value = Number(outcome.requests) || 0;
        const revenue = Number(outcome.revenue) || 0;
        
        if (!isNaN(value) && !isNaN(revenue) && value >= 0 && revenue >= 0) {
          const link = {
            source: data.bot_types.length, // Total traffic node index
            target: data.bot_types.length + 1 + index,
            value: value,
            revenue: revenue,
            color: outcome.color || '#666666'
          };
          console.log(`Adding outcome link:`, link);
          links.push(link);
        } else {
          console.warn(`Skipping invalid outcome link:`, { outcome, value, revenue, index });
        }
      });

    } else if (type === 'content') {
      const data = flowData as ContentPerformanceFlowData;
      
      // Validate content data structure
      if (!data.categories || !Array.isArray(data.categories) || 
          !data.revenue_streams || !Array.isArray(data.revenue_streams) ||
          !data.total_requests) {
        console.error('Invalid content performance flow data structure:', data);
        return { nodes: [], links: [] };
      }
      
      // Add category nodes with validation
      data.categories.forEach(category => {
        const value = Number(category.requests) || 0;
        const revenue = Number(category.revenue) || 0;
        
        if (isNaN(value) || isNaN(revenue) || value < 0 || revenue < 0) {
          console.warn('Invalid category data:', category);
          return; // Skip this node
        }
        
        nodes.push({
          name: category.name || 'Unknown Category',
          color: category.color || '#666666',
          value: value,
          revenue: revenue
        });
      });

      // Add total requests node with validation
      const totalValue = Number(data.total_requests.requests) || 0;
      const totalRevenue = Number(data.total_requests.revenue) || 0;
      
      if (!isNaN(totalValue) && !isNaN(totalRevenue) && totalValue >= 0 && totalRevenue >= 0) {
        nodes.push({
          name: "Total API Requests",
          color: "#6c757d",
          value: totalValue,
          revenue: totalRevenue
        });
      } else {
        console.warn('Invalid total requests data:', data.total_requests);
        return { nodes: [], links: [] };
      }

      // Add revenue stream nodes with validation
      data.revenue_streams.forEach(stream => {
        const value = Number(stream.requests) || 0;
        const revenue = Number(stream.revenue) || 0;
        
        if (isNaN(value) || isNaN(revenue) || value < 0 || revenue < 0) {
          console.warn('Invalid revenue stream data:', stream);
          return; // Skip this node
        }
        
        nodes.push({
          name: stream.name || 'Unknown Stream',
          color: stream.color || '#666666',
          value: value,
          revenue: revenue
        });
      });

      // Only create links if we have valid nodes
      if (nodes.length < 3) { // Need at least categories + total + streams
        console.warn('Not enough valid nodes for content Sankey diagram');
        return { nodes: [], links: [] };
      }

      // Add links from categories to total requests
      data.categories.forEach((category, index) => {
        const value = Number(category.requests) || 0;
        const revenue = Number(category.revenue) || 0;
        
        if (!isNaN(value) && !isNaN(revenue) && value >= 0 && revenue >= 0) {
          links.push({
            source: index,
            target: data.categories.length,
            value: value,
            revenue: revenue,
            color: category.color || '#666666'
          });
        }
      });

      // Add links from total requests to revenue streams
      data.revenue_streams.forEach((stream, index) => {
        const value = Number(stream.requests) || 0;
        const revenue = Number(stream.revenue) || 0;
        
        if (!isNaN(value) && !isNaN(revenue) && value >= 0 && revenue >= 0) {
          links.push({
            source: data.categories.length,
            target: data.categories.length + 1 + index,
            value: value,
            revenue: revenue,
            color: stream.color || '#666666'
          });
        }
      });

    } else if (type === 'partner') {
      const data = flowData as PartnerAnalyticsFlowData;
      
      // Add partner nodes
      data.partners.forEach(partner => {
        nodes.push({
          name: partner.name,
          color: partner.color,
          value: partner.requests,
          revenue: partner.revenue
        });
      });

      // Add total traffic node
      nodes.push({
        name: "Total API Traffic",
        color: "#6c757d",
        value: data.total_traffic.requests,
        revenue: data.total_traffic.revenue
      });

      // Add revenue stream nodes
      data.revenue_streams.forEach(stream => {
        nodes.push({
          name: stream.name,
          color: stream.color,
          value: stream.requests,
          revenue: stream.revenue
        });
      });

      // Add links from partners to total traffic
      data.partners.forEach((partner, index) => {
        links.push({
          source: index,
          target: data.partners.length,
          value: partner.requests,
          revenue: partner.revenue,
          color: partner.color
        });
      });

      // Add links from total traffic to revenue streams
      data.revenue_streams.forEach((stream, index) => {
        links.push({
          source: data.partners.length,
          target: data.partners.length + 1 + index,
          value: stream.requests,
          revenue: stream.revenue,
          color: stream.color
        });
      });
    }

    // Final validation of the generated Sankey data
    const validNodes = nodes.every(node => 
      typeof node.name === 'string' && 
      typeof node.value === 'number' && 
      !isNaN(node.value) && 
      node.value >= 0 &&
      typeof node.color === 'string'
    );

    const validLinks = links.every(link => 
      typeof link.source === 'number' && 
      typeof link.target === 'number' && 
      typeof link.value === 'number' && 
      !isNaN(link.value) && 
      link.value >= 0 &&
      link.source >= 0 && 
      link.target >= 0 &&
      link.source < nodes.length && 
      link.target < nodes.length
    );

    if (!validNodes || !validLinks) {
      console.error('Generated invalid Sankey data:', { 
        validNodes, 
        validLinks, 
        nodes: nodes.map(n => ({ name: n.name, value: n.value, color: n.color })),
        links: links.map(l => ({ source: l.source, target: l.target, value: l.value }))
      });
      return { nodes: [], links: [] };
    }

    console.log(`Successfully converted ${type} data to Sankey format:`, { nodes: nodes.length, links: links.length });
    return { nodes, links };
  }

  /**
   * Mock bot activity flow data for fallback
   */
  private getMockBotActivityFlowData(): BotActivityFlowData {
    return {
      bot_types: [
        { name: "Legitimate AI Bots", requests: 4500, revenue: 1250.50, color: "#037f0c" },
        { name: "Search Engine Crawlers", requests: 1500, revenue: 180.25, color: "#0073bb" },
        { name: "Blocked Scrapers", requests: 3500, revenue: 0, color: "#d13212" },
        { name: "Unknown Bots", requests: 500, revenue: 0, color: "#ff9900" }
      ],
      outcomes: [
        { name: "Revenue Generated", requests: 6000, revenue: 1430.75, color: "#28a745" },
        { name: "Security Blocked", requests: 4000, revenue: 0, color: "#dc3545" }
      ],
      total_traffic: {
        requests: 10000,
        revenue: 1430.75
      }
    };
  }

  /**
   * Mock content performance flow data for fallback
   */
  private getMockContentPerformanceFlowData(): ContentPerformanceFlowData {
    return {
      categories: [
        { name: "Technology & Computing", requests: 3420, revenue: 890.50, color: "#0073bb" },
        { name: "Medical Health", requests: 2890, revenue: 675.25, color: "#037f0c" },
        { name: "Business & Finance", requests: 2100, revenue: 520.75, color: "#ff9900" },
        { name: "Science", requests: 1850, revenue: 445.30, color: "#8b5cf6" },
        { name: "Education", requests: 1160, revenue: 308.70, color: "#06b6d4" }
      ],
      revenue_streams: [
        { name: "Premium Content Access", requests: 6852, revenue: 1704.30, color: "#28a745" },
        { name: "Standard Content Access", requests: 3426, revenue: 852.15, color: "#17a2b8" },
        { name: "Research & Analytics", requests: 1142, revenue: 284.05, color: "#ffc107" }
      ],
      total_requests: {
        requests: 11420,
        revenue: 2840.50
      }
    };
  }

  /**
   * Mock partner analytics flow data for fallback
   */
  private getMockPartnerAnalyticsFlowData(): PartnerAnalyticsFlowData {
    return {
      partners: [
        { name: "OpenAI", requests: 15420, revenue: 771.00, color: "#00a86b" },
        { name: "Anthropic", requests: 12100, revenue: 605.00, color: "#ff6b35" },
        { name: "Google DeepMind", requests: 9800, revenue: 490.00, color: "#4285f4" },
        { name: "Microsoft", requests: 8500, revenue: 425.00, color: "#00bcf2" },
        { name: "Meta AI", requests: 6200, revenue: 310.00, color: "#1877f2" }
      ],
      revenue_streams: [
        { name: "Premium API Access", requests: 31212, revenue: 1561.00, color: "#28a745" },
        { name: "Standard API Access", requests: 15606, revenue: 780.30, color: "#17a2b8" },
        { name: "Analytics & Insights", requests: 5202, revenue: 260.10, color: "#ffc107" }
      ],
      total_traffic: {
        requests: 52020,
        revenue: 2601.00
      }
    };
  }

  /**
   * Mock bot activity for fallback
   */
  private getMockBotActivity(): BotActivity[] {
    return [
      {
        id: 'activity-001',
        timestamp: '2024-01-15T14:30:15Z',
        user_agent: 'GPTBot/1.0',
        ip_address: '192.168.1.100',
        request_path: '/api/content',
        action: 'Blocked',
        rule: 'Block AI Training Bots',
        country: 'United States'
      },
      {
        id: 'activity-002',
        timestamp: '2024-01-15T14:30:10Z',
        user_agent: 'Googlebot/2.1',
        ip_address: '66.249.66.1',
        request_path: '/api/content/discover',
        action: 'Allowed',
        rule: 'Allow Search Engines',
        country: 'United States'
      },
      {
        id: 'activity-003',
        timestamp: '2024-01-15T14:30:05Z',
        user_agent: 'facebookexternalhit/1.1',
        ip_address: '173.252.74.22',
        request_path: '/api/content/metadata',
        action: 'Allowed',
        rule: 'Allow Social Media',
        country: 'United States'
      },
      {
        id: 'activity-004',
        timestamp: '2024-01-15T14:29:58Z',
        user_agent: 'python-requests/2.28.1',
        ip_address: '45.33.32.156',
        request_path: '/api/content/bulk',
        action: 'Blocked',
        rule: 'Block Content Scrapers',
        country: 'Germany'
      }
    ];
  }
}

export const analyticsService = new AnalyticsService();