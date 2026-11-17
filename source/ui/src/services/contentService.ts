/**
 * Content Service
 * 
 * Service for managing content through the IAB API endpoints
 */

import { getCurrentUser, fetchAuthSession } from 'aws-amplify/auth';

// Types for content management
export interface ContentItem {
  content_id: string;
  title: string;
  content_type: string;
  iab_categories: IABCategory[] | string[]; // Support both standardized objects and legacy strings
  status: 'processing' | 'published' | 'failed' | 'deleting' | 'comparison';
  created_at: string;
  updated_at: string;
  metadata?: {
    description?: string;
    author?: string;
    source_url?: string;
    language?: string;
  };
  processing_info?: {
    stage: string;
    progress: number;
    estimated_completion?: string;
  };
  analytics?: {
    requests: number;
    revenue: number;
    blocked_attempts: number;
    last_accessed?: string;
  };
  // Standardized field names matching OpenAPI spec
  embedding_info?: {
    model: string;
    dimensions: number;
    created_at: string;
    storage_type?: string;
  };
  processing_metrics?: {
    processing_time_ms: number;
    tokens_processed: number;
    images_processed: number;
    embedding_generation_time_ms?: number;
  };
}

// Standardized IAB Category interface matching OpenAPI spec
export interface IABCategory {
  id: number;
  name: string;
  tier_1: string;
  tier_2?: string | null;
  tier_3?: string | null;
  tier_4?: string | null;
  parent_id?: number | null;
}

export interface ProcessingJob {
  job_id: string;
  content_id: string;
  filename: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  stage: string;
  progress: number;
  created_at: string;
  updated_at: string;
  estimated_completion?: string;
  error_message?: string;
}

class ContentService {
  private apiBaseUrl: string;

  constructor() {
    this.apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'https://api.example.com';
  }

  /**
   * Normalize IAB categories to display format
   * Handles both standardized objects and legacy string arrays
   */
  private normalizeIABCategories(categories: IABCategory[] | string[]): string[] {
    if (!categories || categories.length === 0) {
      return [];
    }

    // If it's already an array of strings, return as-is
    if (typeof categories[0] === 'string') {
      return categories as string[];
    }

    // Convert standardized objects to display strings
    return (categories as IABCategory[]).map(cat => cat.name);
  }

  /**
   * Convert display categories back to standardized format for API calls
   */
  private standardizeIABCategories(categories: string[]): IABCategory[] {
    // This is a simplified mapping - in a real implementation,
    // you'd have a complete mapping table or call an API
    const categoryMap: { [key: string]: IABCategory } = {
      'Technology & Computing': {
        id: 126,
        name: 'Technology & Computing',
        tier_1: 'Technology & Computing',
        tier_2: null,
        tier_3: null,
        tier_4: null,
        parent_id: null
      },
      'Medical Health': {
        id: 111,
        name: 'Medical Health',
        tier_1: 'Medical Health',
        tier_2: null,
        tier_3: null,
        tier_4: null,
        parent_id: null
      },
      'Business & Finance': {
        id: 52,
        name: 'Business & Finance',
        tier_1: 'Business & Finance',
        tier_2: null,
        tier_3: null,
        tier_4: null,
        parent_id: null
      }
    };

    return categories.map(catName => 
      categoryMap[catName] || {
        id: 0,
        name: catName,
        tier_1: catName,
        tier_2: null,
        tier_3: null,
        tier_4: null,
        parent_id: null
      }
    );
  }

