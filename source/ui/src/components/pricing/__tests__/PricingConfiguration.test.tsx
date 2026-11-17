/**
 * Pricing Configuration Component Tests
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import PricingConfiguration from '../../../pages/PricingConfiguration';
import { pricingService } from '../../../services/pricingService';

// Mock the pricing service
vi.mock('../../../services/pricingService', () => ({
  pricingService: {
    getCurrentRules: vi.fn(),
    createRules: vi.fn(),
    updateRules: vi.fn(),
    getPricingHistory: vi.fn(),
    calculatePricing: vi.fn(),
  },
}));

// Mock the pricing components
vi.mock('../PricingRulesEditor', () => ({
  default: ({ onSave, currentRules }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid="pricing-rules-editor">
      <button onClick={() => onSave({}, '2025-01-01', 'Test change', true)}>
        Save Rules
      </button>
      {currentRules && <div>Current Rules Present</div>}
    </div>
  ),
}));

vi.mock('../PricingHistory', () => ({
  default: ({ onVersionSelect }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid="pricing-history">
      <button onClick={() => onVersionSelect({ version: 1 })}>
        Select Version
      </button>
    </div>
  ),
}));

vi.mock('../PricingPreview', () => ({
  default: ({ pricingRules }: React.PropsWithChildren<Record<string, unknown>>) => (
    <div data-testid="pricing-preview">
      {pricingRules && <div>Preview Available</div>}
    </div>
  ),
}));

describe('PricingConfiguration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pricing configuration page', async () => {
    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(null);

    render(<PricingConfiguration />);

    expect(screen.getByText('Pricing Configuration')).toBeInTheDocument();
    expect(screen.getByText('Configure and manage versioned pricing rules with effective date scheduling')).toBeInTheDocument();
  });

  it('loads current pricing rules on mount', async () => {
    const mockRules = {
      rule_set_id: 'test-rules',
      status: 'active',
      pricing_rules: { per_access: { article: '0.05' } },
      audit_trail: { version: 1, change_reason: 'Initial setup' }
    };

    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(mockRules);

    render(<PricingConfiguration />);

    await waitFor(() => {
      expect(pricingService.getCurrentRules).toHaveBeenCalled();
    });
  });

  it('handles tab navigation', async () => {
    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(null);

    render(<PricingConfiguration />);

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText('Active Pricing Rules')).toBeInTheDocument();
    });

    // Check that tabs are present (they should be rendered by Cloudscape Tabs component)
    expect(screen.getByRole('tablist')).toBeInTheDocument();
  });

  it('handles pricing rules creation', async () => {
    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(null);
    vi.mocked(pricingService.createRules).mockResolvedValue({
      rule_set_id: 'new-rules',
      status: 'active'
    });

    render(<PricingConfiguration />);

    // Switch to editor tab and save rules
    const saveButton = await screen.findByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(pricingService.createRules).toHaveBeenCalledWith({
        pricing_rules: {},
        effective_date: '2025-01-01',
        change_reason: 'Test change',
        activate: true
      });
    });
  });

  it('handles pricing rules update', async () => {
    const mockRules = {
      rule_set_id: 'existing-rules',
      status: 'active',
      pricing_rules: { per_access: { article: '0.05' } },
      audit_trail: { version: 1, change_reason: 'Initial setup' }
    };

    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(mockRules);
    vi.mocked(pricingService.updateRules).mockResolvedValue({
      ...mockRules,
      audit_trail: { version: 2, change_reason: 'Updated' }
    });

    render(<PricingConfiguration />);

    await waitFor(() => {
      expect(screen.getByText('Current Rules Present')).toBeInTheDocument();
    });

    const saveButton = screen.getByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(pricingService.updateRules).toHaveBeenCalledWith({
        pricing_rules: {},
        effective_date: '2025-01-01',
        change_reason: 'Test change',
        rule_set_id: 'existing-rules'
      });
    });
  });

  it('displays error messages', async () => {
    vi.mocked(pricingService.getCurrentRules).mockRejectedValue(new Error('API Error'));

    render(<PricingConfiguration />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load current pricing rules. Please try again.')).toBeInTheDocument();
    });
  });

  it('displays success messages', async () => {
    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(null);
    vi.mocked(pricingService.createRules).mockResolvedValue({
      rule_set_id: 'new-rules',
      status: 'active'
    });

    render(<PricingConfiguration />);

    const saveButton = await screen.findByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(screen.getByText('Pricing rules created and activated successfully!')).toBeInTheDocument();
    });
  });

  it('handles version selection from history', async () => {
    vi.mocked(pricingService.getCurrentRules).mockResolvedValue(null);

    render(<PricingConfiguration />);

    const selectVersionButton = await screen.findByText('Select Version');
    fireEvent.click(selectVersionButton);

    // Version selection should work without errors
    expect(selectVersionButton).toBeInTheDocument();
  });
});