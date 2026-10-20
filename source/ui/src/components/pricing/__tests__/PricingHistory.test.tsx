/**
 * Pricing History Component Tests
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import PricingHistory from '../PricingHistory';
import { pricingService } from '../../../services/pricingService';

// Mock the pricing service
vi.mock('../../../services/pricingService', () => ({
  pricingService: {
    getPricingHistory: vi.fn(),
  },
}));

// Mock the PricingPreview component
vi.mock('../PricingPreview', () => ({
  default: ({ pricingRules, readonly }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid="pricing-preview">
      {readonly && <div>Readonly Preview</div>}
      {pricingRules && <div>Preview with Rules</div>}
    </div>
  ),
}));

describe('PricingHistory', () => {
  const mockOnVersionSelect = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pricing history component', async () => {
    vi.mocked(pricingService.getPricingHistory).mockResolvedValue([]);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    expect(screen.getByText('Pricing Rule History')).toBeInTheDocument();
    expect(screen.getByText('View version history and audit trail for pricing rules')).toBeInTheDocument();
  });

  it('loads and displays pricing history', async () => {
    const mockHistory = [
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2025-01-01T00:00:00Z',
        effective_date: '2025-01-01T00:00:00Z',
        status: 'active',
        created_by: 'admin',
        created_at: '2025-01-01T00:00:00Z',
        pricing_rules: {
          per_access: { article: '0.05' }
        },
        audit_trail: {
          version: 2,
          change_reason: 'Updated pricing'
        }
      },
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2024-12-01T00:00:00Z',
        effective_date: '2024-12-01T00:00:00Z',
        status: 'inactive',
        created_by: 'admin',
        created_at: '2024-12-01T00:00:00Z',
        pricing_rules: {
          per_access: { article: '0.04' }
        },
        audit_trail: {
          version: 1,
          change_reason: 'Initial setup'
        }
      }
    ];

    vi.mocked(pricingService.getPricingHistory).mockResolvedValue(mockHistory);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('v2')).toBeInTheDocument();
      expect(screen.getByText('v1')).toBeInTheDocument();
      expect(screen.getByText('Updated pricing')).toBeInTheDocument();
      expect(screen.getByText('Initial setup')).toBeInTheDocument();
    });

    expect(screen.getByText('Version History')).toBeInTheDocument();
    expect(screen.getByText('(2)')).toBeInTheDocument(); // Counter
  });

  it('handles empty history', async () => {
    vi.mocked(pricingService.getPricingHistory).mockResolvedValue([]);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('No pricing history found')).toBeInTheDocument();
      expect(screen.getByText('Create your first pricing rules to see version history.')).toBeInTheDocument();
    });
  });

  it('handles version selection', async () => {
    const mockHistory = [
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2025-01-01T00:00:00Z',
        effective_date: '2025-01-01T00:00:00Z',
        status: 'active',
        created_by: 'admin',
        created_at: '2025-01-01T00:00:00Z',
        pricing_rules: {
          per_access: { article: '0.05' }
        },
        audit_trail: {
          version: 1,
          change_reason: 'Initial setup'
        }
      }
    ];

    vi.mocked(pricingService.getPricingHistory).mockResolvedValue(mockHistory);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('View Details')).toBeInTheDocument();
    });

    const viewDetailsButton = screen.getByText('View Details');
    fireEvent.click(viewDetailsButton);

    expect(mockOnVersionSelect).toHaveBeenCalledWith(mockHistory[0]);
  });

  it('opens version modal', async () => {
    const mockHistory = [
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2025-01-01T00:00:00Z',
        effective_date: '2025-01-01T00:00:00Z',
        status: 'active',
        created_by: 'admin',
        created_at: '2025-01-01T00:00:00Z',
        pricing_rules: {
          per_access: { article: '0.05' }
        },
        audit_trail: {
          version: 1,
          change_reason: 'Initial setup'
        }
      }
    ];

    vi.mocked(pricingService.getPricingHistory).mockResolvedValue(mockHistory);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('View Details')).toBeInTheDocument();
    });

    const viewDetailsButton = screen.getByText('View Details');
    fireEvent.click(viewDetailsButton);

    await waitFor(() => {
      expect(screen.getByText('Pricing Rules - Version 1')).toBeInTheDocument();
      expect(screen.getByText('Initial setup')).toBeInTheDocument();
      expect(screen.getByTestId('pricing-preview')).toBeInTheDocument();
      expect(screen.getByText('Readonly Preview')).toBeInTheDocument();
    });
  });

  it('handles refresh action', async () => {
    vi.mocked(pricingService.getPricingHistory).mockResolvedValue([]);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('Refresh')).toBeInTheDocument();
    });

    const refreshButton = screen.getByText('Refresh');
    fireEvent.click(refreshButton);

    expect(pricingService.getPricingHistory).toHaveBeenCalledTimes(2); // Initial load + refresh
  });

  it('handles API errors', async () => {
    vi.mocked(pricingService.getPricingHistory).mockRejectedValue(new Error('API Error'));

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load pricing history. Please try again.')).toBeInTheDocument();
    });
  });

  it('displays status indicators correctly', async () => {
    const mockHistory = [
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2025-01-01T00:00:00Z',
        effective_date: '2025-01-01T00:00:00Z',
        status: 'active',
        created_by: 'admin',
        created_at: '2025-01-01T00:00:00Z',
        pricing_rules: { per_access: { article: '0.05' } },
        audit_trail: { version: 1, change_reason: 'Test' }
      },
      {
        rule_set_id: 'test-rules',
        version_timestamp: '2024-12-01T00:00:00Z',
        effective_date: '2024-12-01T00:00:00Z',
        status: 'draft',
        created_by: 'admin',
        created_at: '2024-12-01T00:00:00Z',
        pricing_rules: { per_access: { article: '0.04' } },
        audit_trail: { version: 2, change_reason: 'Test' }
      }
    ];

    vi.mocked(pricingService.getPricingHistory).mockResolvedValue(mockHistory);

    render(<PricingHistory onVersionSelect={mockOnVersionSelect} />);

    await waitFor(() => {
      // Status indicators should be rendered (exact text depends on Cloudscape implementation)
      expect(screen.getByText('active')).toBeInTheDocument();
      expect(screen.getByText('draft')).toBeInTheDocument();
    });
  });
});