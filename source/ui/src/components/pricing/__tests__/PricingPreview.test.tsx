/**
 * Pricing Preview Component Tests
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import PricingPreview from '../PricingPreview';
import { pricingService } from '../../../services/pricingService';

// Mock the pricing service
vi.mock('../../../services/pricingService', () => ({
  pricingService: {
    calculatePricing: vi.fn(),
  },
}));

describe('PricingPreview', () => {
  const mockPricingRules = {
    per_access: {
      article: '0.05',
      report: '0.25',
      document: '0.10'
    },
    multipliers: {
      iab_category: {
        '126': '1.2', // Technology & Computing
        '111': '1.1'  // Medical Health
      },
      recency: {
        '24h': '1.5',
        '7d': '1.2',
        '30d': '1.0'
      }
    }
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pricing preview component', () => {
    render(<PricingPreview pricingRules={mockPricingRules} />);

    expect(screen.getByText('Pricing Preview')).toBeInTheDocument();
    expect(screen.getByText('Preview how pricing rules will be applied')).toBeInTheDocument();
  });

  it('renders in readonly mode', () => {
    render(<PricingPreview pricingRules={mockPricingRules} readonly={true} />);

    expect(screen.getByText('Pricing Preview')).toBeInTheDocument();
    // In readonly mode, there should be no interactive elements like "Calculate Example"
    expect(screen.queryByText('Calculate Example')).not.toBeInTheDocument();
  });

  it('displays pricing rules structure', () => {
    render(<PricingPreview pricingRules={mockPricingRules} />);

    // Should display the pricing structure
    expect(screen.getByText('Per-Access Pricing')).toBeInTheDocument();
    expect(screen.getByText('article: $0.05')).toBeInTheDocument();
    expect(screen.getByText('report: $0.25')).toBeInTheDocument();
    expect(screen.getByText('document: $0.10')).toBeInTheDocument();
  });

  it('displays multipliers', () => {
    render(<PricingPreview pricingRules={mockPricingRules} />);

    expect(screen.getByText('Category Multipliers')).toBeInTheDocument();
    expect(screen.getByText('Technology & Computing (126): 1.2x')).toBeInTheDocument();
    expect(screen.getByText('Medical Health (111): 1.1x')).toBeInTheDocument();

    expect(screen.getByText('Recency Multipliers')).toBeInTheDocument();
    expect(screen.getByText('24h: 1.5x')).toBeInTheDocument();
    expect(screen.getByText('7d: 1.2x')).toBeInTheDocument();
    expect(screen.getByText('30d: 1.0x')).toBeInTheDocument();
  });

  it('handles empty pricing rules', () => {
    render(<PricingPreview pricingRules={{}} />);

    expect(screen.getByText('No pricing examples')).toBeInTheDocument();
    expect(screen.getByText('Configure pricing rules to see examples.')).toBeInTheDocument();
  });

  it('handles null pricing rules', () => {
    render(<PricingPreview pricingRules={null} />);

    expect(screen.getByText('No pricing examples')).toBeInTheDocument();
    expect(screen.getByText('Configure pricing rules to see examples.')).toBeInTheDocument();
  });

  it('calculates pricing examples', async () => {
    const mockCalculation = {
      base_price: 0.05,
      final_price: 0.06,
      multipliers_applied: {
        iab_category: 1.2
      },
      breakdown: {
        base: 0.05,
        category_multiplier: 0.01
      }
    };

    vi.mocked(pricingService.calculatePricing).mockResolvedValue(mockCalculation);

    render(<PricingPreview pricingRules={mockPricingRules} />);

    // Should show calculate button in non-readonly mode
    const calculateButton = screen.getByText('Calculate Example');
    fireEvent.click(calculateButton);

    await waitFor(() => {
      expect(pricingService.calculatePricing).toHaveBeenCalledWith({
        content_type: 'article',
        access_type: 'full'
      });
    });

    // Should display calculation results
    await waitFor(() => {
      expect(screen.getByText('$0.06')).toBeInTheDocument(); // Final price
      expect(screen.getByText('Base: $0.05')).toBeInTheDocument();
    });
  });

  it('handles calculation errors', async () => {
    vi.mocked(pricingService.calculatePricing).mockRejectedValue(new Error('Calculation failed'));

    render(<PricingPreview pricingRules={mockPricingRules} />);

    const calculateButton = screen.getByText('Calculate Example');
    fireEvent.click(calculateButton);

    await waitFor(() => {
      expect(screen.getByText('Failed to calculate pricing example')).toBeInTheDocument();
    });
  });

  it('shows loading state during calculation', async () => {
    const slowCalculation = vi.fn(() => new Promise(resolve => setTimeout(resolve, 100)));
    vi.mocked(pricingService.calculatePricing).mockImplementation(slowCalculation);

    render(<PricingPreview pricingRules={mockPricingRules} />);

    const calculateButton = screen.getByText('Calculate Example');
    fireEvent.click(calculateButton);

    // Should show loading state
    expect(screen.getByText('Calculating...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Calculate Example')).toBeInTheDocument();
    });
  });

  it('allows changing content type for examples', async () => {
    render(<PricingPreview pricingRules={mockPricingRules} />);

    // Should have content type selector
    const contentTypeSelect = screen.getByLabelText('Content Type');
    fireEvent.change(contentTypeSelect, { target: { value: 'report' } });

    const calculateButton = screen.getByText('Calculate Example');
    fireEvent.click(calculateButton);

    await waitFor(() => {
      expect(pricingService.calculatePricing).toHaveBeenCalledWith({
        content_type: 'report',
        access_type: 'full'
      });
    });
  });

  it('displays impact analysis', () => {
    render(<PricingPreview pricingRules={mockPricingRules} />);

    expect(screen.getByText('Impact Analysis')).toBeInTheDocument();
    expect(screen.getByText('Estimated impact on revenue based on current pricing changes')).toBeInTheDocument();
  });
});