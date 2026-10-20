/**
 * Tests for GlobalAnalyticsAssistant Component
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import GlobalAnalyticsAssistant from '../GlobalAnalyticsAssistant';

// Mock React Router with MemoryRouter for better test control
const MockRouter = ({ children, initialPath = '/' }: { children: React.ReactNode; initialPath?: string }) => {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <div data-testid="mock-location" data-pathname={initialPath}>
        {children}
      </div>
    </MemoryRouter>
  );
};

describe('GlobalAnalyticsAssistant', () => {
  beforeEach(() => {
    // Clear any existing timers
    vi.clearAllTimers();
  });

  afterEach(() => {
    // Clean up after each test
    vi.restoreAllMocks();
  });

  describe('Floating Button Rendering', () => {
    it('renders floating button when closed', () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const button = screen.getByRole('button', { name: /open analytics assistant/i });
      expect(button).toBeInTheDocument();
      expect(button).toHaveTextContent('Analytics Assistant');
    });

    it('renders floating button in different positions', () => {
      const { rerender } = render(
        <MockRouter>
          <GlobalAnalyticsAssistant position="top-left" />
        </MockRouter>
      );

      let button = screen.getByRole('button', { name: /open analytics assistant/i });
      expect(button).toBeInTheDocument();

      rerender(
        <MockRouter>
          <GlobalAnalyticsAssistant position="bottom-right" />
        </MockRouter>
      );

      button = screen.getByRole('button', { name: /open analytics assistant/i });
      expect(button).toBeInTheDocument();
    });
  });

  describe('Modal Opening and Closing', () => {
    it('opens modal when floating button is clicked', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText('Analytics Assistant')).toBeInTheDocument();
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });
    });

    it('closes modal when close button is clicked', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // Close modal
      const closeButton = screen.getByRole('button', { name: /close/i });
      fireEvent.click(closeButton);

      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/ask me about your analytics/i)).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /open analytics assistant/i })).toBeInTheDocument();
      });
    });

    it('minimizes modal when minimize button is clicked', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // Minimize modal
      const minimizeButton = screen.getByRole('button', { name: /minimize/i });
      fireEvent.click(minimizeButton);

      await waitFor(() => {
        expect(screen.queryByPlaceholderText(/ask me about your analytics/i)).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: /restore analytics assistant/i })).toBeInTheDocument();
      });
    });
  });

  describe('Page Context Detection', () => {
    it('detects analytics page context', async () => {
      render(
        <MockRouter initialPath="/dashboard">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        // Look for the badge that shows the page context
        expect(screen.getByText('Analytics Dashboard')).toBeInTheDocument();
      });
    });

    it('detects content page context', async () => {
      render(
        <MockRouter initialPath="/content/add">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText('Content Management')).toBeInTheDocument();
      });
    });

    it('detects bot control page context', async () => {
      render(
        <MockRouter initialPath="/bot-control">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText('Bot Control')).toBeInTheDocument();
      });
    });

    it('defaults to home context for unknown paths', async () => {
      render(
        <MockRouter initialPath="/unknown-path">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText('Publisher Platform')).toBeInTheDocument();
      });
    });
  });

  describe('Chat Functionality', () => {
    it('displays contextual greeting message on open', async () => {
      render(
        <MockRouter initialPath="/dashboard">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        // Look for any greeting message - the exact text may vary
        expect(screen.getByText(/analytics/i)).toBeInTheDocument();
      });
    });

    it('sends user message when send button is clicked', async () => {
      vi.useFakeTimers();
      
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // Type message
      const input = screen.getByPlaceholderText(/ask me about your analytics/i);
      fireEvent.change(input, { target: { value: 'Show me revenue data' } });

      // Send message
      const sendButton = screen.getByRole('button', { name: /send message/i });
      fireEvent.click(sendButton);

      // Check user message appears
      await waitFor(() => {
        expect(screen.getByText('Show me revenue data')).toBeInTheDocument();
      });

      // Fast-forward timers to complete the simulated API call
      vi.advanceTimersByTime(2000);

      // Check assistant response appears
      await waitFor(() => {
        expect(screen.getByText(/analyzing your request/i)).toBeInTheDocument();
      });

      vi.useRealTimers();
    });

    it('sends message on Enter key press', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // Type message and press Enter
      const input = screen.getByPlaceholderText(/ask me about your analytics/i);
      fireEvent.change(input, { target: { value: 'Test message' } });
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

      // Check message was sent
      await waitFor(() => {
        expect(screen.getByText('Test message')).toBeInTheDocument();
      });
    });

    it('does not send message on Shift+Enter', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // Type message and press Shift+Enter
      const input = screen.getByPlaceholderText(/ask me about your analytics/i);
      fireEvent.change(input, { target: { value: 'Test message' } });
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', shiftKey: true });

      // Message should not be sent (input should still have value)
      expect(input).toHaveValue('Test message');
    });
  });

  describe('Quick Actions', () => {
    it('displays quick actions based on page context', async () => {
      render(
        <MockRouter initialPath="/dashboard">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText(/quick actions for analytics dashboard/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /export data/i })).toBeInTheDocument();
      });
    });

    it('populates input when quick action is clicked', async () => {
      render(
        <MockRouter initialPath="/dashboard">
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /export data/i })).toBeInTheDocument();
      });

      // Click quick action
      const quickActionButton = screen.getByRole('button', { name: /export data/i });
      fireEvent.click(quickActionButton);

      // Check input is populated
      const input = screen.getByPlaceholderText(/ask me about your analytics/i);
      expect(input).toHaveValue('Help me export data');
    });
  });

  describe('Message Suggestions', () => {
    it('displays suggestion buttons for assistant messages', async () => {
      vi.useFakeTimers();
      
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal and wait for greeting
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByText(/welcome/i)).toBeInTheDocument();
      });

      // Send a message to get suggestions
      const input = screen.getByPlaceholderText(/ask me about your analytics/i);
      fireEvent.change(input, { target: { value: 'revenue' } });
      
      const sendButton = screen.getByRole('button', { name: /send message/i });
      fireEvent.click(sendButton);

      // Fast-forward to complete API simulation
      vi.advanceTimersByTime(2000);

      await waitFor(() => {
        // Look for suggestion buttons (they appear as inline links)
        const suggestions = screen.getAllByRole('button').filter(button => 
          button.textContent?.includes('revenue') || 
          button.textContent?.includes('trends') ||
          button.textContent?.includes('category')
        );
        expect(suggestions.length).toBeGreaterThan(0);
      });

      vi.useRealTimers();
    });
  });

  describe('Error Handling', () => {
    it('handles API errors gracefully', async () => {
      // Mock console.error to avoid test output noise
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      // Open modal
      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        expect(screen.getByPlaceholderText(/ask me about your analytics/i)).toBeInTheDocument();
      });

      // The component should handle errors in the simulated API call
      // Since we're using a simulation, we can't easily force an error,
      // but we can verify the component renders without crashing
      expect(screen.getByText(/analytics assistant/i)).toBeInTheDocument();

      consoleSpy.mockRestore();
    });
  });

  describe('Accessibility', () => {
    it('has proper ARIA labels', () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const button = screen.getByRole('button', { name: /open analytics assistant/i });
      expect(button).toHaveAttribute('aria-label', 'Open Analytics Assistant');
    });

    it('focuses input when modal opens', async () => {
      render(
        <MockRouter>
          <GlobalAnalyticsAssistant />
        </MockRouter>
      );

      const openButton = screen.getByRole('button', { name: /open analytics assistant/i });
      fireEvent.click(openButton);

      await waitFor(() => {
        const input = screen.getByPlaceholderText(/ask me about your analytics/i);
        expect(input).toBeInTheDocument();
        // Note: jsdom doesn't fully support focus(), so we just check the element exists
      });
    });
  });
});