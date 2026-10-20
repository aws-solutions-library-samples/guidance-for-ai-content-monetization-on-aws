/**
 * Pricing Configuration Page
 * 
 * This component provides a comprehensive interface for managing versioned pricing rules
 * with effective date scheduling, history tracking, and impact analysis.
 */

import { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Alert,
  Tabs,
  Box,
  ColumnLayout,
  StatusIndicator
} from '@cloudscape-design/components';

// Import pricing-specific components
import PricingRulesEditor from '../components/pricing/PricingRulesEditor';
import PricingHistory from '../components/pricing/PricingHistory';
import PricingPreview from '../components/pricing/PricingPreview';

// Import services
import { pricingService, PricingRules } from '../services/pricingService';

// Types
interface PricingRule {
  rule_set_id: string;
  version_timestamp: string;
  effective_date: string;
  status: 'draft' | 'active' | 'inactive';
  created_by: string;
  created_at: string;
  pricing_rules: PricingRules;
  audit_trail: {
    version: number;
    change_reason: string;
    previous_version?: string;
  };
}

export default function PricingConfiguration() {
  const [activeTab, setActiveTab] = useState('current');
  const [currentRules, setCurrentRules] = useState<PricingRule | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Load current pricing rules on component mount
  useEffect(() => {
    loadCurrentRules();
  }, []);

  const loadCurrentRules = async () => {
    try {
      setLoading(true);
      setError(null);

      const rules = await pricingService.getCurrentRules();
      setCurrentRules(rules);
    } catch (err) {
      console.error('Failed to load current pricing rules:', err);
      setError('Failed to load current pricing rules. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleRulesUpdate = async (newRules: PricingRules, effectiveDate: string, changeReason: string) => {
    try {
      setError(null);
      setSuccess(null);

      await pricingService.updateRules({
        pricing_rules: newRules,
        effective_date: effectiveDate,
        change_reason: changeReason
      });

      setSuccess('Pricing rules updated successfully!');
      await loadCurrentRules(); // Reload current rules

      // Switch to history tab to show the new version
      setActiveTab('history');
    } catch (err) {
      console.error('Failed to update pricing rules:', err);
      setError('Failed to update pricing rules. Please try again.');
    }
  };

  const handleRulesCreate = async (newRules: PricingRules, effectiveDate: string, changeReason: string, activate: boolean = false) => {
    try {
      setError(null);
      setSuccess(null);

      await pricingService.createRules({
        pricing_rules: newRules,
        effective_date: effectiveDate,
        change_reason: changeReason,
        activate
      });

      setSuccess(`Pricing rules ${activate ? 'created and activated' : 'created'} successfully!`);
      await loadCurrentRules(); // Reload current rules

      // Switch to history tab to show the new version
      setActiveTab('history');
    } catch (err) {
      console.error('Failed to create pricing rules:', err);
      setError('Failed to create pricing rules. Please try again.');
    }
  };

  const tabs = [
    {
      id: 'current',
      label: 'Current Rules',
      content: (
        <Container
          header={
            <Header
              variant="h2"
              description="View and manage currently active pricing rules"
              actions={
                <Button
                  variant="primary"
                  onClick={() => setActiveTab('editor')}
                >
                  Edit Rules
                </Button>
              }
            >
              Active Pricing Rules
            </Header>
          }
        >
          {loading ? (
            <Box textAlign="center" padding="l">
              <StatusIndicator type="loading">Loading pricing rules...</StatusIndicator>
            </Box>
          ) : currentRules ? (
            <SpaceBetween size="l">
              <ColumnLayout columns={3}>
                <div>
                  <Box variant="awsui-key-label">Version</Box>
                  <div>{currentRules.audit_trail?.version || 'N/A'}</div>
                </div>
                <div>
                  <Box variant="awsui-key-label">Effective Date</Box>
                  <div>{currentRules.effective_date}</div>
                </div>
                <div>
                  <Box variant="awsui-key-label">Status</Box>
                  <StatusIndicator type={currentRules.status === 'active' ? 'success' : 'pending'}>
                    {currentRules.status}
                  </StatusIndicator>
                </div>
              </ColumnLayout>

              <PricingPreview
                pricingRules={currentRules.pricing_rules}
                readonly={true}
              />
            </SpaceBetween>
          ) : (
            <Box textAlign="center" padding="l">
              <StatusIndicator type="info">
                No active pricing rules found. Create your first pricing configuration.
              </StatusIndicator>
              <Box margin={{ top: 'm' }}>
                <Button
                  variant="primary"
                  onClick={() => setActiveTab('editor')}
                >
                  Create Pricing Rules
                </Button>
              </Box>
            </Box>
          )}
        </Container>
      )
    },
    {
      id: 'editor',
      label: 'Edit Rules',
      content: (
        <PricingRulesEditor
          currentRules={currentRules}
          onSave={currentRules ? handleRulesUpdate : handleRulesCreate}
          onCancel={() => setActiveTab('current')}
        />
      )
    },
    {
      id: 'history',
      label: 'Version History',
      content: (
        <PricingHistory
          onVersionSelect={(version) => {
            // Could implement version comparison or rollback here
            console.log('Selected version:', version);
          }}
        />
      )
    },
    {
      id: 'preview',
      label: 'Impact Analysis',
      content: (
        <Container
          header={
            <Header
              variant="h2"
              description="Preview pricing changes and analyze potential impact"
            >
              Pricing Impact Analysis
            </Header>
          }
        >
          <Alert type="info">
            Impact analysis helps you understand how pricing changes will affect revenue.
            This feature analyzes historical transaction data to project the impact of new pricing rules.
          </Alert>
          <Box margin={{ top: 'l' }}>
            <StatusIndicator type="info">
              Select a pricing rule version from the editor to see impact analysis.
            </StatusIndicator>
          </Box>
        </Container>
      )
    }
  ];

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Configure and manage versioned pricing rules with effective date scheduling"
      >
        Pricing Configuration
      </Header>

      {error && (
        <Alert
          type="error"
          dismissible
          onDismiss={() => setError(null)}
        >
          {error}
        </Alert>
      )}

      {success && (
        <Alert
          type="success"
          dismissible
          onDismiss={() => setSuccess(null)}
        >
          {success}
        </Alert>
      )}

      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={tabs}
      />
    </SpaceBetween>
  );
}