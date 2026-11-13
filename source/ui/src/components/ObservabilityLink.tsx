/**
 * ObservabilityLink Component
 * 
 * Provides deep links to AWS CloudWatch GenAI Observability for AgentCore traces and metrics.
 * This component generates and displays links to specific agent sessions and technical traces.
 */

import React from 'react';
import { Button, Box, SpaceBetween, StatusIndicator } from '@cloudscape-design/components';

interface ObservabilityLinkProps {
  /** Agent ARN for the AgentCore deployment */
  agentArn?: string;
  /** Session ID for specific session traces */
  sessionId?: string;
  /** Type of observability view to link to */
  viewType?: 'traces' | 'metrics' | 'logs' | 'dashboard';
  /** Button variant */
  variant?: 'primary' | 'normal' | 'link' | 'icon';
  /** Button size */
  size?: 'small' | 'normal' | 'large';
  /** Custom label for the button */
  label?: string;
  /** Whether to show the link as disabled if configuration is missing */
  showDisabledIfMissing?: boolean;
}

interface ObservabilityConfig {
  region: string;
  cloudWatchUrl: string;
  agentId?: string;
  runtimeId?: string;
}

export default function ObservabilityLink({
  agentArn,
  sessionId,
  viewType = 'traces',
  variant = 'normal',
  size = 'normal',
  label,
  showDisabledIfMissing = true
}: ObservabilityLinkProps) {
  
  // Get observability configuration from environment variables
  const getObservabilityConfig = (): ObservabilityConfig | null => {
    const region = import.meta.env.VITE_AWS_REGION || 'us-west-2';
    const cloudWatchUrl = import.meta.env.VITE_CLOUDWATCH_GENAI_URL;
    
    // If no CloudWatch URL is configured, construct default
    const baseCloudWatchUrl = cloudWatchUrl || `https://${region}.console.aws.amazon.com/cloudwatch`;
    
    // Extract agent ID from ARN if provided
    let agentId: string | undefined;
    let runtimeId: string | undefined;
    
    if (agentArn) {
      // Parse AgentCore ARN format: arn:aws:bedrock-agentcore:region:account:agent/agent-id/runtime/runtime-id
      const arnParts = agentArn.split('/');
      if (arnParts.length >= 2) {
        agentId = arnParts[1];
        if (arnParts.length >= 4) {
          runtimeId = arnParts[3];
        }
      }
    }
    
    return {
      region,
      cloudWatchUrl: baseCloudWatchUrl,
      agentId,
      runtimeId
    };
  };

  // Generate deep link URL based on view type and parameters
  const generateObservabilityUrl = (config: ObservabilityConfig): string => {
    const { cloudWatchUrl, region, agentId, runtimeId } = config;
    
    // Base CloudWatch GenAI Observability URL
    const baseUrl = `${cloudWatchUrl}/genai`;
    
    switch (viewType) {
      case 'traces':
        if (agentId && sessionId) {
          // Link to specific session traces
          return `${baseUrl}/traces?region=${region}&agentId=${agentId}&sessionId=${sessionId}`;
        } else if (agentId) {
          // Link to agent traces overview
          return `${baseUrl}/traces?region=${region}&agentId=${agentId}`;
        } else {
          // Link to general traces view
          return `${baseUrl}/traces?region=${region}`;
        }
        
      case 'metrics':
        if (agentId) {
          // Link to agent-specific metrics
          return `${baseUrl}/metrics?region=${region}&agentId=${agentId}`;
        } else {
          // Link to general metrics view
          return `${baseUrl}/metrics?region=${region}`;
        }
        
      case 'logs':
        if (agentId && runtimeId) {
          // Link to agent runtime logs
          return `${cloudWatchUrl}/logs/log-groups/log-group/%2Faws%2Fbedrock-agentcore%2F${agentId}%2F${runtimeId}?region=${region}`;
        } else if (agentId) {
          // Link to agent logs
          return `${cloudWatchUrl}/logs/log-groups?region=${region}&prefix=%2Faws%2Fbedrock-agentcore%2F${agentId}`;
        } else {
          // Link to general AgentCore logs
          return `${cloudWatchUrl}/logs/log-groups?region=${region}&prefix=%2Faws%2Fbedrock-agentcore`;
        }
        
      case 'dashboard':
        if (agentId) {
          // Link to agent-specific dashboard
          return `${baseUrl}/dashboard?region=${region}&agentId=${agentId}`;
        } else {
          // Link to general GenAI dashboard
          return `${baseUrl}/dashboard?region=${region}`;
        }
        
      default:
        return `${baseUrl}?region=${region}`;
    }
  };

  // Get default label based on view type
  const getDefaultLabel = (): string => {
    switch (viewType) {
      case 'traces':
        return sessionId ? 'View Session Traces' : 'View Technical Traces';
      case 'metrics':
        return 'View Metrics';
      case 'logs':
        return 'View Logs';
      case 'dashboard':
        return 'View Dashboard';
      default:
        return 'View Observability';
    }
  };

  // Get icon for the view type
  const getViewTypeIcon = (): string => {
    switch (viewType) {
      case 'traces':
        return 'timeline';
      case 'metrics':
        return 'analytics';
      case 'logs':
        return 'file-text';
      case 'dashboard':
        return 'dashboard';
      default:
        return 'external';
    }
  };

  const config = getObservabilityConfig();
  const isConfigured = config && (config.cloudWatchUrl || config.agentId);
  const displayLabel = label || getDefaultLabel();

  // If not configured and we should show disabled state
  if (!isConfigured && showDisabledIfMissing) {
    return (
      <Button
        variant={variant}
        size={size}
        disabled={true}
        iconName={getViewTypeIcon()}
      >
        {displayLabel} (Not Configured)
      </Button>
    );
  }

  // If not configured and we shouldn't show disabled state, don't render
  if (!isConfigured) {
    return null;
  }

  const observabilityUrl = generateObservabilityUrl(config!);

  const handleClick = () => {
    // Open in new tab
    window.open(observabilityUrl, '_blank', 'noopener,noreferrer');
  };

  return (
    <Button
      variant={variant}
      size={size}
      iconName={getViewTypeIcon()}
      onClick={handleClick}
    >
      {displayLabel}
    </Button>
  );
}

