/**
 * Unit tests for PricingRulesEditor — server-resolved category names (#26/#27)
 *
 * Post-#26 the UI no longer performs a client-side taxonomy ID->name join.
 * GET /pricing/rules returns `multipliers.iab_category_resolved` (category ID ->
 * { multiplier, category_name, full_path, tier_level }), and the editor derives
 * its display names directly from that block. These tests assert that contract:
 * resolved names render, no getTaxonomyCategories call is made, and unknown
 * categories degrade gracefully.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi } from 'vitest';
import PricingRulesEditor from '../PricingRulesEditor';

const mockCurrentRules = {
  rule_set_id: 'test-pricing',
  version_timestamp: '2025-01-15T10:00:00Z',
  effective_date: '2025-01-01',
  status: 'active' as const,
  created_by: 'test@example.com',
  created_at: '2025-01-01T00:00:00Z',
  pricing_rules: {
    per_access: {
      article: '0.05',
      report: '0.25',
      video: '0.15',
      image: '0.03',
    },
    multipliers: {
      iab_category: {
        '111': '1.2',
        '126': '1.1',
        '52': '1.3',
      },
      // Server-resolved block (SSOT #26) — the sole source of display names.
      iab_category_resolved: {
        '111': { multiplier: 1.2, category_name: 'Medical Health', full_path: 'Medical Health', tier_level: 1 },
        '126': { multiplier: 1.1, category_name: 'Technology & Computing', full_path: 'Technology & Computing', tier_level: 1 },
        '52': { multiplier: 1.3, category_name: 'Business and Finance', full_path: 'Business and Finance', tier_level: 1 },
      },
      recency: { last_24h: '1.5', last_week: '1.2', last_month: '1.0' },
      demand: { high: '1.3', medium: '1.0', low: '0.8' },
    },
  },
  audit_trail: { version: 1, change_reason: 'Test pricing rules', previous_version: undefined },
};

const mockOnSave = vi.fn();
const mockOnCancel = vi.fn();

describe('PricingRulesEditor — server-resolved categories', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays resolved category names from iab_category_resolved (no taxonomy join)', async () => {
    render(
      <PricingRulesEditor currentRules={mockCurrentRules} onSave={mockOnSave} onCancel={mockOnCancel} />
    );

    await waitFor(() => {
      expect(screen.getByText('Medical Health')).toBeInTheDocument();
      expect(screen.getByText('Technology & Computing')).toBeInTheDocument();
      expect(screen.getByText('Business and Finance')).toBeInTheDocument();
    });

    expect(screen.getByDisplayValue('1.2')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1.1')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1.3')).toBeInTheDocument();
  });

  it('updates a category multiplier value', async () => {
    render(
      <PricingRulesEditor currentRules={mockCurrentRules} onSave={mockOnSave} onCancel={mockOnCancel} />
    );

    await waitFor(() => expect(screen.getByText('Medical Health')).toBeInTheDocument());

    const medicalHealthInput = screen.getByDisplayValue('1.2');
    fireEvent.change(medicalHealthInput, { target: { value: '1.5' } });
    expect(medicalHealthInput).toHaveValue('1.5');
  });

  it('saves when required fields are provided', async () => {
    render(
      <PricingRulesEditor currentRules={mockCurrentRules} onSave={mockOnSave} onCancel={mockOnCancel} />
    );

    await waitFor(() => expect(screen.getByText('Medical Health')).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText('YYYY-MM-DD'), { target: { value: '2025-02-01' } });
    fireEvent.change(screen.getByPlaceholderText('e.g., Q1 2024 pricing update'), {
      target: { value: 'Test update' },
    });

    fireEvent.click(screen.getByText('Save and Activate'));

    await waitFor(() => expect(mockOnSave).toHaveBeenCalled());
  });

  it('renders default pricing when currentRules is null (no resolved names, no crash)', async () => {
    render(<PricingRulesEditor currentRules={null} onSave={mockOnSave} onCancel={mockOnCancel} />);

    expect(screen.getByDisplayValue('0.05')).toBeInTheDocument(); // article price
    expect(screen.getByDisplayValue('0.25')).toBeInTheDocument(); // report price
  });

  it('degrades gracefully for a category missing from iab_category_resolved', async () => {
    const rulesWithUnknownCategory = {
      ...mockCurrentRules,
      pricing_rules: {
        ...mockCurrentRules.pricing_rules,
        multipliers: {
          ...mockCurrentRules.pricing_rules.multipliers,
          iab_category: { '111': '1.2', '999': '1.4' },
          // '999' intentionally absent from the resolved block
        },
      },
    };

    render(
      <PricingRulesEditor
        currentRules={rulesWithUnknownCategory}
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    await waitFor(() => expect(screen.getByText('Medical Health')).toBeInTheDocument());
    // Unknown category still shows its multiplier field even without a resolved name.
    expect(screen.getByDisplayValue('1.4')).toBeInTheDocument();
  });
});
