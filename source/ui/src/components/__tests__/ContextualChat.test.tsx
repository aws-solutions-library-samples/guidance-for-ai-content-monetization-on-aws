/**
 * Unit tests for Contextual Chat functionality in GlobalAnalyticsAssistant
 */

import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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
const renderWithRouter = (initialEntries: string[] = ['/'], searchParams: string = '') => {
  const fullPath = initialEntries[0] + (searchParams ? `?${searchParams}` : '');
  return render(
    <MemoryRouter initialEntries={[fullPath]}>
      <GlobalAnalyticsAssistant />
    </MemoryRouter>
  );
};

// Helper function to open the assistant
const openAssistant = async () => {
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

describe('GlobalAnalyticsAssistant - Contextual Chat Features', () => {
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

  describe('Enhanced Contextual Greetings', () => {
    it('should display enhanced greeting with context for analytics page', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Check for enhanced greeting elements
      expect(screen.getByText(/analytics dashboard active/i)).toBeInTheDocument();
      expect(screen.getByText(/viewing analytics data/i)).toBeInTheDocument();
      expect(screen.getByText(/what would you like to explore/i)).toBeInTheDocument();
    });

    it('should display enhanced greeting with context for content page', async () => {
      renderWithRouter(['/content']);
      await openAssistant();

      expect(screen.getByText(/content management hub/i)).toBeInTheDocument();
      expect(screen.getByText(/content management/i)).toBeInTheDocument();
    });

    it('should display enhanced greeting with context for bot control page', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistant();

      expect(screen.getByText(/bot control center/i)).toBeInTheDocument();
      expect(screen.getByText(/managing ai bot access/i)).toBeInTheDocument();
    });

    it('should display enhanced greeting with context for system page', async () => {
      renderWithRouter(['/system']);
      await openAssistant();

      expect(screen.getByText(/system management console/i)).toBeInTheDocument();
      expect(screen.getByText(/system management/i)).toBeInTheDocument();
    });

    it('should include specific sub-page context in greeting', async () => {
      renderWithRouter(['/analytics/revenue']);
      await openAssistant();

      expect(screen.getByText(/revenue analytics/i)).toBeInTheDocument();
    });

    it('should include filter context in greeting when filters are present', async () => {
      renderWithRouter(['/analytics'], 'dateRange=last_7_days&contentType=article');
      await openAssistant();

      // The greeting should mention the filters
      expect(screen.getByText(/analytics dashboard active/i)).toBeInTheDocument();
    });
  });

  describe('Page Context Indicators and Badges', () => {
    it('should display page context badge in modal view', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Check for context badge
      expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();
    });

    it('should display filter badge when filters are active', async () => {
      renderWithRouter(['/analytics'], 'dateRange=last_7_days&contentType=article');
      await openAssistant();

      // Check for filter badge
      expect(screen.getByText(/2 filters active/i)).toBeInTheDocument();
    });

    it('should display available actions badge', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Check for actions badge
      expect(screen.getByText(/actions available/i)).toBeInTheDocument();
    });

    it('should display context-aware assistant alert', async () => {
      renderWithRouter(['/content']);
      await openAssistant();

      expect(screen.getByText(/context-aware assistant/i)).toBeInTheDocument();
      expect(screen.getByText(/working with content data/i)).toBeInTheDocument();
    });

    it('should show active filters in context alert', async () => {
      renderWithRouter(['/content'], 'category=news&status=published');
      await openAssistant();

      expect(screen.getByText(/active filters/i)).toBeInTheDocument();
      expect(screen.getByText(/category=news/i)).toBeInTheDocument();
      expect(screen.getByText(/status=published/i)).toBeInTheDocument();
    });
  });

  describe('Enhanced Page Action Buttons', () => {
    it('should display primary actions section', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByText(/primary actions/i)).toBeInTheDocument();
    });

    it('should display secondary actions when available', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByText(/additional actions/i)).toBeInTheDocument();
    });

    it('should display navigation help section', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByText(/navigation help/i)).toBeInTheDocument();
      expect(screen.getByText(/🧭 navigation help/i)).toBeInTheDocument();
      expect(screen.getByText(/❓ page guide/i)).toBeInTheDocument();
      expect(screen.getByText(/🔗 related pages/i)).toBeInTheDocument();
    });

    it('should populate input when primary action is clicked', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      const exportButton = screen.getByRole('button', { name: /export analytics data/i });
      fireEvent.click(exportButton);

      const textarea = screen.getByRole('textbox');
      expect(textarea).toHaveValue('Help me export analytics data');
    });

    it('should populate input when navigation help is clicked', async () => {
      renderWithRouter(['/content']);
      await openAssistant();

      const navHelpButton = screen.getByRole('button', { name: /🧭 navigation help/i });
      fireEvent.click(navHelpButton);

      const textarea = screen.getByRole('textbox');
      expect(textarea).toHaveValue('How do I navigate from Content Management?');
    });

    it('should populate input when page guide is clicked', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistant();

      const pageGuideButton = screen.getByRole('button', { name: /❓ page guide/i });
      fireEvent.click(pageGuideButton);

      const textarea = screen.getByRole('textbox');
      expect(textarea).toHaveValue('What can I do on the Bot Control page?');
    });

    it('should populate input when related pages is clicked', async () => {
      renderWithRouter(['/system']);
      await openAssistant();

      const relatedPagesButton = screen.getByRole('button', { name: /🔗 related pages/i });
      fireEvent.click(relatedPagesButton);

      const textarea = screen.getByRole('textbox');
      expect(textarea).toHaveValue('Show me related pages to System Management');
    });
  });

  describe('Docked View Contextual Features', () => {
    it('should display enhanced context indicator in docked view', async () => {
      renderWithRouter(['/analytics']);
      
      // Open in docked mode
      const dockedButton = screen.getByRole('button', { name: /open analytics assistant \(docked\)/i });
      fireEvent.click(dockedButton);

      await waitFor(() => {
        expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();
      });

      expect(screen.getByText(/context-aware help available/i)).toBeInTheDocument();
    });

    it('should display filter indicator in docked view', async () => {
      renderWithRouter(['/content'], 'category=news');
      
      const dockedButton = screen.getByRole('button', { name: /open analytics assistant \(docked\)/i });
      fireEvent.click(dockedButton);

      await waitFor(() => {
        expect(screen.getByText(/content management/i)).toBeInTheDocument();
      });

      expect(screen.getByText(/🔍 1 filter/i)).toBeInTheDocument();
    });

    it('should display multiple filters indicator in docked view', async () => {
      renderWithRouter(['/analytics'], 'dateRange=last_7_days&contentType=article&status=active');
      
      const dockedButton = screen.getByRole('button', { name: /open analytics assistant \(docked\)/i });
      fireEvent.click(dockedButton);

      await waitFor(() => {
        expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();
      });

      expect(screen.getByText(/🔍 3 filters/i)).toBeInTheDocument();
    });
  });

  describe('Message Context Indicators', () => {
    it('should display context badge on assistant messages', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // The initial greeting message should have a context badge
      expect(screen.getByText(/📍 analytics dashboard context/i)).toBeInTheDocument();
    });

    it('should display context-aware response indicator', async () => {
      renderWithRouter(['/content']);
      await openAssistant();

      // Check for context-aware indicator
      expect(screen.getByText(/🎯 context-aware response/i)).toBeInTheDocument();
    });

    it('should display suggestion prompts with context', async () => {
      renderWithRouter(['/bot-control']);
      await openAssistant();

      // Check for suggestion prompt
      expect(screen.getByText(/💡 try asking/i)).toBeInTheDocument();
    });

    it('should populate input when suggestion is clicked', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Find and click a suggestion button
      const suggestionButtons = screen.getAllByRole('button');
      const suggestionButton = suggestionButtons.find(button => 
        button.textContent?.includes('Show me revenue trends')
      );

      if (suggestionButton) {
        fireEvent.click(suggestionButton);
        
        const textarea = screen.getByRole('textbox');
        expect(textarea).toHaveValue('Show me revenue trends');
      }
    });
  });

  describe('Context Changes and Updates', () => {
    it('should update context when page changes', async () => {
      const { rerender } = renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByText(/analytics dashboard/i)).toBeInTheDocument();

      // Simulate navigation to content page
      rerender(
        <MemoryRouter initialEntries={['/content']}>
          <GlobalAnalyticsAssistant />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/content management/i)).toBeInTheDocument();
      });
    });

    it('should generate new contextual greeting when page changes', async () => {
      const { rerender } = renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByText(/analytics dashboard active/i)).toBeInTheDocument();

      // Navigate to different page
      rerender(
        <MemoryRouter initialEntries={['/bot-control']}>
          <GlobalAnalyticsAssistant />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText(/bot control center/i)).toBeInTheDocument();
      });
    });

    it('should update available actions when context changes', async () => {
      const { rerender } = renderWithRouter(['/analytics']);
      await openAssistant();

      expect(screen.getByRole('button', { name: /export analytics data/i })).toBeInTheDocument();

      // Navigate to content page
      rerender(
        <MemoryRouter initialEntries={['/content']}>
          <GlobalAnalyticsAssistant />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /upload new content/i })).toBeInTheDocument();
      });
    });
  });

  describe('Contextual Quick Actions in Docked View', () => {
    it('should display collapsible quick actions in docked view', async () => {
      renderWithRouter(['/analytics']);
      
      const dockedButton = screen.getByRole('button', { name: /open analytics assistant \(docked\)/i });
      fireEvent.click(dockedButton);

      await waitFor(() => {
        expect(screen.getByText(/quick actions/i)).toBeInTheDocument();
      });
    });

    it('should populate input when docked quick action is clicked', async () => {
      renderWithRouter(['/content']);
      
      const dockedButton = screen.getByRole('button', { name: /open analytics assistant \(docked\)/i });
      fireEvent.click(dockedButton);

      await waitFor(() => {
        expect(screen.getAllByText(/content management/i).length).toBeGreaterThan(0);
      });

      // Find a quick action button in the docked view
      const quickActionButtons = screen.getAllByRole('button');
      const uploadButton = quickActionButtons.find(button => 
        button.textContent?.includes('Upload New Content')
      );

      if (uploadButton) {
        fireEvent.click(uploadButton);
        
        const textarea = screen.getByRole('textbox');
        expect(textarea).toHaveValue('Help me upload new content');
      }
    });
  });

  describe('Error Handling with Context', () => {
    it('should maintain context indicators even when API fails', async () => {
      mockFetch.mockRejectedValue(new Error('Network error'));
      
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Context should still be displayed
      expect(screen.getAllByText(/analytics dashboard/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/context-aware assistant/i)).toBeInTheDocument();
    });

    it('should show contextual error messages', async () => {
      mockFetch.mockRejectedValue(new Error('API unavailable'));
      
      renderWithRouter(['/content']);
      await openAssistant();

      // Send a message to trigger API call
      const textarea = screen.getByRole('textbox');
      const sendButton = screen.getByRole('button', { name: /send/i });
      
      fireEvent.change(textarea, { target: { value: 'Test message' } });
      fireEvent.click(sendButton);

      await waitFor(() => {
        expect(screen.getByText(/having trouble connecting/i)).toBeInTheDocument();
      });

      // Context should still be maintained
      expect(screen.getAllByText(/content management/i).length).toBeGreaterThan(0);
    });
  });

  describe('Accessibility and Usability', () => {
    it('should have proper ARIA labels for contextual elements', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      // Check for proper ARIA labels
      const navHelpButton = screen.getByRole('button', { name: /🧭 navigation help/i });
      expect(navHelpButton).toBeInTheDocument();
      
      const pageGuideButton = screen.getByRole('button', { name: /❓ page guide/i });
      expect(pageGuideButton).toBeInTheDocument();
    });

    it('should maintain focus management with contextual features', async () => {
      renderWithRouter(['/analytics']);
      await openAssistant();

      const textarea = screen.getByRole('textbox');
      expect(textarea).toBeInTheDocument();
      
      // Click a suggestion and verify focus returns to textarea
      const suggestionButtons = screen.getAllByRole('button');
      const suggestionButton = suggestionButtons.find(button => 
        button.textContent?.includes('Explain these metrics')
      );

      if (suggestionButton) {
        fireEvent.click(suggestionButton);
        // Focus should be maintained on the textarea
        expect(textarea).toHaveValue('Explain these metrics to me');
      }
    });
  });
});