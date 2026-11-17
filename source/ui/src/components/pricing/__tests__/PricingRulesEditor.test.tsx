/**
 * Pricing Rules Editor Component Tests
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import PricingRulesEditor from '../PricingRulesEditor';

describe('PricingRulesEditor', () => {
  const mockOnSave = vi.fn();
  const mockOnCancel = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders pricing rules editor', () => {
    render(
      <PricingRulesEditor
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    expect(screen.getByText('Create Pricing Rules')).toBeInTheDocument();
    expect(screen.getByText('Create new pricing rules')).toBeInTheDocument();
  });

  it('renders in edit mode with current rules', () => {
    const currentRules = {
      rule_set_id: 'test-rules',
      status: 'active' as const,
      pricing_rules: {
        per_access: {
          article: '0.05',
          report: '0.25'
        }
      },
      audit_trail: {
        version: 1,
        change_reason: 'Initial setup'
      }
    };

    render(
      <PricingRulesEditor
        currentRules={currentRules}
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    expect(screen.getByText('Edit Pricing Rules')).toBeInTheDocument();
    expect(screen.getByText('Update existing pricing rules')).toBeInTheDocument();
  });

  it('handles form submission', async () => {
    render(
      <PricingRulesEditor
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    // Fill in required fields
    const effectiveDateInput = screen.getByLabelText('Effective Date');
    const changeReasonInput = screen.getByLabelText('Change Reason');
    
    fireEvent.change(effectiveDateInput, { target: { value: '2025-01-01' } });
    fireEvent.change(changeReasonInput, { target: { value: 'Test pricing update' } });

    // Submit form
    const saveButton = screen.getByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(mockOnSave).toHaveBeenCalledWith(
        expect.any(Object), // pricing rules
        '2025-01-01',
        'Test pricing update',
        false // activate
      );
    });
  });

  it('handles form validation', async () => {
    render(
      <PricingRulesEditor
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    // Try to submit without required fields
    const saveButton = screen.getByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(screen.getByText('Effective date is required')).toBeInTheDocument();
      expect(screen.getByText('Change reason is required')).toBeInTheDocument();
    });

    expect(mockOnSave).not.toHaveBeenCalled();
  });

  it('handles cancel action', () => {
    render(
      <PricingRulesEditor
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    const cancelButton = screen.getByText('Cancel');
    fireEvent.click(cancelButton);

    expect(mockOnCancel).toHaveBeenCalled();
  });

  it('handles activate checkbox', async () => {
    render(
      <PricingRulesEditor
        onSave={mockOnSave}
        onCancel={mockOnCancel}
      />
    );

    // Fill in required fields
    const effectiveDateInput = screen.getByLabelText('Effective Date');
    const changeReasonInput = screen.getByLabelText('Change Reason');
    const activateCheckbox = screen.getByLabelText('Activate immediately');
    
    fireEvent.change(effectiveDateInput, { target: { value: '2025-01-01' } });
    fireEvent.change(changeReasonInput, { target: { value: 'Test pricing update' } });
    fireEvent.click(activateCheckbox);

    // Submit form
    const saveButton = screen.getByText('Save Rules');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(mockOnSave).toHaveBeenCalledWith(
        expect.any(Object), // pricing rules
        '2025-01-01',
        'Test pricing update',
        true // activate
      );
    });
  });

  it('shows loading state during save', async () => {
    const slowOnSave = vi.fn(() => new Promise(resolve => setTimeout(resolve, 100)));

    render(
      <PricingRulesEditor
        onSave={slowOnSave}
        onCancel={mockOnCancel}
      />
    );

    // Fill in required fields
    const effectiveDateInput = screen.getByLabelText('Effective Date');
    const changeReasonInput = screen.getByLabelText('Change Reason');
    
    fireEvent.change(effectiveDateInput, { target: { value: '2025-01-01' } });
    fireEvent.change(changeReasonInput, { target: { value: 'Test pricing update' } });

    // Submit form
    const saveButton = screen.getByText('Save Rules');
    fireEvent.click(saveButton);

    // Should show loading state
    expect(screen.getByText('Saving...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Save Rules')).toBeInTheDocument();
    });
  });
});