// Utility component for multiple observability links
interface ObservabilityLinksProps {
  agentArn?: string;
  sessionId?: string;
  showTraces?: boolean;
  showMetrics?: boolean;
  showLogs?: boolean;
  showDashboard?: boolean;
  variant?: 'primary' | 'normal' | 'link' | 'icon';
  size?: 'small' | 'normal' | 'large';
}

export function ObservabilityLinks({
  agentArn,
  sessionId,
  showTraces = true,
  showMetrics = false,
  showLogs = false,
  showDashboard = false,
  variant = 'normal',
  size = 'small'
}: ObservabilityLinksProps) {
  const links = [];

  if (showTraces) {
    links.push(
      <ObservabilityLink
        key="traces"
        agentArn={agentArn}
        sessionId={sessionId}
        viewType="traces"
        variant={variant}
        size={size}
      />
    );
  }

  if (showMetrics) {
    links.push(
      <ObservabilityLink
        key="metrics"
        agentArn={agentArn}
        sessionId={sessionId}
        viewType="metrics"
        variant={variant}
        size={size}
      />
    );
  }

  if (showLogs) {
    links.push(
      <ObservabilityLink
        key="logs"
        agentArn={agentArn}
        sessionId={sessionId}
        viewType="logs"
        variant={variant}
        size={size}
      />
    );
  }

  if (showDashboard) {
    links.push(
      <ObservabilityLink
        key="dashboard"
        agentArn={agentArn}
        sessionId={sessionId}
        viewType="dashboard"
        variant={variant}
        size={size}
      />
    );
  }

  if (links.length === 0) {
    return null;
  }

  if (links.length === 1) {
    return <>{links[0]}</>;
  }

  return (
    <SpaceBetween direction="horizontal" size="xs">
      {links}
    </SpaceBetween>
  );
}

// Utility component for observability status indicator
interface ObservabilityStatusProps {
  agentArn?: string;
  showConfigStatus?: boolean;
}

export function ObservabilityStatus({
  agentArn,
  showConfigStatus = true
}: ObservabilityStatusProps) {
  const region = import.meta.env.VITE_AWS_REGION || 'us-west-2';
  const cloudWatchUrl = import.meta.env.VITE_CLOUDWATCH_GENAI_URL;
  const isConfigured = !!(cloudWatchUrl || agentArn);

  if (!showConfigStatus) {
    return null;
  }

  return (
    <Box>
      <SpaceBetween direction="horizontal" size="xs">
        <StatusIndicator type={isConfigured ? 'success' : 'warning'}>
          {isConfigured ? 'Observability Configured' : 'Observability Not Configured'}
        </StatusIndicator>
        {isConfigured && (
          <span style={{ fontSize: '12px', color: '#5f6b7a' }}>
            Region: {region}
          </span>
        )}
      </SpaceBetween>
    </Box>
  );
}