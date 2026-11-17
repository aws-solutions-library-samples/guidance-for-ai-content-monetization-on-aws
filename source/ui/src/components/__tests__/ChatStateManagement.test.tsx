/**
 * Unit tests for chat state management with AgentCore Memory in GlobalAnalyticsAssistant
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, 'localStorage', {
  value: localStorageMock
});

// Wrapper component for tests
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <BrowserRouter>
    {children}
  </BrowserRouter>
);

describe('GlobalAnalyticsAssistant Chat State Management', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockClear();
    localStorageMock.getItem.mockClear();
    localStorageMock.setItem.mockClear();
  });

  describe('Session Management', () => {
    it('should generate and store a new session ID on first load', () => {
      localStorageMock.getItem.mockReturnValue(null);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Should check for existing session ID
      expect(localStorageMock.getItem).toHaveBeenCalledWith('analytics-assistant-session-id');
      
      // Should generate and store new session ID
      expect(localStorageMock.setItem).toHaveBeenCalledWith(
        'analytics-assistant-session-id',
        expect.stringMatching(/^analytics-session-\d+-[a-z0-9]+$/)
      );
    });

    it('should use existing session ID if available', () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Should check for existing session ID
      expect(localStorageMock.getItem).toHaveBeenCalledWith('analytics-assistant-session-id');
      
      // Should not generate new session ID
      expect(localStorageMock.setItem).not.toHaveBeenCalled();
    });
  });

  describe('Conversation History Loading', () => {
    it('should attempt to load conversation history on mount', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock successful conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Hello',
              timestamp: '2024-01-01T10:00:00Z',
              metadata: {}
            },
            {
              id: 'msg-2',
              type: 'assistant',
              content: 'Hi there!',
              timestamp: '2024-01-01T10:00:01Z',
              metadata: {}
            }
          ],
          last_message_time: '2024-01-01T10:00:01Z'
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Wait for conversation loading API call
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          'http://localhost:5001/api/analytics/conversation/load',
          expect.objectContaining({
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: expect.stringContaining(existingSessionId)
          })
        );
      });
    });

    it('should display conversation history indicator when messages are loaded', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock successful conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Hello',
              timestamp: '2024-01-01T10:00:00Z'
            },
            {
              id: 'msg-2',
              type: 'assistant',
              content: 'Hi there!',
              timestamp: '2024-01-01T10:00:01Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:01Z'
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Wait for conversation loading and check for history indicator
      await waitFor(() => {
        expect(screen.getByText('2 msgs')).toBeInTheDocument();
      });
    });

    it('should handle conversation loading failure gracefully', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock failed conversation loading
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Should not crash and should continue without history
      await waitFor(() => {
        expect(screen.getByText('Analytics Assistant')).toBeInTheDocument();
      });

      // Should not show history indicator
      expect(screen.queryByText(/msgs/)).not.toBeInTheDocument();
    });
  });

  describe('Conversation Persistence', () => {
    it('should save conversation history when sending messages', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock empty conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ messages: [] })
      } as Response);

      // Mock chat API response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Hello there!',
          insights: { type: 'greeting' }
        })
      } as Response);

      // Mock conversation saving
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Wait for initial loading
      await waitFor(() => {
        expect(screen.getByTestId('textarea')).toBeInTheDocument();
      });

      // Send a message
      const textarea = screen.getByTestId('textarea');
      const sendButton = screen.getByText('Send');
      
      fireEvent.change(textarea, { target: { value: 'Test message' } });
      fireEvent.click(sendButton);

      // Wait for conversation saving API call
      await waitFor(() => {
        const saveCalls = mockFetch.mock.calls.filter(call => 
          call[0].includes('/conversation/save')
        );
        expect(saveCalls.length).toBeGreaterThan(0);
      });
    });

    it('should maintain conversation state across page navigation', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading with existing messages
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Previous message',
              timestamp: '2024-01-01T10:00:00Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:00Z'
        })
      } as Response);

      const { rerender } = render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Wait for conversation loading
      await waitFor(() => {
        expect(screen.getByText('1 msgs')).toBeInTheDocument();
      });

      // Simulate page navigation by re-rendering
      rerender(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Should still show conversation indicator
      expect(screen.getByText('1 msgs')).toBeInTheDocument();
    });
  });

  describe('Conversation Management Actions', () => {
    it('should show clear conversation button when history exists', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading with existing messages
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Previous message',
              timestamp: '2024-01-01T10:00:00Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:00Z'
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('1 msgs');
      fireEvent.click(openButton);

      // Wait for modal to open and check for clear button
      await waitFor(() => {
        expect(screen.getByLabelText('Clear conversation history')).toBeInTheDocument();
      });
    });

    it('should clear conversation history when clear button is clicked', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading with existing messages
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Previous message',
              timestamp: '2024-01-01T10:00:00Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:00Z'
        })
      } as Response);

      // Mock conversation clearing
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ success: true })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('1 msgs');
      fireEvent.click(openButton);

      // Wait for modal and click clear button
      await waitFor(() => {
        const clearButton = screen.getByLabelText('Clear conversation history');
        fireEvent.click(clearButton);
      });

      // Should call conversation clear API
      await waitFor(() => {
        const clearCalls = mockFetch.mock.calls.filter(call => 
          call[0].includes('/conversation/clear')
        );
        expect(clearCalls.length).toBeGreaterThan(0);
      });
    });

    it('should not show clear button when no history exists', async () => {
      localStorageMock.getItem.mockReturnValue(null);

      // Mock empty conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ messages: [] })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Wait for modal to open
      await waitFor(() => {
        expect(screen.getByTestId('modal')).toBeInTheDocument();
      });

      // Should not show clear button
      expect(screen.queryByLabelText('Clear conversation history')).not.toBeInTheDocument();
    });
  });

  describe('UI State Management', () => {
    it('should maintain minimized state independently of conversation persistence', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ messages: [] })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Wait for modal and minimize
      await waitFor(() => {
        const minimizeButton = screen.getByLabelText('Minimize');
        fireEvent.click(minimizeButton);
      });

      // Should show minimized state
      expect(screen.getByLabelText('Restore Analytics Assistant')).toBeInTheDocument();
    });

    it('should show conversation indicators in minimized state', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading with messages
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Test',
              timestamp: '2024-01-01T10:00:00Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:00Z'
        })
      } as Response);

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open and minimize
      const openButton = screen.getByText('1 msgs');
      fireEvent.click(openButton);

      await waitFor(() => {
        const minimizeButton = screen.getByLabelText('Minimize');
        fireEvent.click(minimizeButton);
      });

      // Should show conversation count in minimized state
      expect(screen.getByTestId('badge-blue')).toBeInTheDocument();
    });
  });

  describe('Error Handling', () => {
    it('should handle conversation save failures gracefully', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock empty conversation loading
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ messages: [] })
      } as Response);

      // Mock chat API response
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          response: 'Hello there!'
        })
      } as Response);

      // Mock conversation save failure
      mockFetch.mockRejectedValueOnce(new Error('Save failed'));

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant
      const openButton = screen.getByText('Analytics Assistant');
      fireEvent.click(openButton);

      // Wait for initial loading and send message
      await waitFor(() => {
        const textarea = screen.getByTestId('textarea');
        const sendButton = screen.getByText('Send');
        
        fireEvent.change(textarea, { target: { value: 'Test message' } });
        fireEvent.click(sendButton);
      });

      // Should still show the message in UI even if save failed
      await waitFor(() => {
        expect(screen.getByText('Hello there!')).toBeInTheDocument();
      });
    });

    it('should handle conversation clear failures gracefully', async () => {
      const existingSessionId = 'analytics-session-12345-abcdef';
      localStorageMock.getItem.mockReturnValue(existingSessionId);

      // Mock conversation loading with messages
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          messages: [
            {
              id: 'msg-1',
              type: 'user',
              content: 'Test',
              timestamp: '2024-01-01T10:00:00Z'
            }
          ],
          last_message_time: '2024-01-01T10:00:00Z'
        })
      } as Response);

      // Mock conversation clear failure
      mockFetch.mockRejectedValueOnce(new Error('Clear failed'));

      render(
        <TestWrapper>
          <GlobalAnalyticsAssistant />
        </TestWrapper>
      );

      // Open the assistant and try to clear
      const openButton = screen.getByText('1 msgs');
      fireEvent.click(openButton);

      await waitFor(() => {
        const clearButton = screen.getByLabelText('Clear conversation history');
        fireEvent.click(clearButton);
      });

      // Should not crash the component
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });
  });
});