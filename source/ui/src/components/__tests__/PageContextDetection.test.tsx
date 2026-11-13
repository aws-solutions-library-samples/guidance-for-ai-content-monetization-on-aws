/**
 * Unit tests for Page Context Detection functionality in GlobalAnalyticsAssistant
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import GlobalAnalyticsAssistant from '../GlobalAnalyticsAssistant';

// Mock fetch for API calls
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Mock environment variables
vi.mock('import.meta', () => ({
  env: {
    VITE_API_BASE_URL: 'http://localhost:5000'
  }
}));

// Helper function to render component with router
const renderWithRouter = (initialEntries: string[] = ['/']) => {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <GlobalAnalyticsAssistant />
    </MemoryRouter>
  );
};

// Helper function to open the assistant and check context
const openAssistantAndCheckContext = async () => {
  // Get the primary button (not the docked one)
  const assistantButtons = screen.getAllByRole('button', { name: /open analytics assistant/i });
  const primaryButton = assistantButtons.find(button => 
    button.getAttribute('aria-label') === 'Open Analytics Assistant'
  );
  
  if (!primaryButton) {
    throw new Error('Could not find primary Analytics Assistant button');
  }
  
  primaryButton.click();
  
  await waitFor(() => {
    expect(screen.getByText(/analytics assistant/i)).toBeInTheDocument();
  });
};

describe('GlobalAnalyticsAssistant - Page Context Detection', () => {
  beforeEach(() => {
    mockFetch.mockClear();
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ error: 'API not available' })
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('Home Page Context', () => {
    it('should detect home page context correctly', async () => {
      renderWithRouter(['/']);
      await openAssistantAndCheckContext();

      // Check for home page context indicators
      expect(screen.getByText(/home dashboard/i)).toBeInTheDocument();
      expect(screen.getByText(/welcome/i)).toBeInTheDocument();
    });

    it('should detect home page context for /home route', async () => {
      renderWithRouter(['/home']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/home dashboard/i)).toBeInTheDocument();
    });

    it('should provide correct home page actions', async () => {
      renderWithRouter(['/']);
      await openAssistantAndCheckContext();

      // Check for home page specific actions
      expect(screen.getByText(/navigate to analytics/i)).toBeInTheDocument();
      expect(screen.getByText(/view content library/i)).toBeInTheDocument();
    });
  });

  describe('Analytics Page Context', () => {
    it('should detect analytics dashboard context', async () => {
      renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();
      expect(screen.getByText(/viewing analytics/i)).toBeInTheDocument();
    });

    it('should detect dashboard route as analytics context', async () => {
      renderWithRouter(['/dashboard']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();
    });

    it('should detect revenue analytics sub-page', async () => {
      renderWithRouter(['/analytics/revenue']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/revenue analytics/i)).toBeInTheDocument();
    });

    it('should detect content performance sub-page', async () => {
      renderWithRouter(['/analytics/content-performance']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/content performance/i)).toBeInTheDocument();
    });

    it('should detect bot activity analytics sub-page', async () => {
      renderWithRouter(['/analytics/bot-activity']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/bot activity analytics/i)).toBeInTheDocument();
    });

    it('should provide correct analytics page actions', async () => {
      renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/export analytics data/i)).toBeInTheDocument();
      expect(screen.getByText(/change date range/i)).toBeInTheDocument();
    });
  });

  describe('Content Management Context', () => {
    it('should detect content management context', async () => {
      renderWithRouter(['/content']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/content management/i)).toBeInTheDocument();
      expect(screen.getByText(/content management/i)).toBeInTheDocument();
    });

    it('should detect content upload sub-page', async () => {
      renderWithRouter(['/content/upload']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/content upload/i)).toBeInTheDocument();
    });

    it('should detect content library sub-page', async () => {
      renderWithRouter(['/content/library']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/content library/i)).toBeInTheDocument();
    });

    it('should detect content processing sub-page', async () => {
      renderWithRouter(['/content/processing']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/content processing/i)).toBeInTheDocument();
    });

    it('should provide correct content management actions', async () => {
      renderWithRouter(['/content']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/upload new content/i)).toBeInTheDocument();
      expect(screen.getByText(/edit content metadata/i)).toBeInTheDocument();
    });
  });

  describe('Bot Control Context', () => {
    it('should detect bot control context', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/bot control/i)).toBeInTheDocument();
      expect(screen.getByText(/managing bot access/i)).toBeInTheDocument();
    });

    it('should detect access rules sub-page', async () => {
      renderWithRouter(['/bot-control/access-rules']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/access rules configuration/i).length).toBeGreaterThan(0);
    });

    it('should detect partners sub-page', async () => {
      renderWithRouter(['/bot-control/partners']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/partner management/i).length).toBeGreaterThan(0);
    });

    it('should detect pricing sub-page', async () => {
      renderWithRouter(['/bot-control/pricing']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/pricing configuration/i).length).toBeGreaterThan(0);
    });

    it('should provide correct bot control actions', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/configure access rules/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/review bot activity/i).length).toBeGreaterThan(0);
    });
  });

  describe('System Management Context', () => {
    it('should detect system management context', async () => {
      renderWithRouter(['/system']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/system management/i).length).toBeGreaterThan(0);
    });

    it('should detect system status sub-page', async () => {
      renderWithRouter(['/system/status']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/system status/i).length).toBeGreaterThan(0);
    });

    it('should detect system logs sub-page', async () => {
      renderWithRouter(['/system/logs']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/system logs/i).length).toBeGreaterThan(0);
    });

    it('should detect system settings sub-page', async () => {
      renderWithRouter(['/system/settings']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/system settings/i).length).toBeGreaterThan(0);
    });

    it('should provide correct system management actions', async () => {
      renderWithRouter(['/system']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/check system status/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/view system logs/i).length).toBeGreaterThan(0);
    });
  });

  describe('URL Parameters and Filters Detection', () => {
    it('should extract URL parameters as current filters', async () => {
      renderWithRouter(['/analytics?dateRange=last_7_days&contentType=article']);
      await openAssistantAndCheckContext();

      // The component should detect these filters
      // We can't directly test the internal state, but we can verify the context is working
      expect(screen.getAllByText(/analytics dashboard/i).length).toBeGreaterThan(0);
    });

    it('should handle multiple URL parameters', async () => {
      renderWithRouter(['/content?category=news&status=published&author=test']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/content management/i).length).toBeGreaterThan(0);
    });

    it('should handle empty URL parameters', async () => {
      renderWithRouter(['/bot-control?']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/bot control/i).length).toBeGreaterThan(0);
    });
  });

  describe('Context Changes on Navigation', () => {
    it('should update context when navigating between pages', async () => {
      const { rerender } = renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/analytics dashboard/i).length).toBeGreaterThan(0);

      // Simulate navigation to content page
      rerender(
        <MemoryRouter initialEntries={['/content']}>
          <GlobalAnalyticsAssistant />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getAllByText(/content management/i).length).toBeGreaterThan(0);
      });
    });

    it('should clear messages and generate new greeting on page change', async () => {
      const { rerender } = renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      // Navigate to different page
      rerender(
        <MemoryRouter initialEntries={['/bot-control']}>
          <GlobalAnalyticsAssistant />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getAllByText(/bot control/i).length).toBeGreaterThan(0);
      });
    });
  });

  describe('Fallback Context', () => {
    it('should provide fallback context for unknown routes', async () => {
      renderWithRouter(['/unknown-route']);
      await openAssistantAndCheckContext();

      // Check that the assistant opens and shows some context
      expect(screen.getAllByText(/publisher platform/i).length).toBeGreaterThan(0);
    });

    it('should handle deeply nested unknown routes', async () => {
      renderWithRouter(['/some/deeply/nested/unknown/route']);
      await openAssistantAndCheckContext();

      // Check that the assistant opens and shows some context
      expect(screen.getAllByText(/publisher platform/i).length).toBeGreaterThan(0);
    });
  });

  describe('Page Data Extraction', () => {
    it('should extract page section data for analytics pages', async () => {
      renderWithRouter(['/analytics/revenue']);
      await openAssistantAndCheckContext();

      // The component should have extracted pageSection as 'analytics'
      expect(screen.getAllByText(/revenue analytics/i).length).toBeGreaterThan(0);
    });

    it('should extract page section data for content pages', async () => {
      renderWithRouter(['/content/library']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/content library/i).length).toBeGreaterThan(0);
    });

    it('should extract page section data for bot control pages', async () => {
      renderWithRouter(['/bot-control/partners']);
      await openAssistantAndCheckContext();

      expect(screen.getAllByText(/partner management/i).length).toBeGreaterThan(0);
    });
  });

  describe('Contextual Greetings', () => {
    it('should generate appropriate greeting for analytics page', async () => {
      renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/viewing analytics/i)).toBeInTheDocument();
      expect(screen.getByText(/interpret these metrics/i)).toBeInTheDocument();
    });

    it('should generate appropriate greeting for content page', async () => {
      renderWithRouter(['/content']);
      await openAssistantAndCheckContext();

      // Check for the greeting message specifically
      expect(screen.getByText(/you're in content management/i)).toBeInTheDocument();
      expect(screen.getAllByText(/analyze content performance/i).length).toBeGreaterThan(0);
    });

    it('should generate appropriate greeting for bot control page', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistantAndCheckContext();

      expect(screen.getByText(/managing bot access/i)).toBeInTheDocument();
      expect(screen.getByText(/analyze bot behavior/i)).toBeInTheDocument();
    });

    it('should generate appropriate greeting for system page', async () => {
      renderWithRouter(['/system']);
      await openAssistantAndCheckContext();

      // Check for the greeting message specifically
      expect(screen.getByText(/you're in system management/i)).toBeInTheDocument();
      expect(screen.getByText(/interpret system status/i)).toBeInTheDocument();
    });
  });

  describe('Quick Actions Context Awareness', () => {
    it('should show context-appropriate quick actions for analytics', async () => {
      renderWithRouter(['/analytics']);
      await openAssistantAndCheckContext();

      // Look for analytics-specific quick actions - use getAllByText to handle multiple instances
      expect(screen.getAllByText(/export analytics data/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/change date range/i).length).toBeGreaterThan(0);
    });

    it('should show context-appropriate quick actions for content', async () => {
      renderWithRouter(['/content']);
      await openAssistantAndCheckContext();

      // Look for content-specific quick actions - use getAllByText to handle multiple instances
      expect(screen.getAllByText(/upload new content/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/edit content metadata/i).length).toBeGreaterThan(0);
    });

    it('should show context-appropriate quick actions for bot control', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistantAndCheckContext();

      // Look for bot control-specific quick actions - use getAllByText to handle multiple instances
      expect(screen.getAllByText(/configure access rules/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/review bot activity/i).length).toBeGreaterThan(0);
    });
  });
});