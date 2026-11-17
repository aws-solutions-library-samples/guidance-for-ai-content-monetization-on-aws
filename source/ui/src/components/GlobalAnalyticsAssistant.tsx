/**
 * Global Analytics Assistant Component
 * 
 * A floating analytics assistant that can be launched from any page in the publisher UI.
 * Uses CloudScape design system components and provides context-aware help and insights.
 */

import React, { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import {
  Button,
  Modal,
  Box,
  SpaceBetween,
  Container,
  Header,
  Badge,
  StatusIndicator,
  Icon,
  Textarea,
  Alert
} from '@cloudscape-design/components';

// Types for chat messages and page context
interface ChatMessage {
  id: string;
  type: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  metadata?: {
    pageContext?: PageContext;
    suggestions?: string[];
    insights?: MessageInsights;
    charts?: ChartData[];
    pageActions?: PageAction[];
    badges?: MessageBadge[];
  };
}

interface MessageInsights {
  type: 'performance' | 'revenue' | 'trend' | 'recommendation' | 'alert' | 'success';
  title?: string;
  value?: string | number;
  change?: {
    value: number;
    direction: 'up' | 'down' | 'neutral';
    period: string;
  };
  confidence?: number;
  relevantMetrics?: string[];
  contextType?: string;
  recommendations?: string[];
}

interface ChartData {
  type: 'line' | 'bar' | 'pie' | 'metric';
  title: string;
  data: unknown;
  config?: {
    xAxis?: string;
    yAxis?: string;
    colors?: string[];
    showLegend?: boolean;
  };
}

interface PageAction {
  label: string;
  type: 'navigate' | 'filter' | 'export' | 'analyze';
  action: string;
  icon?: string;
  description?: string;
  url?: string;
}

interface MessageBadge {
  text: string;
  type: 'info' | 'success' | 'warning' | 'error' | 'neutral';
  icon?: string;
}

interface PageContext {
  pageType: 'analytics' | 'content' | 'bot-control' | 'system' | 'home';
  pageName: string;
  currentFilters?: Record<string, unknown>;
  availableActions?: string[];
  currentPageData?: Record<string, unknown>;
}

interface GlobalAnalyticsAssistantProps {
  // Optional props for customization
  position?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
}

// Component to render insights with rich formatting
const InsightRenderer: React.FC<{ insights: MessageInsights }> = ({ insights }) => {
  const getInsightColor = (type: string) => {
    switch (type) {
      case 'performance': return '#0073bb';
      case 'revenue': return '#037f0c';
      case 'trend': return '#8b5a2b';
      case 'recommendation': return '#7d2d94';
      case 'alert': return '#d13212';
      case 'success': return '#037f0c';
      default: return '#5f6b7a';
    }
  };

  const getInsightIcon = (type: string) => {
    switch (type) {
      case 'performance': return '📊';
      case 'revenue': return '💰';
      case 'trend': return '📈';
      case 'recommendation': return '💡';
      case 'alert': return '⚠️';
      case 'success': return '✅';
      default: return 'ℹ️';
    }
  };

  return (
    <div style={{
      backgroundColor: '#f8f9fa',
      border: `1px solid ${getInsightColor(insights.type)}`,
      borderRadius: '8px',
      padding: '12px',
      marginTop: '8px'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '16px' }}>{getInsightIcon(insights.type)}</span>
        {insights.title && (
          <span style={{
            fontWeight: 'bold',
            color: getInsightColor(insights.type),
            fontSize: '14px'
          }}>
            {insights.title}
          </span>
        )}
        {insights.confidence && (
          <Badge color={insights.confidence > 0.8 ? 'green' : insights.confidence > 0.6 ? 'blue' : 'grey'}>
            {Math.round(insights.confidence * 100)}% confidence
          </Badge>
        )}
      </div>

      {insights.value && (
        <div style={{ marginBottom: '8px' }}>
          <span style={{ fontSize: '18px', fontWeight: 'bold', color: getInsightColor(insights.type) }}>
            {insights.value}
          </span>
          {insights.change && (
            <span style={{
              marginLeft: '8px',
              fontSize: '12px',
              color: insights.change.direction === 'up' ? '#037f0c' :
                insights.change.direction === 'down' ? '#d13212' : '#5f6b7a'
            }}>
              {insights.change.direction === 'up' ? '↗️' :
                insights.change.direction === 'down' ? '↘️' : '➡️'}
              {insights.change.value}% vs {insights.change.period}
            </span>
          )}
        </div>
      )}

      {insights.recommendations && insights.recommendations.length > 0 && (
        <div>
          <div style={{ fontSize: '12px', fontWeight: 'bold', marginBottom: '4px' }}>
            Recommendations:
          </div>
          {insights.recommendations.map((rec, index) => (
            <div key={index} style={{
              fontSize: '12px',
              color: '#5f6b7a',
              marginBottom: '2px',
              paddingLeft: '12px'
            }}>
              • {rec}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// Component to render simple charts
const ChartRenderer: React.FC<{ chart: ChartData }> = ({ chart }) => {
  if (chart.type === 'metric') {
    return (
      <div style={{
        backgroundColor: '#f0f8ff',
        border: '1px solid #b3d9ff',
        borderRadius: '8px',
        padding: '12px',
        marginTop: '8px',
        textAlign: 'center'
      }}>
        <div style={{ fontSize: '12px', color: '#5f6b7a', marginBottom: '4px' }}>
          {chart.title}
        </div>
        <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#0073bb' }}>
          {chart.data.value}
        </div>
        {chart.data.subtitle && (
          <div style={{ fontSize: '10px', color: '#5f6b7a' }}>
            {chart.data.subtitle}
          </div>
        )}
      </div>
    );
  }

  // For other chart types, show a placeholder with data summary
  return (
    <div style={{
      backgroundColor: '#f8f9fa',
      border: '1px solid #e9ecef',
      borderRadius: '8px',
      padding: '12px',
      marginTop: '8px'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span>📊</span>
        <span style={{ fontWeight: 'bold', fontSize: '14px' }}>{chart.title}</span>
        <Badge color="blue">{chart.type} chart</Badge>
      </div>
      <div style={{ fontSize: '12px', color: '#5f6b7a' }}>
        Chart data available - {Array.isArray(chart.data) ? chart.data.length : 'Multiple'} data points
      </div>
    </div>
  );
};

// Component to render page actions
const PageActionsRenderer: React.FC<{ actions: PageAction[] }> = ({ actions }) => {
  const getActionIcon = (type: string) => {
    switch (type) {
      case 'navigate': return '🧭';
      case 'filter': return '🔍';
      case 'export': return '📤';
      case 'analyze': return '📊';
      default: return '⚡';
    }
  };

  const getActionColor = (type: string) => {
    switch (type) {
      case 'navigate': return 'blue';
      case 'filter': return 'green';
      case 'export': return 'grey';
      case 'analyze': return 'red';
      default: return 'blue';
    }
  };

  return (
    <div style={{ marginTop: '8px' }}>
      <div style={{ fontSize: '12px', fontWeight: 'bold', marginBottom: '6px', color: '#5f6b7a' }}>
        🎯 Suggested Actions:
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {actions.slice(0, 3).map((action, index) => (
          <button
            key={index}
            onClick={() => {
              if (action.url) {
                window.open(action.url, '_blank');
              } else {
                // Handle other action types
                console.log('Action:', action.action);
              }
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 12px',
              border: '1px solid #e9ecef',
              borderRadius: '6px',
              backgroundColor: '#ffffff',
              cursor: 'pointer',
              fontSize: '12px',
              textAlign: 'left',
              transition: 'all 0.2s ease'
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = '#f8f9fa';
              e.currentTarget.style.borderColor = '#0073bb';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = '#ffffff';
              e.currentTarget.style.borderColor = '#e9ecef';
            }}
          >
            <span>{action.icon || getActionIcon(action.type)}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 'bold' }}>{action.label}</div>
              {action.description && (
                <div style={{ fontSize: '10px', color: '#5f6b7a' }}>
                  {action.description}
                </div>
              )}
            </div>
            <Badge color={getActionColor(action.type)} size="small">
              {action.type}
            </Badge>
          </button>
        ))}
      </div>
    </div>
  );
};

// Component to render message badges
const MessageBadgesRenderer: React.FC<{ badges: MessageBadge[] }> = ({ badges }) => {
  const getBadgeColor = (type: string) => {
    switch (type) {
      case 'success': return 'green';
      case 'warning': return 'red';
      case 'error': return 'red';
      case 'info': return 'blue';
      default: return 'grey';
    }
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '6px' }}>
      {badges.map((badge, index) => (
        <span key={index} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          {badge.icon && <span style={{ fontSize: '12px' }}>{badge.icon}</span>}
          <Badge color={getBadgeColor(badge.type)} size="small">
            {badge.text}
          </Badge>
        </span>
      ))}
    </div>
  );
};

// Enhanced component to render markdown content with proper styling
const MarkdownContent: React.FC<{ content: string }> = ({ content }) => {
  return (
    <ReactMarkdown
      components={{
        // Style headers
        h1: ({ children }) => (
          <div style={{ fontSize: '18px', fontWeight: 'bold', marginBottom: '8px', color: '#232f3e' }}>
            {children}
          </div>
        ),
        h2: ({ children }) => (
          <div style={{ fontSize: '16px', fontWeight: 'bold', marginBottom: '6px', color: '#232f3e' }}>
            {children}
          </div>
        ),
        h3: ({ children }) => (
          <div style={{ fontSize: '14px', fontWeight: 'bold', marginBottom: '4px', color: '#232f3e' }}>
            {children}
          </div>
        ),
        // Style paragraphs
        p: ({ children }) => (
          <div style={{ marginBottom: '8px', lineHeight: '1.4' }}>
            {children}
          </div>
        ),
        // Style lists
        ul: ({ children }) => (
          <ul style={{ marginBottom: '8px', paddingLeft: '16px' }}>
            {children}
          </ul>
        ),
        ol: ({ children }) => (
          <ol style={{ marginBottom: '8px', paddingLeft: '16px' }}>
            {children}
          </ol>
        ),
        li: ({ children }) => (
          <li style={{ marginBottom: '2px' }}>
            {children}
          </li>
        ),
        // Style strong/bold text
        strong: ({ children }) => (
          <strong style={{ fontWeight: 'bold', color: '#232f3e' }}>
            {children}
          </strong>
        ),
        // Style code
        code: ({ children }) => (
          <code style={{
            backgroundColor: '#f2f3f3',
            padding: '2px 4px',
            borderRadius: '3px',
            fontSize: '12px',
            fontFamily: 'monospace'
          }}>
            {children}
          </code>
        ),
        // Style blockquotes
        blockquote: ({ children }) => (
          <div style={{
            borderLeft: '3px solid #0073bb',
            paddingLeft: '12px',
            marginBottom: '8px',
            fontStyle: 'italic',
            color: '#5f6b7a'
          }}>
            {children}
          </div>
        )
      }}
    >
      {content}
    </ReactMarkdown>
  );
};

export default function GlobalAnalyticsAssistant({
  position = 'bottom-right'
}: GlobalAnalyticsAssistantProps) {
  // State management
  const [isOpen, setIsOpen] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [isDocked, setIsDocked] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [pageContext, setPageContext] = useState<PageContext | null>(null);
  const [hasUnreadMessages, setHasUnreadMessages] = useState(false);

  // Session management for AgentCore Memory persistence
  const [sessionId, setSessionId] = useState<string>(() => {
    // Generate or retrieve session ID for AgentCore Memory
    const existingSessionId = localStorage.getItem('analytics-assistant-session-id');
    if (existingSessionId) {
      return existingSessionId;
    }
    const newSessionId = `analytics-session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    localStorage.setItem('analytics-assistant-session-id', newSessionId);
    return newSessionId;
  });

  // Conversation persistence state
  const [conversationLoaded, setConversationLoaded] = useState(false);
  const [conversationIndicator, setConversationIndicator] = useState<{
    hasHistory: boolean;
    lastMessageTime?: Date;
    messageCount: number;
  }>({ hasHistory: false, messageCount: 0 });

  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // React Router location for page context detection
  const location = useLocation();

  // AgentCore Memory integration functions
  const loadConversationHistory = async () => {
    const API_BASE_URL = import.meta.env.VITE_ANALYTICS_API_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001';

    // For AWS backend, use AgentCore Memory instead of local storage
    if (API_BASE_URL.includes('amazonaws.com') || API_BASE_URL === 'https://api.example.com') {
      // AgentCore Memory handles conversation persistence automatically
      // No need to manually load - the agent maintains session state
      return;
    }

    try {
      // Get auth headers if available
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      try {
        const { getCurrentUser } = await import('aws-amplify/auth');
        const user = await getCurrentUser();
        if (user) {
          const { fetchAuthSession } = await import('aws-amplify/auth');
          const session = await fetchAuthSession();
          const token = session.tokens?.idToken?.toString();
          if (token) {
            headers['Authorization'] = `Bearer ${token}`;
          }
        }
      } catch (authError) {
        console.log('No auth available for conversation loading:', authError);
      }
      const response = await fetch(`${API_BASE_URL}/api/analytics/conversation/load`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          session_id: sessionId,
          user_context: {
            publisher_id: 'demo_publisher',
            access_level: 'admin'
          }
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (data.messages && data.messages.length > 0) {
          // Convert API messages to ChatMessage format
          const loadedMessages: ChatMessage[] = data.messages.map((msg: { id?: string; type?: string; content?: string; timestamp?: string; [k: string]: unknown }) => ({
            id: msg.id || `msg-${Date.now()}-${Math.random()}`,
            type: msg.type || 'assistant',
            content: msg.content || '',
            timestamp: new Date(msg.timestamp || Date.now()),
            metadata: msg.metadata || {}
          }));

          setMessages(loadedMessages);
          setConversationIndicator({
            hasHistory: true,
            lastMessageTime: new Date(data.last_message_time),
            messageCount: loadedMessages.length
          });
        }
      }
    } catch (error) {
      console.log('Could not load conversation history:', error);
      // Gracefully continue without history
    } finally {
      setConversationLoaded(true);
    }
  };

  const saveConversationHistory = async (newMessages: ChatMessage[]) => {
    const API_BASE_URL = import.meta.env.VITE_ANALYTICS_API_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001';

    // For AWS backend, AgentCore Memory handles conversation persistence automatically
    if (API_BASE_URL.includes('amazonaws.com') || API_BASE_URL === 'https://api.example.com') {
      // No manual saving needed - AgentCore Memory persists conversations via session_id
      return;
    }

    try {

      // Get auth headers if available
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      try {
        const { getCurrentUser } = await import('aws-amplify/auth');
        const user = await getCurrentUser();
        if (user) {
          const { fetchAuthSession } = await import('aws-amplify/auth');
          const session = await fetchAuthSession();
          const token = session.tokens?.idToken?.toString();
          if (token) {
            headers['Authorization'] = `Bearer ${token}`;
          }
        }
      } catch (authError) {
        console.log('No auth available for conversation saving:', authError);
      }

      // Skip conversation saving for AWS backend - not implemented
      if (API_BASE_URL.includes('amazonaws.com')) {
        return;
      }
      await fetch(`${API_BASE_URL}/api/analytics/conversation/save`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          session_id: sessionId,
          messages: newMessages.map(msg => ({
            id: msg.id,
            type: msg.type,
            content: msg.content,
            timestamp: msg.timestamp.toISOString(),
            metadata: msg.metadata
          })),
          user_context: {
            publisher_id: 'demo_publisher',
            access_level: 'admin'
          },
          page_context: pageContext
        })
      });
    } catch (error) {
      console.log('Could not save conversation history:', error);
      // Gracefully continue without saving
    }
  };

  // Enhanced page context detection with filters and user actions
  useEffect(() => {
    const detectPageContext = (): PageContext => {
      const path = location.pathname;
      const searchParams = new URLSearchParams(location.search);

      // Extract current filters from URL parameters
      const currentFilters: Record<string, unknown> = {};
      searchParams.forEach((value, key) => {
        currentFilters[key] = value;
      });

      // Detect page type and extract context-specific information
      if (path === '/' || path === '/home') {
        return {
          pageType: 'home',
          pageName: 'Home Dashboard',
          currentFilters,
          availableActions: [
            'Navigate to Analytics',
            'View Content Library',
            'Check System Status',
            'Review Bot Activity'
          ]
        };
      } else if (path.startsWith('/dashboard') || path.startsWith('/analytics')) {
        // Enhanced analytics page context
        const analyticsContext = {
          pageType: 'analytics' as const,
          pageName: 'Analytics Dashboard',
          currentFilters,
          availableActions: [
            'Export Analytics Data',
            'Change Date Range',
            'Filter by Content Type',
            'View Revenue Metrics',
            'Analyze Bot Behavior',
            'Create Custom Report'
          ]
        };

        // Add specific sub-page context
        if (path.includes('/revenue')) {
          analyticsContext.pageName = 'Revenue Analytics';
          analyticsContext.availableActions = [
            'View Revenue Trends',
            'Analyze Payment Models',
            'Export Revenue Report',
            'Compare Time Periods'
          ];
        } else if (path.includes('/content-performance')) {
          analyticsContext.pageName = 'Content Performance';
          analyticsContext.availableActions = [
            'View Top Performing Content',
            'Analyze Content Trends',
            'Compare Content Types',
            'Export Performance Data'
          ];
        } else if (path.includes('/bot-activity')) {
          analyticsContext.pageName = 'Bot Activity Analytics';
          analyticsContext.availableActions = [
            'View Bot Usage Patterns',
            'Analyze Access Frequency',
            'Review Bot Credentials',
            'Export Bot Activity Report'
          ];
        }

        return analyticsContext;
      } else if (path.startsWith('/content')) {
        // Enhanced content management context
        const contentContext = {
          pageType: 'content' as const,
          pageName: 'Content Management',
          currentFilters,
          availableActions: [
            'Upload New Content',
            'Edit Content Metadata',
            'View Processing Status',
            'Analyze Content Performance',
            'Manage Content Categories'
          ]
        };

        // Add specific sub-page context
        if (path.includes('/upload')) {
          contentContext.pageName = 'Content Upload';
          contentContext.availableActions = [
            'Upload Files',
            'Set Metadata',
            'Configure Access Rules',
            'Preview Content'
          ];
        } else if (path.includes('/library')) {
          contentContext.pageName = 'Content Library';
          contentContext.availableActions = [
            'Search Content',
            'Filter by Category',
            'Sort by Performance',
            'Bulk Edit Metadata'
          ];
        } else if (path.includes('/processing')) {
          contentContext.pageName = 'Content Processing';
          contentContext.availableActions = [
            'View Processing Queue',
            'Check Processing Status',
            'Retry Failed Processing',
            'Monitor System Health'
          ];
        }

        return contentContext;
      } else if (path.startsWith('/bot-control')) {
        // Enhanced bot control context
        const botControlContext = {
          pageType: 'bot-control' as const,
          pageName: 'Bot Control',
          currentFilters,
          availableActions: [
            'Configure Access Rules',
            'Review Bot Activity',
            'Manage Partner Relationships',
            'Set Pricing Models',
            'Monitor Security Events'
          ]
        };

        // Add specific sub-page context
        if (path.includes('/access-rules')) {
          botControlContext.pageName = 'Access Rules Configuration';
          botControlContext.availableActions = [
            'Create Access Rules',
            'Edit Existing Rules',
            'Test Rule Logic',
            'View Rule Performance'
          ];
        } else if (path.includes('/partners')) {
          botControlContext.pageName = 'Partner Management';
          botControlContext.availableActions = [
            'Add New Partners',
            'Review Partner Activity',
            'Configure Partner Settings',
            'Analyze Partner Performance'
          ];
        } else if (path.includes('/pricing')) {
          botControlContext.pageName = 'Pricing Configuration';
          botControlContext.availableActions = [
            'Set Content Pricing',
            'Configure Payment Models',
            'Review Pricing Performance',
            'Analyze Revenue Impact'
          ];
        }

        return botControlContext;
      } else if (path.startsWith('/system')) {
        // Enhanced system management context
        const systemContext = {
          pageType: 'system' as const,
          pageName: 'System Management',
          currentFilters,
          availableActions: [
            'Check System Status',
            'View System Logs',
            'Configure Settings',
            'Monitor Performance',
            'Manage Users'
          ]
        };

        // Add specific sub-page context
        if (path.includes('/status')) {
          systemContext.pageName = 'System Status';
          systemContext.availableActions = [
            'View Service Health',
            'Check API Status',
            'Monitor Resource Usage',
            'Review System Alerts'
          ];
        } else if (path.includes('/logs')) {
          systemContext.pageName = 'System Logs';
          systemContext.availableActions = [
            'Search Logs',
            'Filter by Severity',
            'Export Log Data',
            'Set Log Alerts'
          ];
        } else if (path.includes('/settings')) {
          systemContext.pageName = 'System Settings';
          systemContext.availableActions = [
            'Update Configuration',
            'Manage API Keys',
            'Configure Notifications',
            'Set Security Policies'
          ];
        }

        return systemContext;
      }

      // Default fallback context
      return {
        pageType: 'home',
        pageName: 'AI Content Monetization API',
        currentFilters,
        availableActions: ['Navigate to sections', 'Get help', 'View documentation']
      };
    };

    const extractCurrentPageData = (): Record<string, unknown> => {
      // Extract any visible data from the current page
      // This would typically be done by reading from page state or DOM
      const pageData: Record<string, unknown> = {};

      // Add URL parameters as current data
      const searchParams = new URLSearchParams(location.search);
      searchParams.forEach((value, key) => {
        pageData[key] = value;
      });

      // Add page-specific data extraction logic
      if (location.pathname.startsWith('/analytics')) {
        pageData.pageSection = 'analytics';
        pageData.hasCharts = true; // Would be determined by actual page content
      } else if (location.pathname.startsWith('/content')) {
        pageData.pageSection = 'content';
        pageData.hasContentList = true; // Would be determined by actual page content
      } else if (location.pathname.startsWith('/bot-control')) {
        pageData.pageSection = 'bot-control';
        pageData.hasAccessRules = true; // Would be determined by actual page content
      }

      return pageData;
    };

    const context = detectPageContext();
    const pageData = extractCurrentPageData();

    // Enhance context with current page data
    const enhancedContext = {
      ...context,
      currentPageData: pageData
    };

    setPageContext(enhancedContext);

    // Generate contextual greeting when page changes and no messages exist (and conversation is loaded)
    if (messages.length === 0 && conversationLoaded) {
      generateContextualGreeting(enhancedContext);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run on navigation only; conversationLoaded is read but not a trigger
  }, [location.pathname, location.search, messages.length]);

  // Load conversation history on component mount and when session changes
  useEffect(() => {
    if (!conversationLoaded) {
      loadConversationHistory();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable loader; run on mount and session change
  }, [conversationLoaded, sessionId]);

  // Persist conversation state to localStorage for page navigation continuity
  useEffect(() => {
    if (messages.length > 0) {
      localStorage.setItem('analytics-assistant-messages', JSON.stringify(messages));
    }
  }, [messages]);

  // Restore conversation state on component mount
  useEffect(() => {
    const savedMessages = localStorage.getItem('analytics-assistant-messages');
    if (savedMessages && messages.length === 0) {
      try {
        const parsedMessages = JSON.parse(savedMessages);
        if (Array.isArray(parsedMessages) && parsedMessages.length > 0) {
          // Convert timestamp strings back to Date objects
          const messagesWithDates = parsedMessages.map(msg => ({
            ...msg,
            timestamp: new Date(msg.timestamp)
          }));
          setMessages(messagesWithDates);
        }
      } catch (error) {
        console.warn('Failed to restore conversation history:', error);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restore once on mount; messages.length is a guard, not a trigger
  }, []);

  // Auto-scroll to bottom when new messages are added
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Focus input when modal opens
  useEffect(() => {
    if (isOpen && !isMinimized) {
      // Focus will be handled by the modal automatically
    }
  }, [isOpen, isMinimized]);

  const scrollToBottom = () => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const generateContextualGreeting = (context: PageContext) => {
    // Enhanced contextual greetings with more specific context awareness
    const getContextualGreeting = () => {
      const baseGreetings = {
        home: {
          icon: "🏠",
          greeting: "Welcome to your Publisher Dashboard!",
          context: "I can see you're on the home page. I'm here to help you navigate and understand your system capabilities.",
          suggestions: [
            "Show me my top performing content",
            "What's my revenue this month?",
            "Which bots are most active?",
            "Help me navigate to analytics"
          ]
        },
        analytics: {
          icon: "📊",
          greeting: "Analytics Dashboard Active",
          context: "I can see you're viewing analytics data. I can help interpret metrics, identify trends, and suggest optimizations.",
          suggestions: [
            "Explain these metrics to me",
            "What are my top performing content pieces?",
            "Show me revenue trends",
            "Which bots are accessing my content most?"
          ]
        },
        content: {
          icon: "📝",
          greeting: "Content Management Hub",
          context: "You're in content management. I can help analyze performance, suggest improvements, and guide content strategy.",
          suggestions: [
            "How is my content performing?",
            "What content should I optimize?",
            "Help me understand processing status",
            "Show me content analytics"
          ]
        },
        'bot-control': {
          icon: "🤖",
          greeting: "Bot Control Center",
          context: "You're managing AI bot access. I can help analyze bot behavior, review access patterns, and optimize partnerships.",
          suggestions: [
            "Show me bot activity patterns",
            "Which partnerships are most valuable?",
            "Help me configure access rules",
            "Analyze bot behavior trends"
          ]
        },
        system: {
          icon: "⚙️",
          greeting: "System Management Console",
          context: "You're in system management. I can help interpret status, explain configurations, and troubleshoot issues.",
          suggestions: [
            "Check system health status",
            "Explain current configuration",
            "Help me troubleshoot issues",
            "Show me system performance"
          ]
        }
      };

      const contextInfo = baseGreetings[context.pageType] || baseGreetings.home;

      // Add specific sub-page context if available
      let specificContext = "";
      if (context.pageName !== context.pageType) {
        specificContext = ` Specifically, you're viewing **${context.pageName}**.`;
      }

      // Add current filters context if available
      let filtersContext = "";
      if (context.currentFilters && Object.keys(context.currentFilters).length > 0) {
        const filtersList = Object.entries(context.currentFilters)
          .map(([key, value]) => `${key}: ${value}`)
          .join(', ');
        filtersContext = ` I can see you have filters applied: ${filtersList}.`;
      }

      // Construct the full contextual greeting
      const fullGreeting = `${contextInfo.icon} **${contextInfo.greeting}**

${contextInfo.context}${specificContext}${filtersContext}

**What would you like to explore?**`;

      return {
        greeting: fullGreeting,
        suggestions: contextInfo.suggestions
      };
    };

    const { greeting, suggestions } = getContextualGreeting();

    const welcomeMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      type: 'assistant',
      content: greeting,
      timestamp: new Date(),
      metadata: {
        pageContext: context,
        suggestions: suggestions,
        insights: {
          contextType: 'greeting',
          pageType: context.pageType,
          hasFilters: context.currentFilters && Object.keys(context.currentFilters).length > 0,
          availableActions: context.availableActions?.length || 0
        }
      }
    };

    setMessages([welcomeMessage]);
  };

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isLoading) return;

    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}-user`,
      type: 'user',
      content: inputValue.trim(),
      timestamp: new Date(),
      metadata: { pageContext: pageContext || undefined }
    };

    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInputValue('');
    setIsLoading(true);

    // Save to AgentCore Memory
    saveConversationHistory(updatedMessages);

    try {
      // Call the real analytics agent API
      const response = await callAnalyticsAgentAPI(userMessage.content, pageContext);

      const assistantMessage: ChatMessage = {
        id: `msg-${Date.now()}-assistant`,
        type: 'assistant',
        content: response.content,
        timestamp: new Date(),
        metadata: {
          pageContext: pageContext || undefined,
          suggestions: response.suggestions,
          // Temporarily disable insights to remove repetitive status widgets
          // insights: response.insights,
          charts: response.charts,
          pageActions: response.pageActions,
          badges: response.badges
        }
      };

      const finalMessages = [...messages, userMessage, assistantMessage];
      setMessages(finalMessages);

      // Save to AgentCore Memory
      saveConversationHistory(finalMessages);

      // Update conversation indicator
      setConversationIndicator({
        hasHistory: true,
        lastMessageTime: assistantMessage.timestamp,
        messageCount: finalMessages.length
      });

      // Mark as unread if minimized
      if (isMinimized) {
        setHasUnreadMessages(true);
      }
    } catch (error) {
      console.error('Error sending message:', error);
      const errorMessage: ChatMessage = {
        id: `msg-${Date.now()}-error`,
        type: 'assistant',
        content: "I'm sorry, I'm having trouble connecting to the analytics service right now. Please try again in a moment.",
        timestamp: new Date()
      };

      const errorMessages = [...messages, userMessage, errorMessage];
      setMessages(errorMessages);

      // Save to AgentCore Memory
      saveConversationHistory(errorMessages);
    } finally {
      setIsLoading(false);
    }
  };

  // Helper function to send a message directly with given text
  const sendMessageWithText = async (messageText: string) => {
    if (!messageText.trim() || isLoading) return;

    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}-user`,
      type: 'user',
      content: messageText.trim(),
      timestamp: new Date(),
      metadata: { pageContext: pageContext || undefined }
    };

    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setInputValue(''); // Clear input after sending
    setIsLoading(true);

    // Save to AgentCore Memory
    saveConversationHistory(updatedMessages);

    try {
      // Call the real analytics agent API
      const response = await callAnalyticsAgentAPI(userMessage.content, pageContext);

      const assistantMessage: ChatMessage = {
        id: `msg-${Date.now()}-assistant`,
        type: 'assistant',
        content: response.content,
        timestamp: new Date(),
        metadata: {
          pageContext: pageContext || undefined,
          suggestions: response.suggestions,
          // Temporarily disable insights to remove repetitive status widgets
          // insights: response.insights,
          charts: response.charts,
          pageActions: response.pageActions,
          badges: response.badges
        }
      };

      const finalMessages = [...messages, userMessage, assistantMessage];
      setMessages(finalMessages);

      // Save to AgentCore Memory
      saveConversationHistory(finalMessages);

      // Update conversation indicator
      setConversationIndicator({
        hasHistory: true,
        lastMessageTime: assistantMessage.timestamp,
        messageCount: finalMessages.length
      });

      // Mark as unread if minimized
      if (isMinimized) {
        setHasUnreadMessages(true);
      }
    } catch (error) {
      console.error('Error sending message:', error);
      const errorMessage: ChatMessage = {
        id: `msg-${Date.now()}-error`,
        type: 'assistant',
        content: "I'm sorry, I'm having trouble connecting to the analytics service right now. Please try again in a moment.",
        timestamp: new Date()
      };

      const errorMessages = [...messages, userMessage, errorMessage];
      setMessages(errorMessages);

      // Save to AgentCore Memory
      saveConversationHistory(errorMessages);
    } finally {
      setIsLoading(false);
    }
  };

  // Helper function to extract contextual suggestions from response content
  const extractSuggestionsFromResponse = (responseContent: string, context: PageContext | null): string[] => {
    const defaultSuggestions = ['Show revenue trends', 'Analyze performance', 'View top content'];
    
    if (!context) return defaultSuggestions;
    
    // Context-aware suggestions based on page type
    switch (context.pageType) {
      case 'analytics':
        return ['Export analytics data', 'Change date range', 'Filter by content type', 'View revenue metrics'];
      case 'content':
        return ['Analyze content performance', 'View content trends', 'Check content popularity'];
      case 'bot-control':
        return ['Analyze bot behavior', 'View bot usage patterns', 'Check access patterns'];
      case 'system':
        return ['View system metrics', 'Check system health', 'Review system logs'];
      default:
        return defaultSuggestions;
    }
  };

  // Call the real analytics agent API
  const callAnalyticsAgentAPI = async (userInput: string, context: PageContext | null) => {
    const ANALYTICS_AGENT_ARN = import.meta.env.VITE_ANALYTICS_AGENT_ARN;
    const AWS_REGION = import.meta.env.VITE_AWS_REGION || 'us-west-2';

    console.log('Environment variables check:');
    console.log('VITE_ANALYTICS_AGENT_ARN:', ANALYTICS_AGENT_ARN);
    console.log('VITE_AWS_REGION:', AWS_REGION);
    console.log('All VITE_ vars:', Object.keys(import.meta.env).filter(key => key.startsWith('VITE_')));

    if (!ANALYTICS_AGENT_ARN) {
      console.error('Analytics Agent ARN not found in environment variables');
      throw new Error('Analytics Agent ARN not configured. Please set VITE_ANALYTICS_AGENT_ARN environment variable.');
    }

    try {
      // Get Cognito token for authentication
      const { getCurrentUser, fetchAuthSession } = await import('aws-amplify/auth');

      let authToken = null;

      try {
        const user = await getCurrentUser();
        if (user) {
          const session = await fetchAuthSession();

          // Try ID token first (required for API Gateway Cognito authorizer)
          if (session.tokens?.idToken) {
            authToken = session.tokens.idToken.toString();
            console.log('✅ Using ID token for API Gateway Cognito authorizer');
          }
          // Fallback to access token if ID token not available
          else if (session.tokens?.accessToken) {
            authToken = session.tokens.accessToken.toString();
            console.log('⚠️ Using access token (API Gateway may reject this)');
          }

          if (authToken) {
            console.log('Added auth token to headers (first 20 chars):', authToken.substring(0, 20) + '...');

            // Decode JWT to see what account/user we're using
            try {
              const payload = JSON.parse(atob(authToken.split('.')[1]));
              console.log('JWT payload (account info):', {
                iss: payload.iss,
                aud: payload.aud,
                client_id: payload.client_id,
                token_use: payload.token_use,
                sub: payload.sub,
                username: payload['cognito:username'],
                groups: payload['cognito:groups']
              });

              // Check if client_id matches expected (gateway uses "Allowed clients")
              const expectedClientId = import.meta.env.VITE_USER_POOL_CLIENT_ID;
              // For ID tokens, client_id is in 'aud' claim; for access tokens, it's in 'client_id' claim
              const clientIdFromToken = payload.client_id || payload.aud;
              const hasValidClientId = clientIdFromToken === expectedClientId;

              console.log('🔍 Token validation details:');
              console.log(`  Expected client ID: ${expectedClientId}`);
              console.log(`  Token aud claim: ${payload.aud}`);
              console.log(`  Token client_id claim: ${payload.client_id}`);
              console.log(`  Token type: ${payload.token_use}`);
              console.log(`  Token issuer: ${payload.iss}`);

              if (!hasValidClientId) {
                console.warn('⚠️ Token client_id mismatch!');
                console.warn('Gateway expects client_id claim to match allowed clients');
              } else {
                console.log('✅ Token client_id matches gateway configuration');
              }
            } catch (e) {
              console.log('Could not decode JWT:', e);
            }
          } else {
            console.log('No access token found in session');
          }
        } else {
          console.log('No current user found');
        }
      } catch (authError) {
        console.log('No auth available, proceeding without credentials:', authError);
      }

      console.log('Using Analytics Agent ARN:', ANALYTICS_AGENT_ARN);

      // Create simple request for Lambda (no MCP format needed)
      const agentRequest = {
        message: userInput,
        page_context: context ? {
          page_type: context.pageType,
          url_path: location.pathname,
          current_data: {
            page_name: context.pageName,
            available_actions: context.availableActions
          }
        } : null,
        session_id: sessionId
      };

      console.log('Agent request payload:', agentRequest);

      // Validate that we have an auth token
      if (!authToken) {
        throw new Error('No authentication tokens available. Please ensure you are logged in.');
      }

      // Call Analytics Agent via dedicated API Gateway or fallback to main API
      const ANALYTICS_API_URL = import.meta.env.VITE_ANALYTICS_API_URL;
      const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'https://example123.execute-api.us-west-2.amazonaws.com/v1';
      
      const analyticsEndpoint = ANALYTICS_API_URL 
        ? `${ANALYTICS_API_URL.replace(/\/$/, '')}/analytics-agent/chat`
        : `${API_BASE_URL.replace(/\/$/, '')}/analytics-agent/chat`;

      console.log('Calling Analytics Agent:', analyticsEndpoint, ANALYTICS_API_URL ? '(dedicated API)' : '(main API)');

      // Create simple request for Lambda (no MCP format needed)
      const analyticsRequest = {
        message: userInput,
        page_context: context ? {
          page_type: context.pageType,
          url_path: location.pathname,
          current_data: {
            page_name: context.pageName,
            available_actions: context.availableActions
          }
        } : null,
        session_id: sessionId
      };

      const response = await fetch(analyticsEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(analyticsRequest)
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error('Gateway request failed:', response.status, errorText);
        throw new Error(`Gateway request failed: ${response.status} - ${errorText}`);
      }

      // Handle Lambda response
      const responseText = await response.text();
      console.log('Lambda response:', responseText.substring(0, 500));

      let agentResponse = null;

      try {
        agentResponse = JSON.parse(responseText);
        console.log('Parsed agent response:', agentResponse);

        // Extract the actual agent response content
        const responseContent = agentResponse.response || agentResponse.content || agentResponse.message;
        
        if (!responseContent) {
          console.warn('No response content found in agent response:', agentResponse);
          throw new Error('Agent response missing content');
        }

        // Extract contextual suggestions based on the response
        const suggestions = agentResponse.suggested_actions || 
                          agentResponse.suggestions || 
                          extractSuggestionsFromResponse(responseContent, context);

        console.log('Agent response metadata:', {
          intent: agentResponse.intent_analysis,
          suggestions: suggestions,
          model: agentResponse.model_used,
          timestamp: agentResponse.timestamp,
          hasContent: !!responseContent
        });

        return {
          content: responseContent,
          suggestions: suggestions,
          insights: agentResponse.insights,
          charts: agentResponse.charts,
          pageActions: agentResponse.page_actions || agentResponse.pageActions,
          badges: agentResponse.badges
        };

      } catch (parseError) {
        console.error('Failed to parse Gateway response:', parseError);
        console.error('Raw response text:', responseText);
        
        // If we can't parse the response, use the raw text if it looks like a valid response
        if (responseText && responseText.length > 10 && !responseText.includes('error')) {
          return {
            content: responseText,
            suggestions: extractSuggestionsFromResponse(responseText, context)
          };
        }
        
        throw new Error(`Failed to parse agent response: ${parseError.message}`);
      }

    } catch (error) {
      console.error('Error calling analytics agent API:', error);

      // Fallback to basic response if API is not available with sample rich metadata
      return {
        content: "I'm having trouble connecting to the analytics service. The local API server might not be running. Please start it with 'python local_api_server.py' and try again.",
        suggestions: ['Start local API server', 'Check connection', 'Try again'],
        insights: {
          type: 'alert',
          title: 'Connection Issue',
          confidence: 0.0,
          relevantMetrics: [],
          contextType: 'error',
          recommendations: ['Check if the analytics agent API is running', 'Verify network connectivity']
        },
        charts: [
          {
            type: 'metric',
            title: 'Service Status',
            data: {
              value: 'Offline',
              subtitle: 'Analytics service unavailable'
            }
          }
        ],
        pageActions: [
          {
            label: 'Troubleshoot Connection',
            type: 'analyze',
            action: 'Help me troubleshoot the analytics service connection',
            icon: '🔧',
            description: 'Get help with connection issues'
          }
        ],
        badges: [
          {
            text: 'Service Offline',
            type: 'error',
            icon: '🔴'
          }
        ]
      };
    }
  };

  // Remove the handleKeyPress function as we're handling it inline now

  const toggleMinimize = () => {
    setIsMinimized(!isMinimized);
    if (isMinimized) {
      setHasUnreadMessages(false);
    }
  };

  const toggleDocked = () => {
    setIsDocked(!isDocked);
  };

  const openAssistant = () => {
    setIsOpen(true);
    setIsMinimized(false);
    setHasUnreadMessages(false);
  };

  const closeAssistant = () => {
    setIsOpen(false);
    setIsMinimized(false);
    setIsDocked(false);
  };

  const clearConversationHistory = async () => {
    const API_BASE_URL = import.meta.env.VITE_ANALYTICS_API_URL || import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001';

    // For AWS backend, clear local UI state and start new session
    if (API_BASE_URL.includes('amazonaws.com') || API_BASE_URL === 'https://api.example.com') {
      setMessages([]);
      // Clear persisted conversation history
      localStorage.removeItem('analytics-assistant-messages');
      // Generate new session ID to start fresh conversation in AgentCore Memory
      const newSessionId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      setSessionId(newSessionId);
      localStorage.setItem('analytics-assistant-session-id', newSessionId);
      return;
    }

    try {

      // Get auth headers if available
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      try {
        const { getCurrentUser } = await import('aws-amplify/auth');
        const user = await getCurrentUser();
        if (user) {
          const { fetchAuthSession } = await import('aws-amplify/auth');
          const session = await fetchAuthSession();
          const token = session.tokens?.idToken?.toString();
          if (token) {
            headers['Authorization'] = `Bearer ${token}`;
          }
        }
      } catch (authError) {
        console.log('No auth available for conversation clearing:', authError);
      }

      // Skip conversation clearing for AWS backend - not implemented
      if (API_BASE_URL.includes('amazonaws.com') || API_BASE_URL === 'https://api.example.com') {
        setMessages([]);
        return;
      }
      await fetch(`${API_BASE_URL}/api/analytics/conversation/clear`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          session_id: sessionId,
          user_context: {
            publisher_id: 'demo_publisher',
            access_level: 'admin'
          }
        })
      });

      // Clear local state
      setMessages([]);
      setConversationIndicator({ hasHistory: false, messageCount: 0 });

      // Generate new contextual greeting
      if (pageContext) {
        generateContextualGreeting(pageContext);
      }
    } catch (error) {
      console.log('Could not clear conversation history:', error);
    }
  };

  // Floating button styles based on position
  const getFloatingButtonStyles = () => {
    const baseStyles = {
      position: 'fixed' as const,
      zIndex: 1000,
      margin: '20px'
    };

    switch (position) {
      case 'bottom-right':
        return { ...baseStyles, bottom: 0, right: 0 };
      case 'bottom-left':
        return { ...baseStyles, bottom: 0, left: 0 };
      case 'top-right':
        return { ...baseStyles, top: 0, right: 0 };
      case 'top-left':
        return { ...baseStyles, top: 0, left: 0 };
      default:
        return { ...baseStyles, bottom: 0, right: 0 };
    }
  };

  // Render floating button when assistant is closed
  if (!isOpen) {
    return (
      <div style={getFloatingButtonStyles()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Button
            variant="primary"
            iconName="contact"
            onClick={openAssistant}
            ariaLabel="Open Analytics Assistant"
          >
            {hasUnreadMessages && (
              <Badge color="red">
                New
              </Badge>
            )}
            {conversationIndicator.hasHistory && !hasUnreadMessages && (
              <Badge color="blue">
                {conversationIndicator.messageCount} msgs
              </Badge>
            )}
            Analytics Assistant
          </Button>
          <Button
            variant="normal"
            iconName="view-vertical"
            onClick={() => {
              setIsDocked(true);
              openAssistant();
            }}
            ariaLabel="Open Analytics Assistant (Docked)"
          />
        </div>
      </div>
    );
  }

  // Render minimized indicator
  if (isMinimized) {
    return (
      <div style={getFloatingButtonStyles()}>
        <Button
          variant="normal"
          iconName="contact"
          onClick={toggleMinimize}
          ariaLabel="Restore Analytics Assistant"
        >
          {hasUnreadMessages && (
            <Badge color="red">
              {messages.filter(m => m.type === 'assistant').length}
            </Badge>
          )}
          {conversationIndicator.hasHistory && !hasUnreadMessages && (
            <Badge color="blue">
              {conversationIndicator.messageCount}
            </Badge>
          )}
          Analytics Assistant
        </Button>
      </div>
    );
  }

  // Render docked panel
  if (isDocked) {
    return (
      <div
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          width: '400px',
          height: '100vh',
          backgroundColor: '#ffffff',
          borderLeft: '1px solid #e9ecef',
          boxShadow: '-2px 0 8px rgba(0,0,0,0.1)',
          zIndex: 1000,
          display: 'flex',
          flexDirection: 'column',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.3s ease-in-out'
        }}
      >
        {/* Compact Docked Header */}
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid #e9ecef',
            backgroundColor: '#fafafa',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            minHeight: '60px'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Icon name="contact" />
            <span style={{ fontSize: '14px', fontWeight: '600' }}>Analytics Assistant</span>
            {pageContext && (
              <Badge color="blue">{pageContext.pageName}</Badge>
            )}
          </div>
          <div style={{ display: 'flex', gap: '4px' }}>
            {conversationIndicator.hasHistory && (
              <Button
                variant="icon"
                iconName="refresh"
                onClick={clearConversationHistory}
                ariaLabel="Clear conversation history"
              />
            )}
            <Button
              variant="icon"
              iconName="view-full"
              onClick={toggleDocked}
              ariaLabel="Switch to modal view"
            />
            <Button
              variant="icon"
              iconName="remove"
              onClick={toggleMinimize}
              ariaLabel="Minimize"
            />
            <Button
              variant="icon"
              iconName="close"
              onClick={closeAssistant}
              ariaLabel="Close"
            />
          </div>
        </div>

        {/* Optimized Docked Content */}
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          minHeight: 0
        }}>
          {/* Enhanced Compact Context Indicator */}
          {pageContext && (
            <div style={{
              padding: '8px 16px',
              backgroundColor: '#f0f8ff',
              borderBottom: '1px solid #e9ecef',
              fontSize: '11px',
              color: '#0073bb',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '8px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span>📍 {pageContext.pageName}</span>
                {pageContext.currentFilters && Object.keys(pageContext.currentFilters).length > 0 && (
                  <span style={{
                    backgroundColor: '#e6f3ff',
                    padding: '2px 6px',
                    borderRadius: '10px',
                    fontSize: '10px'
                  }}>
                    🔍 {Object.keys(pageContext.currentFilters).length} filter{Object.keys(pageContext.currentFilters).length !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              <div style={{ fontSize: '10px', color: '#666' }}>
                Context-aware help available
              </div>
            </div>
          )}

          {/* Chat Messages - Main scrollable area */}
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            minHeight: 0,
            padding: '8px 16px'
          }}>
            <div style={{
              flex: 1,
              overflowY: 'auto',
              overflowX: 'hidden',
              padding: '12px',
              backgroundColor: '#fafafa',
              borderRadius: '6px',
              border: '1px solid #e9ecef'
            }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {messages.map((message) => (
                  <div
                    key={message.id}
                    style={{
                      display: 'flex',
                      justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start'
                    }}
                  >
                    <div
                      style={{
                        maxWidth: '90%',
                        padding: '8px 12px',
                        borderRadius: '12px',
                        backgroundColor: message.type === 'user' ? '#0073bb' : '#ffffff',
                        color: message.type === 'user' ? '#ffffff' : '#000000',
                        border: message.type === 'assistant' ? '1px solid #e9ecef' : 'none',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                        fontSize: '13px',
                        lineHeight: '1.4'
                      }}
                    >
                      <MarkdownContent content={message.content} />

                      {/* Enhanced rich message metadata rendering */}
                      {message.type === 'assistant' && message.metadata && (
                        <div style={{ marginTop: '6px' }}>
                          {/* Message badges */}
                          {message.metadata.badges && (
                            <MessageBadgesRenderer badges={message.metadata.badges} />
                          )}

                          {/* Context indicator - removed to avoid repetitive context messages */}

                          {/* Charts - compact version */}
                          {message.metadata.charts && message.metadata.charts.length > 0 && (
                            <div style={{ marginTop: '4px' }}>
                              {message.metadata.charts.slice(0, 1).map((chart, index) => (
                                <div key={index} style={{
                                  fontSize: '10px',
                                  backgroundColor: '#f0f8ff',
                                  border: '1px solid #b3d9ff',
                                  borderRadius: '4px',
                                  padding: '4px 6px',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '4px'
                                }}>
                                  <span>📊</span>
                                  <span>{chart.title}</span>
                                  {chart.type === 'metric' && chart.data.value && (
                                    <span style={{ fontWeight: 'bold', color: '#0073bb' }}>
                                      {chart.data.value}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Compact insights */}
                          {message.metadata.insights && (
                            <div style={{ marginTop: '4px' }}>
                              <div style={{
                                fontSize: '9px',
                                backgroundColor: '#f0f8f0',
                                color: '#2d5a2d',
                                padding: '2px 4px',
                                borderRadius: '6px',
                                border: '1px solid #b3d9b3',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px'
                              }}>
                                <span>🎯</span>
                                <span>{message.metadata.insights.type} insight</span>
                                {message.metadata.insights.confidence && (
                                  <span>({Math.round(message.metadata.insights.confidence * 100)}%)</span>
                                )}
                              </div>
                            </div>
                          )}

                          {/* Compact suggestions */}
                          {message.metadata.suggestions && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '4px' }}>
                              <div style={{ fontSize: '9px', color: '#666' }}>💡 Try:</div>
                              {message.metadata.suggestions.slice(0, 2).map((suggestion, index) => (
                                <button
                                  key={index}
                                  onClick={() => sendMessageWithText(suggestion)}
                                  style={{
                                    background: 'none',
                                    border: '1px solid #0073bb',
                                    color: '#0073bb',
                                    padding: '3px 6px',
                                    borderRadius: '4px',
                                    fontSize: '10px',
                                    cursor: 'pointer',
                                    textAlign: 'left'
                                  }}
                                >
                                  {suggestion}
                                </button>
                              ))}
                            </div>
                          )}

                          {/* Compact page actions */}
                          {message.metadata.pageActions && message.metadata.pageActions.length > 0 && (
                            <div style={{ marginTop: '4px' }}>
                              <div style={{ fontSize: '9px', color: '#666', marginBottom: '2px' }}>🎯 Actions:</div>
                              {message.metadata.pageActions.slice(0, 2).map((action, index) => (
                                <button
                                  key={index}
                                  onClick={() => {
                                    if (action.url) {
                                      window.open(action.url, '_blank');
                                    } else {
                                      sendMessageWithText(action.action);
                                    }
                                  }}
                                  style={{
                                    display: 'block',
                                    width: '100%',
                                    background: 'none',
                                    border: '1px solid #e9ecef',
                                    color: '#0073bb',
                                    padding: '2px 6px',
                                    borderRadius: '4px',
                                    fontSize: '10px',
                                    cursor: 'pointer',
                                    textAlign: 'left',
                                    marginBottom: '2px'
                                  }}
                                >
                                  {action.icon || '⚡'} {action.label}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      <div
                        style={{
                          fontSize: '10px',
                          color: message.type === 'user' ? 'rgba(255,255,255,0.7)' : '#666',
                          textAlign: message.type === 'user' ? 'right' : 'left',
                          marginTop: '4px'
                        }}
                      >
                        {message.timestamp instanceof Date ? message.timestamp.toLocaleTimeString() : new Date(message.timestamp).toLocaleTimeString()}
                      </div>
                    </div>
                  </div>
                ))}

                {/* Loading indicator */}
                {isLoading && (
                  <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: '12px',
                        backgroundColor: '#ffffff',
                        border: '1px solid #e9ecef',
                        fontSize: '13px'
                      }}
                    >
                      <StatusIndicator type="loading">
                        Analyzing...
                      </StatusIndicator>
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            </div>
          </div>

          {/* Fixed Input Area at bottom */}
          <div style={{
            padding: '8px 16px 16px 16px',
            borderTop: '1px solid #e9ecef',
            backgroundColor: '#ffffff',
            flexShrink: 0
          }}>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
              <div style={{ flex: 1 }}>
                <Textarea
                  value={inputValue}
                  onChange={({ detail }) => setInputValue(detail.value)}
                  onKeyDown={({ detail }) => {
                    if (detail.key === 'Enter' && !detail.shiftKey) {
                      handleSendMessage();
                    }
                  }}
                  placeholder="Ask me about your analytics..."
                  rows={2}
                  disabled={isLoading}
                />
              </div>
              <Button
                variant="primary"
                iconName="angle-right"
                onClick={handleSendMessage}
                disabled={!inputValue.trim() || isLoading}
                ariaLabel="Send message"
              >
                Send
              </Button>
            </div>

            {/* Collapsible Quick Actions */}
            {pageContext?.availableActions && (
              <details style={{ marginTop: '8px' }}>
                <summary style={{
                  fontSize: '12px',
                  color: '#666',
                  cursor: 'pointer',
                  padding: '4px 0'
                }}>
                  Quick Actions
                </summary>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: '4px',
                  marginTop: '4px'
                }}>
                  {pageContext.availableActions.slice(0, 4).map((action, index) => (
                    <button
                      key={index}
                      onClick={() => sendMessageWithText(`Help me ${action.toLowerCase()}`)}
                      style={{
                        padding: '6px 8px',
                        fontSize: '11px',
                        border: '1px solid #e9ecef',
                        borderRadius: '4px',
                        backgroundColor: '#ffffff',
                        cursor: 'pointer',
                        textAlign: 'center'
                      }}
                    >
                      {action}
                    </button>
                  ))}
                </div>
              </details>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Render full chat interface (modal)
  return (
    <Modal
      onDismiss={closeAssistant}
      visible={isOpen}
      header={
        <Header
          variant="h2"
          actions={
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              {conversationIndicator.hasHistory && (
                <Button
                  key="refresh"
                  variant="icon"
                  iconName="refresh"
                  onClick={clearConversationHistory}
                  ariaLabel="Clear conversation history"
                />
              )}
              <Button
                key="docked"
                variant="icon"
                iconName="view-vertical"
                onClick={toggleDocked}
                ariaLabel="Switch to docked view"
              />
              <Button
                key="minimize"
                variant="icon"
                iconName="remove"
                onClick={toggleMinimize}
                ariaLabel="Minimize"
              />
              <Button
                key="close"
                variant="icon"
                iconName="close"
                onClick={closeAssistant}
                ariaLabel="Close"
              />
            </div>
          }
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Icon name="contact" />
            <span>Analytics Assistant</span>
            {pageContext && (
              <Badge color="blue">{pageContext.pageName}</Badge>
            )}
          </div>
        </Header>
      }
      size="large"
    >
      <SpaceBetween size="m">
        {/* Enhanced Page Context Indicator */}
        {pageContext && (
          <Container>
            <SpaceBetween direction="vertical" size="xs">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <Badge color="blue" size="large">
                  📍 {pageContext.pageName}
                </Badge>
                {pageContext.currentFilters && Object.keys(pageContext.currentFilters).length > 0 && (
                  <Badge color="green">
                    🔍 {Object.keys(pageContext.currentFilters).length} filter{Object.keys(pageContext.currentFilters).length !== 1 ? 's' : ''} active
                  </Badge>
                )}
                {pageContext.availableActions && pageContext.availableActions.length > 0 && (
                  <Badge color="grey">
                    ⚡ {pageContext.availableActions.length} action{pageContext.availableActions.length !== 1 ? 's' : ''} available
                  </Badge>
                )}
                {conversationIndicator.hasHistory && (
                  <Badge color="blue">
                    💬 {conversationIndicator.messageCount} message{conversationIndicator.messageCount !== 1 ? 's' : ''} in history
                  </Badge>
                )}
              </div>

              <Alert
                type="info"
                header="Context-Aware Assistant"
              >
                <SpaceBetween direction="vertical" size="xs">
                  <div>
                    I understand you're working with <strong>{pageContext.pageType}</strong> data.
                    I can provide insights, answer questions, and suggest actions relevant to your current context.
                  </div>

                  {pageContext.currentFilters && Object.keys(pageContext.currentFilters).length > 0 && (
                    <div style={{ fontSize: '12px', color: '#666' }}>
                      <strong>Active filters:</strong> {Object.entries(pageContext.currentFilters)
                        .map(([key, value]) => `${key}=${value}`)
                        .join(', ')}
                    </div>
                  )}
                </SpaceBetween>
              </Alert>
            </SpaceBetween>
          </Container>
        )}

        {/* Chat Messages */}
        <Container>
          <div style={{
            height: '400px',
            overflowY: 'auto',
            padding: '16px',
            border: '1px solid #e9ecef',
            borderRadius: '8px',
            backgroundColor: '#fafafa'
          }}>
            <SpaceBetween size="m">
              {messages.map((message) => (
                <div
                  key={message.id}
                  style={{
                    display: 'flex',
                    justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start'
                  }}
                >
                  <div
                    style={{
                      maxWidth: '80%',
                      padding: '12px 16px',
                      borderRadius: '12px',
                      backgroundColor: message.type === 'user' ? '#0073bb' : '#ffffff',
                      color: message.type === 'user' ? '#ffffff' : '#000000',
                      border: message.type === 'assistant' ? '1px solid #e9ecef' : 'none',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                    }}
                  >
                    <Box fontSize="body-m">
                      <MarkdownContent content={message.content} />
                    </Box>

                    {/* Enhanced rich message metadata rendering for modal view */}
                    {message.type === 'assistant' && message.metadata && (
                      <div style={{ marginTop: '12px' }}>
                        {/* Message badges */}
                        {message.metadata.badges && (
                          <MessageBadgesRenderer badges={message.metadata.badges} />
                        )}

                        {/* Context badge for the message */}
                        {/* Page context indicator - removed to avoid repetitive context messages */}

                        {/* Rich insights rendering */}
                        {message.metadata.insights && (
                          <InsightRenderer insights={message.metadata.insights} />
                        )}

                        {/* Charts rendering */}
                        {message.metadata.charts && message.metadata.charts.length > 0 && (
                          <div style={{ marginTop: '8px' }}>
                            {message.metadata.charts.map((chart, index) => (
                              <ChartRenderer key={index} chart={chart} />
                            ))}
                          </div>
                        )}

                        {/* Page actions */}
                        {message.metadata.pageActions && message.metadata.pageActions.length > 0 && (
                          <PageActionsRenderer actions={message.metadata.pageActions} />
                        )}

                        {/* Suggestions */}
                        {message.metadata.suggestions && (
                          <div style={{ marginTop: '8px' }}>
                            <div style={{ fontSize: '12px', color: '#666', marginBottom: '6px' }}>
                              💡 Try asking:
                            </div>
                            <SpaceBetween direction="horizontal" size="xs">
                              {message.metadata.suggestions.slice(0, 4).map((suggestion, index) => (
                                <Button
                                  key={index}
                                  variant="inline-link"
                                  size="small"
                                  onClick={() => sendMessageWithText(suggestion)}
                                >
                                  {suggestion}
                                </Button>
                              ))}
                            </SpaceBetween>
                          </div>
                        )}
                      </div>
                    )}

                    <Box
                      fontSize="body-s"
                      color="text-body-secondary"
                      textAlign={message.type === 'user' ? 'right' : 'left'}
                      margin={{ top: 'xs' }}
                    >
                      {message.timestamp instanceof Date ? message.timestamp.toLocaleTimeString() : new Date(message.timestamp).toLocaleTimeString()}
                    </Box>
                  </div>
                </div>
              ))}

              {/* Loading indicator */}
              {isLoading && (
                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <div
                    style={{
                      padding: '12px 16px',
                      borderRadius: '12px',
                      backgroundColor: '#ffffff',
                      border: '1px solid #e9ecef'
                    }}
                  >
                    <StatusIndicator type="loading">
                      Analyzing your request...
                    </StatusIndicator>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </SpaceBetween>
          </div>
        </Container>

        {/* Input Area */}
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <Textarea
              value={inputValue}
              onChange={({ detail }) => setInputValue(detail.value)}
              onKeyDown={({ detail }) => {
                if (detail.key === 'Enter' && !detail.shiftKey) {
                  handleSendMessage();
                }
              }}
              placeholder="Ask me about your analytics, content performance, or bot activity..."
              rows={2}
              disabled={isLoading}

            />
          </div>
          <Button
            variant="primary"
            iconName="angle-right"
            onClick={handleSendMessage}
            disabled={!inputValue.trim() || isLoading}
            ariaLabel="Send message"
          >
            Send
          </Button>
        </div>

        {/* Enhanced Quick Actions with Navigation Integration */}
        {pageContext?.availableActions && (
          <Container>
            <Header
              variant="h3"
              description={`Context-aware actions for ${pageContext.pageName}. Click to ask me about these topics or navigate to relevant sections.`}
            >
              Quick Actions & Navigation
            </Header>
            <SpaceBetween direction="vertical" size="s">
              {/* Primary Actions */}
              <div>
                <Box fontSize="body-s" fontWeight="bold" margin={{ bottom: 'xs' }}>
                  Primary Actions:
                </Box>
                <SpaceBetween direction="horizontal" size="xs">
                  {pageContext.availableActions.slice(0, 3).map((action, index) => (
                    <Button
                      key={index}
                      variant="primary"
                      size="small"
                      onClick={() => sendMessageWithText(`Help me ${action.toLowerCase()}`)}
                    >
                      {action}
                    </Button>
                  ))}
                </SpaceBetween>
              </div>

              {/* Secondary Actions */}
              {pageContext.availableActions.length > 3 && (
                <div>
                  <Box fontSize="body-s" fontWeight="bold" margin={{ bottom: 'xs' }}>
                    Additional Actions:
                  </Box>
                  <SpaceBetween direction="horizontal" size="xs">
                    {pageContext.availableActions.slice(3, 6).map((action, index) => (
                      <Button
                        key={index + 3}
                        variant="normal"
                        size="small"
                        onClick={() => sendMessageWithText(`Help me ${action.toLowerCase()}`)}
                      >
                        {action}
                      </Button>
                    ))}
                  </SpaceBetween>
                </div>
              )}

              {/* Navigation Suggestions */}
              <div>
                <Box fontSize="body-s" fontWeight="bold" margin={{ bottom: 'xs' }}>
                  Navigation Help:
                </Box>
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    variant="inline-link"
                    size="small"
                    onClick={() => sendMessageWithText(`How do I navigate from ${pageContext.pageName}?`)}
                  >
                    🧭 Navigation Help
                  </Button>
                  <Button
                    variant="inline-link"
                    size="small"
                    onClick={() => sendMessageWithText(`What can I do on the ${pageContext.pageName} page?`)}
                  >
                    ❓ Page Guide
                  </Button>
                  <Button
                    variant="inline-link"
                    size="small"
                    onClick={() => sendMessageWithText(`Show me related pages to ${pageContext.pageName}`)}
                  >
                    🔗 Related Pages
                  </Button>
                </SpaceBetween>
              </div>
            </SpaceBetween>
          </Container>
        )}
      </SpaceBetween>
    </Modal>
  );
}