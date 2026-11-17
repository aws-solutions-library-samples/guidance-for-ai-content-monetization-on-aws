/**
 * Unit tests for message rendering with metadata in GlobalAnalyticsAssistant
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import '@testing-library/jest-dom';
import GlobalAnalyticsAssistant from '../GlobalAnalyticsAssistant';

// Mock CloudScape components
vi.mock('@cloudscape-design/components', () => ({
  Button: ({ children, onClick, variant, iconName, ariaLabel, ...props }: React.PropsWithChildren<Record<string, unknown>>) => (
    <button 
      onClick={onClick} 
      data-testid={`button-${variant || 'default'}`}
      aria-label={ariaLabel}
      {...props}
    >
      {iconName && <span data-testid={`icon-${iconName}`}>{iconName}</span>}
      {children}
    </button>
  ),
  Modal: ({ children, visible, onDismiss, header }: React.PropsWithChildren<Record<string, unknown>>) => 
    visible ? (
      <div data-testid="modal" role="dialog">
        {header}
        {children}
        <button onClick={onDismiss} data-testid="modal-close">Close</button>
      </div>
    ) : null,
  Box: ({ children, ...props }: React.PropsWithChildren<Record<string, unknown>>) => <div {...props}>{children}</div>,
  SpaceBetween: ({ children, direction = 'vertical' }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid={`spaceBetween-${direction}`}>{children}</div>
  ),
  Container: ({ children }: React.PropsWithChildren<Record<string, unknown>>) => <div data-testid="container">{children}</div>,
  Header: ({ children, actions }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid="header">
      {children}
      {actions}
    </div>
  ),
  Badge: ({ children, color }: React.PropsWithChildren<Record<string, unknown>>) => (
    <span data-testid={`badge-${color}`} className={`badge-${color}`}>
      {children}
    </span>
  ),
  StatusIndicator: ({ children, type }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid={`status-${type}`}>{children}</div>
  ),
  Icon: ({ name }: React.PropsWithChildren<Record<string, unknown>>) => <span data-testid={`icon-${name}`}>{name}</span>,
  Textarea: ({ value, onChange, onKeyDown, placeholder, disabled }: React.PropsWithChildren<Record<string, unknown>>) => (
    <textarea
      value={value}
      onChange={(e) => onChange({ detail: { value: e.target.value } })}
      onKeyDown={(e) => onKeyDown({ detail: { key: e.key, shiftKey: e.shiftKey } })}
      placeholder={placeholder}
      disabled={disabled}
      data-testid="textarea"
    />
  ),
  Alert: ({ children, type, header }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid={`alert-${type}`}>
      <div data-testid="alert-header">{header}</div>
      {children}
    </div>
  )
}));

// Mock react-markdown
vi.mock('react-markdown', () => ({
  default: function ReactMarkdown({ children }: { children: string }) {
    return <div data-testid="markdown-content">{children}</div>;
  }
}));

// Mock fetch for API calls
global.fetch = vi.fn();

const mockFetch = fetch as unknown as ReturnType<typeof vi.fn>;

// Wrapper component for tests
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>
    {children}
  </BrowserRouter>
);

describe('GlobalAnalyticsAssistant Message Rendering', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockClear();
  });

  describe('Rich Message Metadata Rendering', () => {
    it('should render message with insights metadata', async () => {
      // Mock API response with insights
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Your revenue is up 15% this month!',
          insights: {
            type: 'revenue',
            title: 'Revenue Growth',
            value: '$125,000',
            change: {
              value: 15,
              direction: 'up',
              period: 'last month'
            },
            confidence: 0.9,
            recommendations: ['Continue current strategy', 'Expand to new markets']
          }
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'How is my revenue?' } });
      fireEvent.click(sendButton);

      // Wait for response and check basic rendering
      await screen.findByText('Your revenue is up 15% this month!');
      
      // Check for insights components (they exist but may be rendered differently)
      expect(screen.getByText('Revenue Growth')).toBeInTheDocument();
      expect(screen.getByText('$125,000')).toBeInTheDocument();
    });

    it('should render message with chart metadata', async () => {
      // Mock API response with charts
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Here are your analytics:',
          charts: [
            {
              type: 'metric',
              title: 'Total Revenue',
              data: {
                value: '$125,000',
                subtitle: 'This month'
              }
            }
          ]
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Show me charts' } });
      fireEvent.click(sendButton);

      // Wait for response and check chart rendering
      await screen.findByText('Here are your analytics:');
      
      // Check for chart components
      expect(screen.getByText('Total Revenue')).toBeInTheDocument();
      expect(screen.getByText('$125,000')).toBeInTheDocument();
    });

    it('should render message with page actions metadata', async () => {
      // Mock API response with page actions
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'I can help you with these actions:',
          page_actions: [
            {
              label: 'Export Revenue Report',
              type: 'export',
              action: 'export revenue data',
              icon: '📤',
              description: 'Download detailed revenue report'
            }
          ]
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'What can I do?' } });
      fireEvent.click(sendButton);

      // Wait for response and check page actions rendering
      await screen.findByText('I can help you with these actions:');
      
      // Check for page actions components
      expect(screen.getByText('Export Revenue Report')).toBeInTheDocument();
    });

    it('should render message with badges metadata', async () => {
      // Mock API response with badges
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'System status update:',
          badges: [
            {
              text: 'High Performance',
              type: 'success',
              icon: '🚀'
            },
            {
              text: 'New Data Available',
              type: 'info',
              icon: '📊'
            }
          ]
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'System status' } });
      fireEvent.click(sendButton);

      // Wait for response and check badges rendering
      await screen.findByText('System status update:');
      
      // Check for badges
      expect(screen.getByTestId('badge-success')).toBeInTheDocument();
      expect(screen.getByTestId('badge-info')).toBeInTheDocument();
      expect(screen.getByText('High Performance')).toBeInTheDocument();
      expect(screen.getByText('New Data Available')).toBeInTheDocument();
    });

    it('should handle complex message with multiple metadata types', async () => {
      // Mock API response with multiple metadata types
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Comprehensive analytics overview:',
          insights: {
            type: 'performance',
            title: 'Overall Performance',
            confidence: 0.85
          },
          charts: [
            {
              type: 'metric',
              title: 'Key Metric',
              data: { value: '95%' }
            }
          ],
          page_actions: [
            {
              label: 'Deep Dive Analysis',
              type: 'analyze',
              action: 'analyze performance'
            }
          ],
          badges: [
            {
              text: 'Trending Up',
              type: 'success'
            }
          ],
          suggested_actions: ['Show more details', 'Export data']
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Full overview' } });
      fireEvent.click(sendButton);

      // Wait for response and check metadata components exist
      await screen.findByText('Comprehensive analytics overview:');
      
      // Check for metadata components
      expect(screen.getByText('Overall Performance')).toBeInTheDocument();
      expect(screen.getByText('Key Metric')).toBeInTheDocument();
      expect(screen.getByText('Deep Dive Analysis')).toBeInTheDocument();
      expect(screen.getByTestId('badge-success')).toBeInTheDocument();
    });
  });

  describe('Modal vs Docked View Rendering', () => {
    it('should render rich metadata in modal view', async () => {
      // Mock API response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Analytics data:',
          insights: {
            type: 'revenue',
            title: 'Revenue Analysis',
            value: '$100K',
            recommendations: ['Optimize pricing', 'Expand market']
          }
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open in modal view (default)
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Verify modal is open
      expect(screen.getByTestId('modal')).toBeInTheDocument();

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Show revenue' } });
      fireEvent.click(sendButton);

      // Wait for response
      await screen.findByText('Analytics data:');
      
      // In modal view, should show insights
      expect(screen.getByText('Revenue Analysis')).toBeInTheDocument();
      expect(screen.getByText('$100K')).toBeInTheDocument();
    });

    it('should render metadata in docked view', async () => {
      // Mock API response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Analytics data:',
          insights: {
            type: 'revenue',
            title: 'Revenue Analysis',
            confidence: 0.9
          }
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open and switch to docked view
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);
      
      const dockButton = screen.getByLabelText('Switch to docked view');
      fireEvent.click(dockButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Show revenue' } });
      fireEvent.click(sendButton);

      // Wait for response
      await screen.findByText('Analytics data:');
      
      // Should show insights in docked view
      expect(screen.getByText('Revenue Analysis')).toBeInTheDocument();
    });
  });

  describe('Interactive Elements', () => {
    it('should handle suggestion clicks', async () => {
      // Mock API response with suggestions
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Here are some options:',
          suggested_actions: ['Show revenue trends', 'Export data']
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Help me' } });
      fireEvent.click(sendButton);

      // Wait for response
      await screen.findByText('Here are some options:');
      
      // Check that suggestions are rendered (they may be in buttons)
      expect(screen.getByText('Show revenue trends')).toBeInTheDocument();
      expect(screen.getByText('Export data')).toBeInTheDocument();
    });

    it('should handle page action rendering', async () => {
      // Mock API response with page actions
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Available actions:',
          page_actions: [
            {
              label: 'View Dashboard',
              type: 'navigate',
              url: '/analytics',
              icon: '📊'
            }
          ]
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'What can I do?' } });
      fireEvent.click(sendButton);

      // Wait for response
      await screen.findByText('Available actions:');
      
      // Check that page actions are rendered
      expect(screen.getByText('View Dashboard')).toBeInTheDocument();
    });
  });

  describe('Error Handling', () => {
    it('should render error metadata when API fails', async () => {
      // Mock API failure
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Test message' } });
      fireEvent.click(sendButton);

      // Wait for error response
      await screen.findByText(/having trouble connecting/);
      
      // Check for error metadata components
      expect(screen.getByText('Connection Issue')).toBeInTheDocument();
      expect(screen.getByText('Service Status')).toBeInTheDocument();
      expect(screen.getByText('Offline')).toBeInTheDocument();
      expect(screen.getByTestId('badge-error')).toBeInTheDocument();
    });
  });
});