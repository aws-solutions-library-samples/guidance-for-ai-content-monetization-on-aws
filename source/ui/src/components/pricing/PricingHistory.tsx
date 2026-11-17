/**
 * Pricing History Component
 * 
 * Shows version history and audit trail for pricing rules
 */

import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  Table,
  Box,
  SpaceBetween,
  StatusIndicator,
  Button,
  Modal,
  ColumnLayout,
  Alert
} from '@cloudscape-design/components';

import { PricingRule, pricingService } from '../../services/pricingService';
import PricingPreview from './PricingPreview';

interface PricingHistoryProps {
  onVersionSelect?: (version: PricingRule) => void;
}

export default function PricingHistory({ onVersionSelect }: PricingHistoryProps) {
  const [history, setHistory] = useState<PricingRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<PricingRule | null>(null);
  const [showVersionModal, setShowVersionModal] = useState(false);

  useEffect(() => {
    loadHistory();
  }, []);

  const loadHistory = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const historyData = await pricingService.getPricingHistory();
      setHistory(historyData);
    } catch (err) {
      console.error('Failed to load pricing history:', err);
      setError('Failed to load pricing history. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleViewVersion = (version: PricingRule) => {
    setSelectedVersion(version);
    setShowVersionModal(true);
    onVersionSelect?.(version);
  };

  const getStatusType = (status: string) => {
    switch (status) {
      case 'active':
        return 'success';
      case 'draft':
        return 'pending';
      case 'inactive':
        return 'stopped';
      default:
        return 'info';
    }
  };

  const columnDefinitions = [
    {
      id: 'version',
      header: 'Version',
      cell: (item: PricingRule) => (
        <Box fontWeight="bold">
          v{item.audit_trail.version}
        </Box>
      ),
      sortingField: 'audit_trail.version'
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: PricingRule) => (
        <StatusIndicator type={getStatusType(item.status)}>
          {item.status}
        </StatusIndicator>
      ),
      sortingField: 'status'
    },
    {
      id: 'effectiveDate',
      header: 'Effective Date',
      cell: (item: PricingRule) => item.effective_date,
      sortingField: 'effective_date'
    },
    {
      id: 'createdBy',
      header: 'Created By',
      cell: (item: PricingRule) => item.created_by,
      sortingField: 'created_by'
    },
    {
      id: 'createdAt',
      header: 'Created At',
      cell: (item: PricingRule) => new Date(item.created_at).toLocaleString(),
      sortingField: 'created_at'
    },
    {
      id: 'changeReason',
      header: 'Change Reason',
      cell: (item: PricingRule) => (
        <Box variant="span" fontSize="body-s">
          {item.audit_trail.change_reason}
        </Box>
      ),
      sortingField: 'audit_trail.change_reason'
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: (item: PricingRule) => (
        <Button
          variant="inline-link"
          onClick={() => handleViewVersion(item)}
        >
          View Details
        </Button>
      )
    }
  ];

  return (
    <>
      <Container
        header={
          <Header
            variant="h2"
            description="View version history and audit trail for pricing rules"
            actions={
              <Button
                iconName="refresh"
                onClick={loadHistory}
                loading={loading}
              >
                Refresh
              </Button>
            }
          >
            Pricing Rule History
          </Header>
        }
      >
        {error && (
          <Alert type="error" dismissible onDismiss={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Table
          columnDefinitions={columnDefinitions}
          items={history}
          loading={loading}
          loadingText="Loading pricing history..."
          sortingDisabled={false}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No pricing history found</b>
              <Box variant="p" color="inherit">
                Create your first pricing rules to see version history.
              </Box>
            </Box>
          }
          header={
            <Header
              counter={`(${history.length})`}
              description="All versions of pricing rules with their audit trail"
            >
              Version History
            </Header>
          }
        />
      </Container>

      <Modal
        visible={showVersionModal}
        onDismiss={() => setShowVersionModal(false)}
        header={`Pricing Rules - Version ${selectedVersion?.audit_trail?.version || (selectedVersion as { version?: number })?.version || 'N/A'}`}
        size="large"
      >
        {selectedVersion && (
          <SpaceBetween size="l">
            <ColumnLayout columns={3}>
              <div>
                <Box variant="awsui-key-label">Status</Box>
                <StatusIndicator type={getStatusType(selectedVersion.status)}>
                  {selectedVersion.status}
                </StatusIndicator>
              </div>
              <div>
                <Box variant="awsui-key-label">Effective Date</Box>
                <div>{selectedVersion.effective_date}</div>
              </div>
              <div>
                <Box variant="awsui-key-label">Created By</Box>
                <div>{selectedVersion.created_by}</div>
              </div>
            </ColumnLayout>

            <div>
              <Box variant="awsui-key-label">Change Reason</Box>
              <Box variant="p">{selectedVersion.audit_trail?.change_reason || 'No reason provided'}</Box>
            </div>

            {selectedVersion.audit_trail?.previous_version && (
              <Alert type="info">
                This version supersedes version created at {selectedVersion.audit_trail.previous_version}
              </Alert>
            )}

            <PricingPreview 
              pricingRules={selectedVersion.pricing_rules}
              readonly={true}
            />
          </SpaceBetween>
        )}
      </Modal>
    </>
  );
}