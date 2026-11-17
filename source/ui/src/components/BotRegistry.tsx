import React, { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  Button,
  Cards,
  Badge,
  StatusIndicator,
  Table,
  Alert,
  Tabs,
  Toggle,
  Input,
  FormField,
  Modal,
  Form,
  Select,
  Link,
  ProgressBar,
  ColumnLayout,
  Checkbox,
  CopyToClipboard,
  ExpandableSection,
} from '@cloudscape-design/components';

import {
  partnerService,
  RegisteredPartner,
  AccessLogEntry,
  PartnerStats,
  UpdatePartnerRequest,
  CreatePartnerRequest,
  PartnerDetail,
  PartnerKeyCreated,
  KEY_GATED_OPERATIONS,
} from '../services/partnerService';
import { useFeatureFlags } from '../contexts/FeatureFlagContext';

export default function BotRegistry() {
  const { demoDataEnabled } = useFeatureFlags();
  const [selectedTab, setSelectedTab] = useState('partners');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Partner data state
  const [partners, setPartners] = useState<RegisteredPartner[]>([]);
  const [partnerStats, setPartnerStats] = useState<PartnerStats | null>(null);
  const [partnerFilter, setPartnerFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  
  // Access log state
  const [accessLogs, setAccessLogs] = useState<AccessLogEntry[]>([]);
  const [logFilter, setLogFilter] = useState('');
  const [selectedPartnerId, setSelectedPartnerId] = useState('all');
  const [timePeriod, setTimePeriod] = useState('last_24_hours');
  
  // Modal state
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedPartner, setSelectedPartner] = useState<RegisteredPartner | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  
  // Form state
  const [editForm, setEditForm] = useState<UpdatePartnerRequest>({});

  // Create / key lifecycle state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState<CreatePartnerRequest>({ partner_name: '', pricing_tier: 'standard', allowed_endpoints: [] });
  const [createLoading, setCreateLoading] = useState(false);
  const [keyReveal, setKeyReveal] = useState<PartnerKeyCreated | null>(null);
  const [details, setDetails] = useState<Record<string, PartnerDetail>>({});
  const [busyPartnerId, setBusyPartnerId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<RegisteredPartner | null>(null);

  // Load data on component mount
  useEffect(() => {
    loadBotRegistryData();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, []);

  const loadBotRegistryData = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const [partnersData, statsData, logsData] = await Promise.all([
        partnerService.getRegisteredPartners(undefined, demoDataEnabled),
        partnerService.getPartnerStats(demoDataEnabled),
        partnerService.getAccessLogs({ time_period: timePeriod, limit: 100 }, demoDataEnabled),
      ]);
      
      setPartners(partnersData);
      setPartnerStats(statsData);
      setAccessLogs(logsData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bot registry data');
      console.error('Error loading bot registry data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    loadBotRegistryData();
  };

  const handleTogglePartnerStatus = async (partnerId: string, enabled: boolean) => {
    try {
      await partnerService.togglePartnerStatus(partnerId, enabled);
      await loadBotRegistryData(); // Refresh data
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update partner status');
    }
  };

  const handleEditPartner = (partner: RegisteredPartner) => {
    setSelectedPartner(partner);
    setEditForm({
      partner_name: partner.partner_name,
      contact_email: partner.contact_email,
      pricing_tier: partner.pricing_tier,
      allowed_endpoints: partner.allowed_endpoints,
      status: partner.status === 'suspended' ? 'suspended' : 'active',
    });
    setShowEditModal(true);
  };

  const handleCreatePartner = async () => {
    try {
      setCreateLoading(true);
      const created = await partnerService.createPartner(createForm);
      setShowCreateModal(false);
      setCreateForm({ partner_name: '', pricing_tier: 'standard', allowed_endpoints: [] });
      setKeyReveal(created);
      await loadBotRegistryData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create partner');
    } finally {
      setCreateLoading(false);
    }
  };

  const handleRotateKey = async (partner: RegisteredPartner) => {
    try {
      setBusyPartnerId(partner.partner_id);
      const rotated = await partnerService.rotateKey(partner.partner_id);
      setKeyReveal(rotated);
      await Promise.all([loadBotRegistryData(), loadDetail(partner.partner_id)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rotate key');
    } finally {
      setBusyPartnerId(null);
    }
  };

  const handleDeletePartner = async () => {
    if (!confirmDelete) return;
    try {
      setBusyPartnerId(confirmDelete.partner_id);
      await partnerService.deletePartner(confirmDelete.partner_id);
      setConfirmDelete(null);
      await loadBotRegistryData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete partner');
    } finally {
      setBusyPartnerId(null);
    }
  };

  const handleRetireKey = async (partnerId: string, keyId: string) => {
    try {
      setBusyPartnerId(partnerId);
      await partnerService.retireKey(partnerId, keyId);
      await Promise.all([loadBotRegistryData(), loadDetail(partnerId)]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to retire key');
    } finally {
      setBusyPartnerId(null);
    }
  };

  const loadDetail = async (partnerId: string) => {
    try {
      const detail = await partnerService.getPartner(partnerId);
      setDetails((prev) => ({ ...prev, [partnerId]: detail }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load partner keys');
    }
  };

  const toggleEndpoint = (current: string[] | undefined, id: string, checked: boolean): string[] => {
    const set = new Set(current || []);
    if (checked) set.add(id); else set.delete(id);
    return KEY_GATED_OPERATIONS.map((op) => op.id).filter((opId) => set.has(opId));
  };

  const handleSavePartner = async () => {
    if (!selectedPartner) return;

    try {
      setEditLoading(true);
      await partnerService.updatePartner(selectedPartner.partner_id, editForm);
      setShowEditModal(false);
      await loadBotRegistryData(); // Refresh data
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update partner');
    } finally {
      setEditLoading(false);
    }
  };

  const resetEditForm = () => {
    setEditForm({});
    setSelectedPartner(null);
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active': return 'success';
      case 'suspended': return 'error';
      case 'deleted': return 'stopped';
      default: return 'info';
    }
  };

  const getTierColor = (tier: string) => {
    switch (tier) {
      case 'enterprise': return 'blue';
      case 'premium': return 'green';
      case 'standard': return 'grey';
      case 'basic': return 'red';
      default: return 'grey';
    }
  };

  const getResponseCodeColor = (code: number) => {
    if (code >= 200 && code < 300) return 'success';
    if (code >= 400 && code < 500) return 'warning';
    if (code >= 500) return 'error';
    return 'info';
  };

  const filteredPartners = partners.filter(partner => {
    const matchesName = partner.partner_name.toLowerCase().includes(partnerFilter.toLowerCase()) ||
                       partner.partner_id.toLowerCase().includes(partnerFilter.toLowerCase());
    const matchesStatus = statusFilter === 'all' || partner.status === statusFilter;
    return matchesName && matchesStatus;
  });

  const filteredAccessLogs = accessLogs.filter(log => {
    const matchesFilter = log.partner_name.toLowerCase().includes(logFilter.toLowerCase()) ||
                         log.endpoint.toLowerCase().includes(logFilter.toLowerCase()) ||
                         log.user_agent.toLowerCase().includes(logFilter.toLowerCase());
    const matchesPartner = selectedPartnerId === 'all' || log.partner_id === selectedPartnerId;
    return matchesFilter && matchesPartner;
  });

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Manage partners / licensees and their API keys, monitor their access patterns and activity"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh Data
              </Button>
              <Button href="/dashboard">View Metrics</Button>
              <Button variant="primary" iconName="add-plus" onClick={() => setShowCreateModal(true)}>
                Create Partner
              </Button>
            </SpaceBetween>
          }
        >
          Partner / Licensee Registry
        </Header>

        {error && (
          <Alert type="error" header="Error Loading Bot Registry Data">
            {error}
          </Alert>
        )}

        {/* Partner Statistics Overview */}
        {partnerStats && (
          <Container>
            <Header variant="h2">Registry Overview</Header>
            <ColumnLayout columns={4} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Total Partners</Box>
                <Box variant="h3" color="text-status-info">{partnerStats.total_partners}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Active Partners</Box>
                <Box variant="h3" color="text-status-success">{partnerStats.active_partners}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Suspended Partners</Box>
                <Box variant="h3" color="text-status-error">{partnerStats.suspended_partners}</Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Active API Keys</Box>
                <Box variant="h3" color="text-status-info">{partnerStats.total_active_keys}</Box>
              </div>
            </ColumnLayout>
          </Container>
        )}

        <Tabs
          activeTabId={selectedTab}
          onChange={({ detail }) => setSelectedTab(detail.activeTabId)}
          tabs={[
            {
              id: 'partners',
              label: 'Partners / Licensees',
              content: (
                <SpaceBetween size="l">
                  {/* Partner Management */}
                  <Container>
                    <Header 
                      variant="h2"
                      description="View and manage registered bots and partners"
                    >
                      Registered Partners
                    </Header>
                    
                    <SpaceBetween size="m">
                      <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                        <FormField label="Filter by name or ID">
                          <Input
                            value={partnerFilter}
                            onChange={({ detail }) => setPartnerFilter(detail.value)}
                            placeholder="Search partners..."
                          />
                        </FormField>
                        <FormField label="Filter by status">
                          <Select
                            selectedOption={{ label: statusFilter === 'all' ? 'All Statuses' : statusFilter, value: statusFilter }}
                            onChange={({ detail }) => setStatusFilter(detail.selectedOption.value)}
                            options={[
                              { label: 'All Statuses', value: 'all' },
                              { label: 'Active', value: 'active' },
                              { label: 'Suspended', value: 'suspended' },
                            ]}
                          />
                        </FormField>
                      </Grid>

                      <Cards
                        cardDefinition={{
                          header: item => (
                            <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
                              <Box variant="h3">{item.partner_name}</Box>
                              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                                <Badge color={getTierColor(item.pricing_tier)}>{item.pricing_tier}</Badge>
                                <StatusIndicator type={getStatusColor(item.status)}>
                                  {item.status}
                                </StatusIndicator>
                                <Toggle
                                  checked={item.status === 'active'}
                                  onChange={({ detail }) => handleTogglePartnerStatus(item.partner_id, detail.checked)}
                                >
                                  {item.status === 'active' ? 'Active' : 'Suspended'}
                                </Toggle>
                              </SpaceBetween>
                            </Grid>
                          ),
                          sections: [
                            {
                              id: 'details',
                              content: item => (
                                <ColumnLayout columns={2} variant="text-grid">
                                  <div>
                                    <Box variant="awsui-key-label">Partner ID</Box>
                                    <Box>{item.partner_id}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Contact Email</Box>
                                    <Box>{item.contact_email}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Registration Date</Box>
                                    <Box>{new Date(item.created_at).toLocaleDateString()}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Identity / Active Keys</Box>
                                    <Box>{item.identity_source} · {item.active_key_count} active key{item.active_key_count === 1 ? '' : 's'}</Box>
                                  </div>
                                </ColumnLayout>
                              )
                            },
                            {
                              id: 'usage',
                              content: item => (
                                <SpaceBetween size="s">
                                  <div>
                                    <Box variant="awsui-key-label">Tier &amp; quota</Box>
                                    {details[item.partner_id]?.tier_limits ? (
                                      <SpaceBetween size="xs">
                                        <Box>
                                          <Badge color={getTierColor(item.pricing_tier)}>{item.pricing_tier}</Badge>{' '}
                                          {details[item.partner_id]!.tier_limits!.quota_limit.toLocaleString()} requests / {details[item.partner_id]!.tier_limits!.quota_period.toLowerCase()}
                                          {details[item.partner_id]!.tier_limits!.rate_limit > 0 && ` · ${details[item.partner_id]!.tier_limits!.rate_limit} rps`}
                                        </Box>
                                        {details[item.partner_id]!.keys.filter((k) => k.status === 'active' && k.usage).map((k) => (
                                          <ProgressBar
                                            key={k.key_id}
                                            label={`Key ${k.key_id}`}
                                            value={details[item.partner_id]!.tier_limits!.quota_limit > 0
                                              ? (k.usage!.used / details[item.partner_id]!.tier_limits!.quota_limit) * 100 : 0}
                                            additionalInfo={`${k.usage!.used.toLocaleString()} used · ${k.usage!.remaining.toLocaleString()} remaining (${k.usage!.period_start} → ${k.usage!.period_end})`}
                                          />
                                        ))}
                                      </SpaceBetween>
                                    ) : (
                                      <Box>
                                        <Badge color={getTierColor(item.pricing_tier)}>{item.pricing_tier}</Badge>{' '}
                                        <Link onFollow={(e) => { e.preventDefault(); loadDetail(item.partner_id); }} href="#">
                                          Load limits &amp; usage
                                        </Link>
                                      </Box>
                                    )}
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Allowed Endpoints</Box>
                                    <Box>
                                      <SpaceBetween direction="horizontal" size="xs">
                                        {(Array.isArray(item.allowed_endpoints) && item.allowed_endpoints.length > 0
                                          ? item.allowed_endpoints
                                          : ['all key-gated operations']
                                        ).map((endpoint, index) => (
                                          <Badge key={index} color="blue">
                                            {endpoint}
                                          </Badge>
                                        ))}
                                      </SpaceBetween>
                                    </Box>
                                  </div>
                                </SpaceBetween>
                              )
                            },
                            {
                              id: 'actions',
                              content: item => (
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Button 
                                    onClick={() => handleEditPartner(item)}
                                  >
                                    Edit Partner
                                  </Button>
                                  {item.identity_source === 'direct' && (
                                    <Button
                                      iconName="key"
                                      onClick={() => handleRotateKey(item)}
                                      loading={busyPartnerId === item.partner_id}
                                    >
                                      Rotate Key
                                    </Button>
                                  )}
                                  <Button 
                                    onClick={() => {
                                      setSelectedPartnerId(item.partner_id);
                                      setSelectedTab('logs');
                                    }}
                                    variant="normal"
                                  >
                                    View Access Logs
                                  </Button>
                                  <Button
                                    iconName="remove"
                                    onClick={() => setConfirmDelete(item)}
                                    disabled={busyPartnerId === item.partner_id}
                                  >
                                    Delete Partner
                                  </Button>
                                </SpaceBetween>
                              )
                            },
                            {
                              id: 'keys',
                              content: item => (
                                <ExpandableSection
                                  headerText={`API keys (${item.active_key_count} active)`}
                                  onChange={({ detail }) => { if (detail.expanded && !details[item.partner_id]) loadDetail(item.partner_id); }}
                                >
                                  {details[item.partner_id] ? (
                                    <Table
                                      variant="embedded"
                                      items={details[item.partner_id].keys}
                                      columnDefinitions={[
                                        { id: 'id', header: 'Key ID', cell: (k) => k.key_id },
                                        { id: 'status', header: 'Status', cell: (k) => <StatusIndicator type={k.status === 'active' ? 'success' : 'stopped'}>{k.status}</StatusIndicator> },
                                        { id: 'created', header: 'Created', cell: (k) => (k.created_at ? new Date(k.created_at).toLocaleString() : '—') },
                                        {
                                          id: 'actions', header: '', cell: (k) => (
                                            <Button
                                              variant="inline-link"
                                              onClick={() => handleRetireKey(item.partner_id, k.key_id)}
                                              disabled={busyPartnerId === item.partner_id || (k.status === 'active' && item.active_key_count <= 1)}
                                            >
                                              Retire
                                            </Button>
                                          ),
                                        },
                                      ]}
                                      empty={<Box textAlign="center">No keys. Rotate to provision one.</Box>}
                                    />
                                  ) : (
                                    <Box>Loading keys…</Box>
                                  )}
                                </ExpandableSection>
                              )
                            }
                          ]
                        }}
                        cardsPerRow={[{ cards: 1 }]}
                        items={filteredPartners}
                        loading={loading}
                        empty={
                          <Box textAlign="center" color="inherit">
                            <b>No partners found</b>
                            <Box variant="p" color="inherit">
                              {partnerFilter || statusFilter !== 'all' ? 'Try adjusting your search criteria.' : 'No registered partners found.'}
                            </Box>
                          </Box>
                        }
                      />
                    </SpaceBetween>
                  </Container>
                </SpaceBetween>
              )
            },
            {
              id: 'logs',
              label: 'Access Log/Activity Info',
              content: (
                <SpaceBetween size="l">
                  {/* Access Log Management */}
                  <Container>
                    <Header 
                      variant="h2"
                      description="Monitor partner access patterns and API usage. For large volume data analysis, use the comprehensive metrics dashboard."
                      actions={
                        <Button href="/dashboard" variant="primary">
                          View Full Metrics Dashboard
                        </Button>
                      }
                    >
                      Access Log Activity
                    </Header>
                    
                    <SpaceBetween size="m">
                      <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
                        <FormField label="Filter logs">
                          <Input
                            value={logFilter}
                            onChange={({ detail }) => setLogFilter(detail.value)}
                            placeholder="Search by partner, endpoint, or user agent..."
                          />
                        </FormField>
                        <FormField label="Partner">
                          <Select
                            selectedOption={{ 
                              label: selectedPartnerId === 'all' ? 'All Partners' : 
                                     partners.find(p => p.partner_id === selectedPartnerId)?.partner_name || 'Unknown',
                              value: selectedPartnerId 
                            }}
                            onChange={({ detail }) => setSelectedPartnerId(detail.selectedOption.value)}
                            options={[
                              { label: 'All Partners', value: 'all' },
                              ...partners.map(partner => ({
                                label: partner.partner_name,
                                value: partner.partner_id,
                              }))
                            ]}
                          />
                        </FormField>
                        <FormField label="Time Period">
                          <Select
                            selectedOption={{ 
                              label: timePeriod.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                              value: timePeriod 
                            }}
                            onChange={({ detail }) => {
                              setTimePeriod(detail.selectedOption.value);
                              // Reload logs with new time period
                              partnerService.getAccessLogs({ 
                                time_period: detail.selectedOption.value, 
                                limit: 100 
                              }, demoDataEnabled)
                                .then(setAccessLogs)
                                .catch((err) => {
                                  setAccessLogs([]);
                                  setError(err instanceof Error ? err.message : 'Failed to load access logs');
                                });
                            }}
                            options={[
                              { label: 'Last Hour', value: 'last_hour' },
                              { label: 'Last 24 Hours', value: 'last_24_hours' },
                              { label: 'Last 7 Days', value: 'last_7_days' },
                              { label: 'Last 30 Days', value: 'last_30_days' },
                            ]}
                          />
                        </FormField>
                      </Grid>

                      <Alert type="info" header="Access Log Data Source">
                        This view shows bot activity data as a proxy for access logs. The data is sourced from 
                        AWS WAF bot control metrics. For comprehensive analytics, detailed reporting, and large 
                        volume data analysis, please use the{' '}
                        <Link href="/dashboard" external={false}>full metrics dashboard</Link> which provides 
                        advanced filtering, export capabilities, and historical trend analysis.
                      </Alert>

                      <Table
                        columnDefinitions={[
                          {
                            id: 'timestamp',
                            header: 'Timestamp',
                            cell: item => new Date(item.timestamp).toLocaleString(),
                            sortingField: 'timestamp',
                          },
                          {
                            id: 'partner',
                            header: 'Partner',
                            cell: item => (
                              <div>
                                <Box variant="strong">{item.partner_name}</Box>
                                <Box variant="small" color="text-body-secondary">{item.partner_id}</Box>
                              </div>
                            ),
                          },
                          {
                            id: 'endpoint',
                            header: 'Endpoint',
                            cell: item => <Badge color="blue">{item.endpoint}</Badge>,
                          },
                          {
                            id: 'response',
                            header: 'Response',
                            cell: item => (
                              <StatusIndicator type={getResponseCodeColor(item.response_code)}>
                                {item.response_code}
                              </StatusIndicator>
                            ),
                          },
                          {
                            id: 'ip_address',
                            header: 'IP Address',
                            cell: item => <Box variant="code">{item.ip_address}</Box>,
                          },
                          {
                            id: 'user_agent',
                            header: 'User Agent',
                            cell: item => (
                              <Box variant="small">
                                {item.user_agent.length > 30 ? `${item.user_agent.substring(0, 30)}...` : item.user_agent}
                              </Box>
                            ),
                          },
                          {
                            id: 'content',
                            header: 'Content Accessed',
                            cell: item => item.content_accessed ? (
                              <Box variant="code">{item.content_accessed}</Box>
                            ) : (
                              <Box color="text-body-secondary">-</Box>
                            ),
                          },
                          {
                            id: 'price',
                            header: 'Price Charged',
                            cell: item => item.price_charged ? (
                              <Box>${item.price_charged.toFixed(3)}</Box>
                            ) : (
                              <Box color="text-body-secondary">$0.000</Box>
                            ),
                          },
                          {
                            id: 'processing_time',
                            header: 'Processing Time',
                            cell: item => item.processing_time_ms ? (
                              <Box>{item.processing_time_ms}ms</Box>
                            ) : (
                              <Box color="text-body-secondary">-</Box>
                            ),
                          },
                        ]}
                        items={filteredAccessLogs}
                        loading={loading}
                        sortingDisabled={false}
                        empty={
                          <Box textAlign="center" color="inherit">
                            <b>No access logs found</b>
                            <Box variant="p" color="inherit">
                              {logFilter || selectedPartnerId !== 'all' ? 'Try adjusting your search criteria.' : 'No access logs available for the selected time period.'}
                            </Box>
                          </Box>
                        }
                        header={
                          <Header
                            counter={`(${filteredAccessLogs.length})`}
                            description="Recent API access attempts and their outcomes"
                          >
                            Access Log Entries
                          </Header>
                        }
                      />
                    </SpaceBetween>
                  </Container>
                </SpaceBetween>
              )
            }
          ]}
        />

        {/* Edit Partner Modal */}
        <Modal
          visible={showEditModal}
          onDismiss={() => {
            setShowEditModal(false);
            resetEditForm();
          }}
          header="Edit Partner / Licensee"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button 
                  variant="link" 
                  onClick={() => {
                    setShowEditModal(false);
                    resetEditForm();
                  }}
                >
                  Cancel
                </Button>
                <Button 
                  variant="primary" 
                  onClick={handleSavePartner}
                  loading={editLoading}
                >
                  Save Changes
                </Button>
              </SpaceBetween>
            </Box>
          }
          size="large"
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Partner Name">
                <Input
                  value={editForm.partner_name || ''}
                  onChange={({ detail }) => setEditForm({ ...editForm, partner_name: detail.value })}
                  placeholder="Enter partner name"
                />
              </FormField>

              <FormField label="Contact Email">
                <Input
                  value={editForm.contact_email || ''}
                  onChange={({ detail }) => setEditForm({ ...editForm, contact_email: detail.value })}
                  placeholder="Enter contact email"
                  type="email"
                />
              </FormField>

              <FormField label="Pricing Tier">
                <Select
                  selectedOption={{ 
                    label: editForm.pricing_tier || 'standard', 
                    value: editForm.pricing_tier || 'standard' 
                  }}
                  onChange={({ detail }) => setEditForm({ ...editForm, pricing_tier: detail.selectedOption.value as CreatePartnerRequest['pricing_tier'] })}
                  options={[
                    { label: 'Basic', value: 'basic' },
                    { label: 'Standard', value: 'standard' },
                    { label: 'Premium', value: 'premium' },
                    { label: 'Enterprise', value: 'enterprise' },
                  ]}
                />
              </FormField>

              <FormField label="Status">
                <Select
                  selectedOption={{ 
                    label: editForm.status || 'active', 
                    value: editForm.status || 'active' 
                  }}
                  onChange={({ detail }) => setEditForm({ ...editForm, status: detail.selectedOption.value as UpdatePartnerRequest['status'] })}
                  options={[
                    { label: 'Active', value: 'active' },
                    { label: 'Suspended (disables every key)', value: 'suspended' },
                  ]}
                />
              </FormField>

              <FormField
                label="Allowed Endpoints"
                description="Key-gated partner-plane operations this partner may call. None selected = all allowed."
              >
                <SpaceBetween size="xs">
                  {KEY_GATED_OPERATIONS.map((op) => (
                    <Checkbox
                      key={op.id}
                      checked={(editForm.allowed_endpoints || []).includes(op.id)}
                      onChange={({ detail }) => setEditForm({ ...editForm, allowed_endpoints: toggleEndpoint(editForm.allowed_endpoints, op.id, detail.checked) })}
                    >
                      {op.label}
                    </Checkbox>
                  ))}
                </SpaceBetween>
              </FormField>
            </SpaceBetween>
          </Form>
        </Modal>

        {/* Create Partner Modal */}
        <Modal
          visible={showCreateModal}
          onDismiss={() => setShowCreateModal(false)}
          header="Create Partner / Licensee"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setShowCreateModal(false)}>Cancel</Button>
                <Button
                  variant="primary"
                  onClick={handleCreatePartner}
                  loading={createLoading}
                  disabled={!createForm.partner_name.trim() || (createForm.identity_source === 'marketplace' && !createForm.marketplace_type)}
                >
                  Create
                </Button>
              </SpaceBetween>
            </Box>
          }
          size="large"
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Partner Name">
                <Input
                  value={createForm.partner_name}
                  onChange={({ detail }) => setCreateForm({ ...createForm, partner_name: detail.value })}
                  placeholder="Enter partner name"
                />
              </FormField>
              <FormField label="Contact Email">
                <Input
                  value={createForm.contact_email || ''}
                  onChange={({ detail }) => setCreateForm({ ...createForm, contact_email: detail.value })}
                  type="email"
                  placeholder="Enter contact email"
                />
              </FormField>
              <FormField label="Pricing Tier" description="Sets the request quota and rate limits via the tier's usage plan.">
                <Select
                  selectedOption={{ label: createForm.pricing_tier, value: createForm.pricing_tier }}
                  onChange={({ detail }) => setCreateForm({ ...createForm, pricing_tier: detail.selectedOption.value as CreatePartnerRequest['pricing_tier'] })}
                  options={[
                    { label: 'basic', value: 'basic' },
                    { label: 'standard', value: 'standard' },
                    { label: 'premium', value: 'premium' },
                    { label: 'enterprise', value: 'enterprise' },
                  ]}
                />
              </FormField>
              <FormField label="Identity" description="Direct partners get an API key now; marketplace partners authenticate through the marketplace.">
                <Select
                  selectedOption={{ label: createForm.identity_source || 'direct', value: createForm.identity_source || 'direct' }}
                  onChange={({ detail }) => setCreateForm({ ...createForm, identity_source: detail.selectedOption.value as CreatePartnerRequest['identity_source'] })}
                  options={[
                    { label: 'direct', value: 'direct' },
                    { label: 'marketplace', value: 'marketplace' },
                  ]}
                />
              </FormField>
              {createForm.identity_source === 'marketplace' && (
                <FormField label="Marketplace type">
                  <Input
                    value={createForm.marketplace_type || ''}
                    onChange={({ detail }) => setCreateForm({ ...createForm, marketplace_type: detail.value })}
                    placeholder="e.g. aws"
                  />
                </FormField>
              )}
              <FormField label="Allowed Endpoints" description="None selected = all key-gated operations allowed.">
                <SpaceBetween size="xs">
                  {KEY_GATED_OPERATIONS.map((op) => (
                    <Checkbox
                      key={op.id}
                      checked={(createForm.allowed_endpoints || []).includes(op.id)}
                      onChange={({ detail }) => setCreateForm({ ...createForm, allowed_endpoints: toggleEndpoint(createForm.allowed_endpoints, op.id, detail.checked) })}
                    >
                      {op.label}
                    </Checkbox>
                  ))}
                </SpaceBetween>
              </FormField>
            </SpaceBetween>
          </Form>
        </Modal>

        {/* Key reveal: the only time a key value is visible */}
        <Modal
          visible={keyReveal !== null}
          onDismiss={() => setKeyReveal(null)}
          header="API key — copy it now"
          footer={
            <Box float="right">
              <Button variant="primary" onClick={() => setKeyReveal(null)}>Done</Button>
            </Box>
          }
        >
          {keyReveal && (
            <SpaceBetween size="m">
              <Alert type="warning" header="This value is shown once">
                The key for <b>{keyReveal.partner.partner_name}</b> is not stored and cannot be retrieved later.
                Share it with the partner securely; rotate the key if it is lost.
              </Alert>
              {keyReveal.key_value ? (
                <CopyToClipboard
                  copyButtonText="Copy key"
                  copyErrorText="Could not copy"
                  copySuccessText="Key copied"
                  textToCopy={keyReveal.key_value}
                  variant="inline"
                />
              ) : (
                <Box>No API key: this partner authenticates through its marketplace identity.</Box>
              )}
              {keyReveal.key && (
                <Box color="text-body-secondary">
                  Key ID {keyReveal.key.key_id} · usage plan {keyReveal.key.usage_plan_id} · send as the <code>x-api-key</code> header.
                </Box>
              )}
            </SpaceBetween>
          )}
        </Modal>

        {/* Delete confirmation */}
        <Modal
          visible={confirmDelete !== null}
          onDismiss={() => setConfirmDelete(null)}
          header="Delete partner?"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setConfirmDelete(null)}>Cancel</Button>
                <Button variant="primary" onClick={handleDeletePartner} loading={busyPartnerId === confirmDelete?.partner_id}>
                  Delete permanently
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          {confirmDelete && (
            <Alert type="warning">
              Deleting <b>{confirmDelete.partner_name}</b> revokes and removes all of its API keys immediately. This cannot be undone.
            </Alert>
          )}
        </Modal>
      </SpaceBetween>
    </Container>
  );
}