  /**
   * Normalize content item for UI consumption
   * Handles field name standardization and backward compatibility
   */
  private normalizeContentItem(item: Record<string, unknown>): ContentItem {
    return {
      ...item,
      // Normalize IAB categories for display
      iab_categories: this.normalizeIABCategories(item.iab_categories || []),
      // Ensure embedding_info field is used (not embeddings)
      embedding_info: item.embedding_info || item.embeddings || undefined,
      // Ensure processing_metrics is available
      processing_metrics: item.processing_metrics || {
        processing_time_ms: 0,
        tokens_processed: 0,
        images_processed: 0
      }
    };
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
   * Get all content items.
   *
   * @param allowMock When `true` (demo mode), a failed real request falls back
   *   to mock data. Defaults to `false` (fail-safe): failures rethrow so the
   *   UI can surface a real error instead of fabricating data (#27).
   */
  async getContent(allowMock = false): Promise<ContentItem[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/content`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch content: ${response.statusText}`);
      }

      const data = await response.json();
      const items = data.items || data.content || [];
      
      // Normalize all content items for UI consumption
      return items.map((item: Record<string, unknown>) => this.normalizeContentItem(item));
    } catch (error) {
      console.error('Error fetching content:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock data (surfaced behind the banner).
        return this.getMockContent();
      }
      throw error;
    }
  }

  /**
   * Get a specific content item by ID
   */
  async getContentById(contentId: string): Promise<ContentItem | null> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/content/${contentId}`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        if (response.status === 404) {
          return null;
        }
        throw new Error(`Failed to fetch content: ${response.statusText}`);
      }

      const data = await response.json();
      return this.normalizeContentItem(data);
    } catch (error) {
      console.error('Error fetching content by ID:', error);
      return null;
    }
  }

  /**
   * Create new content
   */
  async createContent(content: Partial<ContentItem>): Promise<ContentItem> {
    try {
      const headers = await this.getAuthHeaders();
      
      // Prepare content for API - convert display categories to standardized format
      const apiContent = {
        ...content,
        iab_categories: Array.isArray(content.iab_categories) && typeof content.iab_categories[0] === 'string'
          ? this.standardizeIABCategories(content.iab_categories as string[])
          : content.iab_categories
      };
      
      const response = await fetch(`${this.apiBaseUrl}/content`, {
        method: 'POST',
        headers,
        body: JSON.stringify(apiContent),
      });

      if (!response.ok) {
        throw new Error(`Failed to create content: ${response.statusText}`);
      }

      const data = await response.json();
      return this.normalizeContentItem(data);
    } catch (error) {
      console.error('Error creating content:', error);
      throw error;
    }
  }

  /**
   * Update existing content
   */
  async updateContent(contentId: string, updates: Partial<ContentItem>): Promise<ContentItem> {
    try {
      const headers = await this.getAuthHeaders();
      
      // Prepare updates for API - convert display categories to standardized format
      const apiUpdates = {
        ...updates,
        iab_categories: Array.isArray(updates.iab_categories) && typeof updates.iab_categories[0] === 'string'
          ? this.standardizeIABCategories(updates.iab_categories as string[])
          : updates.iab_categories
      };
      
      const response = await fetch(`${this.apiBaseUrl}/content/${contentId}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify(apiUpdates),
      });

      if (!response.ok) {
        throw new Error(`Failed to update content: ${response.statusText}`);
      }

      const data = await response.json();
      return this.normalizeContentItem(data);
    } catch (error) {
      console.error('Error updating content:', error);
      throw error;
    }
  }

  /**
   * Delete content
   */
  async deleteContent(contentId: string): Promise<void> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/content/${contentId}`, {
        method: 'DELETE',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to delete content: ${response.statusText}`);
      }
    } catch (error) {
      console.error('Error deleting content:', error);
      throw error;
    }
  }

  /**
   * Get processing status for all jobs.
   *
   * @param allowMock When `true` (demo mode), a failed real request falls back
   *   to mock jobs. Defaults to `false` (fail-safe): failures rethrow (#27).
   */
  async getProcessingStatus(allowMock = false): Promise<ProcessingJob[]> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/processing/status`, {
        method: 'GET',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch processing status: ${response.statusText}`);
      }

      const data = await response.json();
      return data.jobs || [];
    } catch (error) {
      console.error('Error fetching processing status:', error);
      if (allowMock) {
        // Demo mode only: fall back to mock jobs (surfaced behind the banner).
        return this.getMockProcessingJobs();
      }
      throw error;
    }
  }

  /**
   * Reprocess content
   */
  async reprocessContent(contentId: string): Promise<void> {
    try {
      const headers = await this.getAuthHeaders();
      const response = await fetch(`${this.apiBaseUrl}/content/${contentId}/reprocess`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Failed to reprocess content: ${response.statusText}`);
      }
    } catch (error) {
      console.error('Error reprocessing content:', error);
      throw error;
    }
  }

  /**
   * Mock data for fallback when API is not available
   */
  private getMockContent(): ContentItem[] {
    return [
      {
        content_id: 'content-1',
        title: 'AI in Healthcare: Future Trends',
        content_type: 'article',
        iab_categories: ['Technology & Computing', 'Medical Health'],
        status: 'published',
        created_at: '2024-01-15T10:30:00Z',
        updated_at: '2024-01-15T10:30:00Z',
        metadata: {
          description: 'Comprehensive analysis of AI trends in healthcare',
          author: 'Dr. Sarah Johnson',
          language: 'en'
        },
        analytics: {
          requests: 245,
          revenue: 12.25,
          blocked_attempts: 12,
          last_accessed: '2024-01-15T10:30:00Z'
        },
        embedding_info: {
          model: 'amazon.titan-embed-text-v1',
          dimensions: 1536,
          created_at: '2024-01-15T10:30:00Z',
          storage_type: 's3-json'
        },
        processing_metrics: {
          processing_time_ms: 2500,
          tokens_processed: 1250,
          images_processed: 0,
          embedding_generation_time_ms: 800
        }
      },
      {
        content_id: 'content-2',
        title: 'Climate Change Data Analysis',
        content_type: 'report',
        iab_categories: ['Science', 'Environment'],
        status: 'published',
        created_at: '2024-01-15T09:15:00Z',
        updated_at: '2024-01-15T09:15:00Z',
        metadata: {
          description: 'Data-driven analysis of climate change impacts',
          author: 'Environmental Research Team',
          language: 'en'
        },
        analytics: {
          requests: 189,
          revenue: 9.45,
          blocked_attempts: 8,
          last_accessed: '2024-01-15T09:15:00Z'
        },
        embedding_info: {
          model: 'amazon.titan-embed-text-v2',
          dimensions: 1024,
          created_at: '2024-01-15T09:15:00Z',
          storage_type: 'opensearch'
        },
        processing_metrics: {
          processing_time_ms: 3200,
          tokens_processed: 1800,
          images_processed: 2,
          embedding_generation_time_ms: 1200
        }
      },
      {
        content_id: 'content-3',
        title: 'Financial Markets Overview',
        content_type: 'analysis',
        iab_categories: ['Business & Finance'],
        status: 'processing',
        created_at: '2024-01-15T08:00:00Z',
        updated_at: '2024-01-15T08:00:00Z',
        metadata: {
          description: 'Current financial markets analysis',
          author: 'Finance Team',
          language: 'en'
        },
        processing_info: {
          stage: 'IAB Classification',
          progress: 65,
          estimated_completion: '2 minutes'
        },
        analytics: {
          requests: 0,
          revenue: 0,
          blocked_attempts: 0
        },
        processing_metrics: {
          processing_time_ms: 0,
          tokens_processed: 0,
          images_processed: 0
        }
      }
    ];
  }

  /**
   * Mock processing jobs for fallback
   */
  private getMockProcessingJobs(): ProcessingJob[] {
    return [
      {
        job_id: 'job-001',
        content_id: 'content-001',
        filename: 'ai-healthcare-trends.json',
        status: 'processing',
        stage: 'IAB Classification',
        progress: 65,
        created_at: '2024-01-15T14:30:00Z',
        updated_at: '2024-01-15T14:32:00Z',
        estimated_completion: '2 minutes'
      },
      {
        job_id: 'job-002',
        content_id: 'content-002',
        filename: 'sustainable-energy-report.json',
        status: 'processing',
        stage: 'Vector Embedding',
        progress: 90,
        created_at: '2024-01-15T14:25:00Z',
        updated_at: '2024-01-15T14:33:00Z',
        estimated_completion: '30 seconds'
      },
      {
        job_id: 'job-003',
        content_id: 'content-003',
        filename: 'digital-marketing-guide.json',
        status: 'completed',
        stage: 'Available',
        progress: 100,
        created_at: '2024-01-15T14:20:00Z',
        updated_at: '2024-01-15T14:25:00Z'
      },
      {
        job_id: 'job-004',
        content_id: 'content-004',
        filename: 'blockchain-analysis.json',
        status: 'queued',
        stage: 'Waiting',
        progress: 0,
        created_at: '2024-01-15T14:35:00Z',
        updated_at: '2024-01-15T14:35:00Z',
        estimated_completion: '5 minutes'
      },
      {
        job_id: 'job-005',
        content_id: 'content-005',
        filename: 'renewable-energy-trends.json',
        status: 'failed',
        stage: 'Validation Error',
        progress: 25,
        created_at: '2024-01-15T14:10:00Z',
        updated_at: '2024-01-15T14:12:00Z',
        error_message: 'Invalid JSON format in content file'
      }
    ];
  }
}

export const contentService = new ContentService();
