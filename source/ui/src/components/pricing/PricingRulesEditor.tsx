/**
 * Pricing Rules Editor Component
 * 
 * Provides an interface for creating and editing versioned pricing rules
 */

import { useState, useEffect, useMemo } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Form,
  FormField,
  Input,
  Textarea,
  DatePicker,
  ColumnLayout,
  ExpandableSection,
  Box,
  Tabs
} from '@cloudscape-design/components';

import { PricingRules, PricingRule } from '../../services/pricingService';
import PricingPreview from './PricingPreview';
import CategoryMultipliersManager from './CategoryMultipliersManager';
import MultipliersManager from './MultipliersManager';

// Category Multiplier Field Component
interface PricingRulesEditorProps {
  currentRules?: PricingRule | null;
  onSave: (rules: PricingRules, effectiveDate: string, changeReason: string, activate?: boolean) => Promise<void>;
  onCancel: () => void;
}

export default function PricingRulesEditor({ currentRules, onSave, onCancel }: PricingRulesEditorProps) {
  const [pricingRules, setPricingRules] = useState<PricingRules>({
    per_access: {
      article: '0.05',
      video: '0.10',
      image: '0.02',
      document: '0.08',
      report: '0.25'
    },
    multipliers: {
      iab_category: {
        '111': '1.2',  // Medical Health
        '126': '1.1',  // Technology & Computing
        '52': '1.3',   // Business and Finance
        '1': '1.0',    // Automotive
        '150': '1.0'   // Attractions
      },
      recency: {
        'last_24h': '1.5',
        'last_week': '1.2',
        'last_month': '1.0',
        'older': '0.8'
      },
      demand: {
        'high': '1.3',
        'medium': '1.0',
        'low': '0.8'
      }
    }
  });

  const [effectiveDate, setEffectiveDate] = useState('');
  const [changeReason, setChangeReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<{ [key: string]: string }>({});

  // Category ID -> display name. Derived from the server-resolved iab_category_resolved block
  // returned by GET /pricing/rules (SSOT #26) — no client-side taxonomy join required.
  const categoryMappings = useMemo<{ [key: string]: string }>(() => {
    const resolved = currentRules?.pricing_rules?.multipliers?.iab_category_resolved;
    if (!resolved) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(resolved).map(([id, info]) => [id, info.full_path || info.category_name])
    );
  }, [currentRules]);

  // Initialize form with current rules if editing
  useEffect(() => {
    if (currentRules) {
      setPricingRules(currentRules.pricing_rules);
      // Set effective date to tomorrow by default for updates
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      setEffectiveDate(tomorrow.toISOString().split('T')[0]);
    } else {
      // Set effective date to today for new rules
      setEffectiveDate(new Date().toISOString().split('T')[0]);
    }
  }, [currentRules]);

  const validateForm = (): boolean => {
    const newErrors: { [key: string]: string } = {};

    if (!effectiveDate) {
      newErrors.effectiveDate = 'Effective date is required';
    }

    if (!changeReason.trim()) {
      newErrors.changeReason = 'Change reason is required';
    }

    // Validate pricing values
    Object.entries(pricingRules.per_access).forEach(([key, value]) => {
      if (!value || isNaN(parseFloat(value)) || parseFloat(value) < 0) {
        newErrors[`per_access_${key}`] = `Invalid price for ${key}`;
      }
    });

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = async (activate: boolean = false) => {
    console.log('Save button clicked, activate:', activate);
    console.log('Current pricing rules:', pricingRules);
    console.log('Effective date:', effectiveDate);
    console.log('Change reason:', changeReason);
    
    if (!validateForm()) {
      console.log('Form validation failed');
      return;
    }

    try {
      setSaving(true);
      console.log('Calling onSave...');
      await onSave(pricingRules, effectiveDate, changeReason, activate);
      console.log('onSave completed successfully');
    } catch (error) {
      console.error('Failed to save pricing rules:', error);
      // Show error to user
      alert(`Failed to save pricing rules: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setSaving(false);
    }
  };

  const updatePerAccessPrice = (contentType: string, value: string) => {
    setPricingRules(prev => ({
      ...prev,
      per_access: {
        ...prev.per_access,
        [contentType]: value
      }
    }));
  };

  const updateMultiplier = (category: string, key: string, value: string) => {
    setPricingRules(prev => ({
      ...prev,
      multipliers: {
        ...prev.multipliers,
        [category]: {
          ...prev.multipliers?.[category],
          [key]: value
        }
      }
    }));
  };

  const updateCategoryMultiplier = (categoryId: string, multiplier: string) => {
    updateMultiplier('iab_category', categoryId, multiplier);
  };

  const updateRecencyMultiplier = (key: string, multiplier: string) => {
    updateMultiplier('recency', key, multiplier);
  };

  const updateDemandMultiplier = (key: string, multiplier: string) => {
    updateMultiplier('demand', key, multiplier);
  };

  const removeRecencyMultiplier = (key: string) => {
    setPricingRules(prev => {
      const newMultipliers = { ...prev.multipliers };
      if (newMultipliers.recency) {
        const newRecency = { ...newMultipliers.recency };
        delete newRecency[key];
        newMultipliers.recency = newRecency;
      }
      return { ...prev, multipliers: newMultipliers };
    });
  };

  const removeDemandMultiplier = (key: string) => {
    setPricingRules(prev => {
      const newMultipliers = { ...prev.multipliers };
      if (newMultipliers.demand) {
        const newDemand = { ...newMultipliers.demand };
        delete newDemand[key];
        newMultipliers.demand = newDemand;
      }
      return { ...prev, multipliers: newMultipliers };
    });
  };

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={currentRules ? "Update existing pricing rules" : "Create new pricing rules"}
        >
          {currentRules ? 'Edit Pricing Rules' : 'Create Pricing Rules'}
        </Header>
      }
    >
      <Form
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              loading={saving}
              onClick={() => handleSave(false)}
            >
              Save as Draft
            </Button>
            <Button
              variant="primary"
              loading={saving}
              onClick={() => handleSave(true)}
            >
              Save and Activate
            </Button>
          </SpaceBetween>
        }
      >
        <SpaceBetween size="l">
          <ColumnLayout columns={2}>
            <FormField
              label="Effective Date"
              description="Date when these pricing rules will become active"
              errorText={errors.effectiveDate}
            >
              <DatePicker
                value={effectiveDate}
                onChange={({ detail }) => setEffectiveDate(detail.value)}
                placeholder="YYYY-MM-DD"
              />
            </FormField>

            <FormField
              label="Change Reason"
              description="Describe why these pricing rules are being changed"
              errorText={errors.changeReason}
            >
              <Textarea
                value={changeReason}
                onChange={({ detail }) => setChangeReason(detail.value)}
                placeholder="e.g., Q1 2024 pricing update"
                rows={3}
              />
            </FormField>
          </ColumnLayout>

          <ExpandableSection headerText="Per-Access Pricing" defaultExpanded>
            <SpaceBetween size="m">
              <Box variant="p">
                Set the base price for each content type when accessed by AI agents.
              </Box>
              <ColumnLayout columns={3}>
                {Object.entries(pricingRules.per_access).map(([contentType, price]) => (
                  <FormField
                    key={contentType}
                    label={contentType.charAt(0).toUpperCase() + contentType.slice(1)}
                    errorText={errors[`per_access_${contentType}`]}
                  >
                    <Input
                      value={price}
                      onChange={({ detail }) => updatePerAccessPrice(contentType, detail.value)}
                      placeholder="0.00"
                      type="number"
                      step="0.01"
                    />
                  </FormField>
                ))}
              </ColumnLayout>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Pricing Multipliers" defaultExpanded>
            <SpaceBetween size="m">
              <Box variant="p">
                Configure multipliers that adjust the base pricing based on content characteristics.
                Use the tabs below to manage different types of multipliers.
              </Box>
              
              <Tabs
                tabs={[
                  {
                    id: 'categories',
                    label: 'Categories',
                    content: (
                      <CategoryMultipliersManager
                        multipliers={pricingRules.multipliers?.iab_category || {}}
                        categoryMappings={categoryMappings}
                        onUpdate={updateCategoryMultiplier}
                      />
                    )
                  },
                  {
                    id: 'recency',
                    label: 'Content Age',
                    content: (
                      <MultipliersManager
                        title="Recency Multipliers"
                        description="Adjust pricing based on content age. Newer content typically commands higher prices."
                        multipliers={pricingRules.multipliers?.recency || {}}
                        displayNames={{
                          'last_24h': 'Last 24 Hours',
                          'last_week': 'Last Week',
                          'last_month': 'Last Month',
                          'older': 'Older than 1 Month'
                        }}
                        descriptions={{
                          'last_24h': 'Content published within the last 24 hours',
                          'last_week': 'Content published within the last week',
                          'last_month': 'Content published within the last month',
                          'older': 'Content older than 1 month'
                        }}
                        onUpdate={updateRecencyMultiplier}
                        onRemove={removeRecencyMultiplier}
                        allowCustom={true}
                      />
                    )
                  },
                  {
                    id: 'demand',
                    label: 'Demand Level',
                    content: (
                      <MultipliersManager
                        title="Demand Multipliers"
                        description="Adjust pricing based on content demand level. High-demand content commands premium pricing."
                        multipliers={pricingRules.multipliers?.demand || {}}
                        displayNames={{
                          'high': 'High Demand',
                          'medium': 'Medium Demand',
                          'low': 'Low Demand'
                        }}
                        descriptions={{
                          'high': 'Content with high access frequency and engagement',
                          'medium': 'Content with moderate access patterns',
                          'low': 'Content with low access frequency'
                        }}
                        onUpdate={updateDemandMultiplier}
                        onRemove={removeDemandMultiplier}
                        allowCustom={true}
                      />
                    )
                  }
                ]}
              />
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Preview">
            <PricingPreview pricingRules={pricingRules} categoryMappings={categoryMappings} />
          </ExpandableSection>
        </SpaceBetween>
      </Form>
    </Container>
  );
}