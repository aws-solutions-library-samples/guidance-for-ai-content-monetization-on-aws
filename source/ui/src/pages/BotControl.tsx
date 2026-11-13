import React, { useState, useEffect } from 'react'
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
  ExpandableSection,
  Tabs,
  Toggle,
  Input,
  FormField,
  Modal,
  Form,
  Select,
  Textarea,
  AreaChart
} from '@cloudscape-design/components'

import { 
  botControlService,
  BotRule,
  BotActivity,
  BotStats,
  CreateBotRuleRequest
} from '../services/botControlService'

export default function BotControl() {
  const [selectedTab, setSelectedTab] = useState('overview')
  const [ruleFilter, setRuleFilter] = useState('')
  const [botRules, setBotRules] = useState<BotRule[]>([])
  const [botActivity, setBotActivity] = useState<BotActivity | null>(null)
  const [botStats, setBotStats] = useState<BotStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  // Rule management state
  const [showCreateRuleModal, setShowCreateRuleModal] = useState(false)
  const [showEditRuleModal, setShowEditRuleModal] = useState(false)
  const [selectedRule, setSelectedRule] = useState<BotRule | null>(null)
  const [createRuleLoading, setCreateRuleLoading] = useState(false)
  const [updateRuleLoading, setUpdateRuleLoading] = useState(false)
  const [deleteRuleLoading, setDeleteRuleLoading] = useState(false)
  
  // Form state for rule creation/editing
  const [ruleForm, setRuleForm] = useState<CreateBotRuleRequest>({
    rule_name: '',
    description: '',
    action: 'block',
    priority: 100,
    conditions: {},
    status: 'active'
  })
  const [conditionsJson, setConditionsJson] = useState('')

  // Load data on component mount
  useEffect(() => {
    loadBotControlData()
  }, [])

  const loadBotControlData = async () => {
    try {
      setLoading(true)
      setError(null)
      
      const [rules, activity, stats] = await Promise.all([
        botControlService.getBotRules(),
        botControlService.getBotActivity({ time_period: 'last_24_hours' }),
        botControlService.getBotStats('last_24_hours')
      ])
      
      setBotRules(rules)
      setBotActivity(activity)
      setBotStats(stats)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bot control data')
      console.error('Error loading bot control data:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleRefresh = () => {
    loadBotControlData()
  }

  const handleCreateRule = async () => {
    try {
      setCreateRuleLoading(true)
      
      // Parse conditions JSON
      let conditions = {}
      if (conditionsJson.trim()) {
        try {
          conditions = JSON.parse(conditionsJson)
        } catch {
          setError('Invalid JSON in conditions field')
          return
        }
      }

      const ruleData = {
        ...ruleForm,
        conditions
      }

      // Validate conditions
      const validation = botControlService.validateRuleConditions(conditions)
      if (!validation.valid) {
        setError(`Invalid conditions: ${validation.errors.join(', ')}`)
        return
      }

      await botControlService.createBotRule(ruleData)
      setShowCreateRuleModal(false)
      resetRuleForm()
      await loadBotControlData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create rule')
    } finally {
      setCreateRuleLoading(false)
    }
  }

  const handleUpdateRule = async () => {
    if (!selectedRule) return

    try {
      setUpdateRuleLoading(true)
      
      // Parse conditions JSON
      let conditions = {}
      if (conditionsJson.trim()) {
        try {
          conditions = JSON.parse(conditionsJson)
        } catch {
          setError('Invalid JSON in conditions field')
          return
        }
      }

      const updates = {
        rule_name: ruleForm.rule_name,
        description: ruleForm.description,
        action: ruleForm.action,
        priority: ruleForm.priority,
        conditions,
        status: ruleForm.status
      }

      await botControlService.updateBotRule(selectedRule.rule_id, updates)
      setShowEditRuleModal(false)
      resetRuleForm()
      await loadBotControlData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update rule')
    } finally {
      setUpdateRuleLoading(false)
    }
  }

  const handleDeleteRule = async (ruleId: string) => {
    try {
      setDeleteRuleLoading(true)
      await botControlService.deleteBotRule(ruleId)
      await loadBotControlData()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete rule')
    } finally {
      setDeleteRuleLoading(false)
    }
  }

  const handleEditRule = (rule: BotRule) => {
    setSelectedRule(rule)
    setRuleForm({
      rule_name: rule.rule_name,
      description: rule.description,
      action: rule.action,
      priority: rule.priority,
      conditions: rule.conditions,
      status: rule.status
    })
    setConditionsJson(JSON.stringify(rule.conditions, null, 2))
    setShowEditRuleModal(true)
  }

  const handleUseTemplate = (template: { name: string; description: string; action: CreateBotRuleRequest['action']; priority: number }) => {
    setRuleForm({
      rule_name: template.name,
      description: template.description,
      action: template.action,
      priority: template.priority,
      conditions: template.conditions,
      status: 'active'
    })
    setConditionsJson(JSON.stringify(template.conditions, null, 2))
  }

  const resetRuleForm = () => {
    setRuleForm({
      rule_name: '',
      description: '',
      action: 'block',
      priority: 100,
      conditions: {},
      status: 'active'
    })
    setConditionsJson('')
    setSelectedRule(null)
  }

  const getActionColor = (action: string) => {
    switch (action) {
      case 'block': return 'red'
      case 'allow': return 'green'
      case 'count': return 'blue'
      case 'rate_limit': return 'grey'
      default: return 'blue'
    }
  }

  const getPriorityColor = (priority: number) => {
    if (priority <= 100) return 'red'    // High priority
    if (priority <= 200) return 'blue'   // Medium priority
    return 'grey'                        // Low priority
  }

  const getSyncStatusColor = (status: string) => {
    switch (status) {
      case 'synced': return 'success'
      case 'pending': return 'in-progress'
      case 'error': return 'error'
      default: return 'stopped'
    }
  }

  const filteredRules = botRules.filter(rule =>
    rule.rule_name.toLowerCase().includes(ruleFilter.toLowerCase()) ||
    rule.description.toLowerCase().includes(ruleFilter.toLowerCase())
  )

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Monitor and manage bot control policies to protect your content while allowing legitimate access"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh Data
              </Button>
              <Button href="/system-status">System Status</Button>
            </SpaceBetween>
          }
        >
          Bot Control Dashboard
        </Header>

        {error && (
          <Alert type="error" header="Error Loading Bot Control Data">
            {error}
          </Alert>
        )}

        {/* Bot Control Status */}
        <Alert 
          type={botStats?.waf_health === 'healthy' ? 'success' : 'warning'} 
          header={`Bot Control ${botStats?.waf_health === 'healthy' ? 'Active' : 'Degraded'}`}
        >
          AWS WAF Bot Control is {botStats?.waf_health === 'healthy' ? 'actively protecting' : 'experiencing issues with'} your content. 
          {botStats?.traffic_summary.blocked_requests.toLocaleString() || 0} malicious requests blocked in the last 24 hours.
          {botStats?.sync_status && (
            <> Sync Health: {botStats.sync_status.sync_health}% ({botStats.sync_status.synced_rules}/{botStats.rule_summary.total_rules} rules synced)</>
          )}
        </Alert>

        <Tabs
          activeTabId={selectedTab}
          onChange={({ detail }) => setSelectedTab(detail.activeTabId)}
          tabs={[
            {
              id: 'overview',
              label: 'Overview',
              content: (
                <SpaceBetween size="l">
                  {/* Bot Statistics */}
                  <Container>
                    <Header variant="h2">Bot Activity Statistics (24 Hours)</Header>
                    <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-info">{botStats?.traffic_summary.total_requests.toLocaleString() || 0}</Box>
                        <Box variant="small">Total Requests</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-success">{botStats?.traffic_summary.allowed_requests.toLocaleString() || 0}</Box>
                        <Box variant="small">Allowed Requests</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-error">{botStats?.traffic_summary.blocked_requests.toLocaleString() || 0}</Box>
                        <Box variant="small">Blocked Requests</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-warning">{botStats?.traffic_summary.block_rate || 0}%</Box>
                        <Box variant="small">Block Rate</Box>
                      </Box>
                    </Grid>
                  </Container>

                  {/* Traffic Chart */}
                  {botActivity && botActivity.datapoints.length > 0 && (
                    <Container>
                      <Header variant="h2">Traffic Trends</Header>
                      <AreaChart
                        series={[
                          {
                            title: 'Blocked Requests',
                            type: 'area',
                            data: botActivity.datapoints.map(dp => ({
                              x: new Date(dp.timestamp),
                              y: dp.blocked
                            })),
                            color: '#d13212'
                          },
                          {
                            title: 'Allowed Requests',
                            type: 'area',
                            data: botActivity.datapoints.map(dp => ({
                              x: new Date(dp.timestamp),
                              y: dp.allowed
                            })),
                            color: '#037f0c'
                          }
                        ]}
                        xDomain={botActivity.datapoints.length > 0 ? [
                          new Date(botActivity.datapoints[0].timestamp),
                          new Date(botActivity.datapoints[botActivity.datapoints.length - 1].timestamp)
                        ] : undefined}
                        yDomain={[0, Math.max(...botActivity.datapoints.map(dp => dp.blocked + dp.allowed))]}
                        i18nStrings={{
                          filterLabel: "Filter displayed data",
                          filterPlaceholder: "Filter data",
                          filterSelectedAriaLabel: "selected",
                          legendAriaLabel: "Legend",
                          chartAriaRoleDescription: "line chart",
                          xTickFormatter: (e) => new Date(e).toLocaleTimeString(),
                          yTickFormatter: (e) => e.toString()
                        }}
                        ariaLabel="Bot traffic trends over time"
                        height={300}
                        hideFilter
                        hideLegend={false}
                        loading={loading}
                        loadingText="Loading chart data..."
                        recoveryText="Retry"
                        empty={
                          <Box textAlign="center" color="inherit">
                            <b>No data available</b>
                            <Box variant="p" color="inherit">
                              There is no data available for the selected time range.
                            </Box>
                          </Box>
                        }
                      />
                    </Container>
                  )}

                  {/* Rule Effectiveness */}
                  <Container>
                    <Header variant="h2">Most Effective Rules</Header>
                    <Table
                      columnDefinitions={[
                        {
                          id: 'rule_name',
                          header: 'Rule Name',
                          cell: item => <Badge color={getActionColor(item.action)}>{item.rule_name}</Badge>
                        },
                        {
                          id: 'action',
                          header: 'Action',
                          cell: item => (
                            <StatusIndicator type={item.action === 'block' ? 'error' : 'success'}>
                              {item.action.charAt(0).toUpperCase() + item.action.slice(1)}
                            </StatusIndicator>
                          )
                        },
                        {
                          id: 'blocked_requests',
                          header: 'Blocked Requests',
                          cell: item => item.blocked_requests.toLocaleString()
                        },
                        {
                          id: 'effectiveness_score',
                          header: 'Effectiveness Score',
                          cell: item => `${item.effectiveness_score.toFixed(1)}%`
                        }
                      ]}
                      items={botStats?.rule_effectiveness || []}
                      loading={loading}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <b>No rule effectiveness data</b>
                          <Box variant="p" color="inherit">
                            No rules have blocked requests yet.
                          </Box>
                        </Box>
                      }
                    />
                  </Container>

                  {/* Rule Summary */}
                  <Container>
                    <Header variant="h2">Rule Summary</Header>
                    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-info">{botStats?.rule_summary.total_rules || 0}</Box>
                        <Box variant="small">Total Rules</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-success">{botStats?.rule_summary.active_rules || 0}</Box>
                        <Box variant="small">Active Rules</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-warning">{botStats?.rule_summary.inactive_rules || 0}</Box>
                        <Box variant="small">Inactive Rules</Box>
                      </Box>
                    </Grid>
                  </Container>
                </SpaceBetween>
              )
            },
            {
              id: 'rules',
              label: 'Bot Control Rules',
              content: (
                <SpaceBetween size="l">
                  {/* Rule Management */}
                  <Container>
                    <Header 
                      variant="h2"
                      description="Manage IAB-compliant bot control rules"
                      actions={
                        <Button 
                          variant="primary" 
                          onClick={() => setShowCreateRuleModal(true)}
                        >
                          Add New Rule
                        </Button>
                      }
                    >
                      Bot Control Rules
                    </Header>
                    
                    <SpaceBetween size="m">
                      <FormField label="Filter rules">
                        <Input
                          value={ruleFilter}
                          onChange={({ detail }) => setRuleFilter(detail.value)}
                          placeholder="Search rules by name or description..."
                        />
                      </FormField>

                      <Cards
                        cardDefinition={{
                          header: item => (
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <Box variant="h3">{item.rule_name}</Box>
                              <div style={{ display: 'flex', gap: '8px' }}>
                                <Badge color={getPriorityColor(item.priority)}>Priority: {item.priority}</Badge>
                                <Badge color={getActionColor(item.action)}>{item.action}</Badge>
                                <StatusIndicator type={item.status === 'active' ? 'success' : 'stopped'}>
                                  {item.status}
                                </StatusIndicator>
                                <StatusIndicator type={getSyncStatusColor(item.sync_status)}>
                                  {item.sync_status}
                                </StatusIndicator>
                              </div>
                            </div>
                          ),
                          sections: [
                            {
                              id: 'description',
                              content: item => <Box variant="p">{item.description}</Box>
                            },
                            {
                              id: 'conditions',
                              content: item => (
                                <ExpandableSection headerText="Rule Conditions" variant="container">
                                  <Box variant="code">
                                    {JSON.stringify(item.conditions, null, 2)}
                                  </Box>
                                </ExpandableSection>
                              )
                            },
                            {
                              id: 'metadata',
                              content: item => (
                                <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
                                  <div>
                                    <Box variant="awsui-key-label">Created</Box>
                                    <Box>{new Date(item.created_at).toLocaleDateString()}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Updated</Box>
                                    <Box>{new Date(item.updated_at).toLocaleDateString()}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">WAF Rule ID</Box>
                                    <Box>{item.waf_rule_id || 'Not synced'}</Box>
                                  </div>
                                  <div>
                                    <Box variant="awsui-key-label">Last Synced</Box>
                                    <Box>{item.last_synced ? new Date(item.last_synced).toLocaleString() : 'Never'}</Box>
                                  </div>
                                </Grid>
                              )
                            },
                            {
                              id: 'actions',
                              content: item => (
                                <SpaceBetween direction="horizontal" size="xs">
                                  <Button 
                                    onClick={() => handleEditRule(item)}
                                    loading={updateRuleLoading}
                                  >
                                    Edit
                                  </Button>
                                  <Button 
                                    onClick={() => handleDeleteRule(item.rule_id)}
                                    loading={deleteRuleLoading}
                                    variant="normal"
                                  >
                                    Delete
                                  </Button>
                                </SpaceBetween>
                              )
                            }
                          ]
                        }}
                        cardsPerRow={[{ cards: 1 }]}
                        items={filteredRules}
                        loading={loading}
                        empty={
                          <Box textAlign="center" color="inherit">
                            <b>No rules found</b>
                            <Box variant="p" color="inherit">
                              {ruleFilter ? 'Try adjusting your search criteria.' : 'No bot control rules are configured.'}
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
              id: 'settings',
              label: 'Settings',
              content: (
                <SpaceBetween size="l">
                  {/* Bot Control Settings */}
                  <Container>
                    <Header variant="h2">Bot Control Configuration</Header>
                    <SpaceBetween size="m">
                      <FormField
                        label="Enable Bot Control"
                        description="Turn on/off AWS WAF Bot Control protection"
                      >
                        <Toggle checked={true}>Bot Control Enabled</Toggle>
                      </FormField>

                      <FormField
                        label="Strict Mode"
                        description="Enable stricter bot detection and blocking"
                      >
                        <Toggle checked={false}>Strict Mode</Toggle>
                      </FormField>

                      <FormField
                        label="Log All Requests"
                        description="Log all bot requests for analysis (may increase costs)"
                      >
                        <Toggle checked={true}>Full Logging Enabled</Toggle>
                      </FormField>

                      <FormField
                        label="Rate Limiting"
                        description="Enable rate limiting for API endpoints"
                      >
                        <Toggle checked={true}>Rate Limiting Enabled</Toggle>
                      </FormField>
                    </SpaceBetween>
                  </Container>

                  {/* IAB Compliance */}
                  <Container>
                    <Header variant="h2">IAB Compliance</Header>
                    <Alert type="info" header="IAB Framework Compliance">
                      Your bot control configuration follows IAB (Interactive Advertising Bureau) guidelines for responsible AI training data access and content monetization.
                    </Alert>
                    
                    <SpaceBetween size="m">
                      <ExpandableSection headerText="Compliant Bot Categories" variant="container">
                        <SpaceBetween size="s">
                          <div>✅ <strong>Search Engines:</strong> Google, Bing, DuckDuckGo, Baidu, Yandex</div>
                          <div>✅ <strong>Social Media:</strong> Facebook, Twitter, LinkedIn, WhatsApp</div>
                          <div>✅ <strong>Monitoring Services:</strong> Pingdom, UptimeRobot, GTmetrix</div>
                          <div>❌ <strong>AI Training Bots:</strong> GPTBot, ChatGPT, CCBot, Claude-Web, Bard</div>
                          <div>❌ <strong>Content Scrapers:</strong> Scrapy, curl, wget, python-requests</div>
                          <div>❌ <strong>SEO Competitors:</strong> AhrefsBot, SemrushBot, MJ12bot</div>
                        </SpaceBetween>
                      </ExpandableSection>

                      <ExpandableSection headerText="Revenue Protection" variant="container">
                        <Box variant="p">
                          Bot control helps protect your content monetization by ensuring only authorized 
                          AI agents can access your content through proper API channels, while blocking 
                          unauthorized scraping and training data collection.
                        </Box>
                      </ExpandableSection>
                    </SpaceBetween>
                  </Container>
                </SpaceBetween>
              )
            }
          ]}
        />

        {/* Create Rule Modal */}
        <Modal
          visible={showCreateRuleModal}
          onDismiss={() => {
            setShowCreateRuleModal(false)
            resetRuleForm()
          }}
          header="Create Bot Control Rule"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button 
                  variant="link" 
                  onClick={() => {
                    setShowCreateRuleModal(false)
                    resetRuleForm()
                  }}
                >
                  Cancel
                </Button>
                <Button 
                  variant="primary" 
                  onClick={handleCreateRule}
                  loading={createRuleLoading}
                >
                  Create Rule
                </Button>
              </SpaceBetween>
            </Box>
          }
          size="large"
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Rule Name" errorText={!ruleForm.rule_name ? 'Rule name is required' : ''}>
                <Input
                  value={ruleForm.rule_name}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, rule_name: detail.value })}
                  placeholder="Enter rule name"
                />
              </FormField>

              <FormField label="Description" errorText={!ruleForm.description ? 'Description is required' : ''}>
                <Textarea
                  value={ruleForm.description}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, description: detail.value })}
                  placeholder="Enter rule description"
                  rows={3}
                />
              </FormField>

              <FormField label="Action">
                <Select
                  selectedOption={{ label: ruleForm.action, value: ruleForm.action }}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, action: detail.selectedOption.value as CreateBotRuleRequest['action'] })}
                  options={[
                    { label: 'block', value: 'block' },
                    { label: 'allow', value: 'allow' },
                    { label: 'count', value: 'count' },
                    { label: 'rate_limit', value: 'rate_limit' }
                  ]}
                />
              </FormField>

              <FormField label="Priority" description="Lower numbers = higher priority (1-1000)">
                <Input
                  type="number"
                  value={ruleForm.priority.toString()}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, priority: parseInt(detail.value) || 100 })}
                />
              </FormField>

              <FormField label="Status">
                <Select
                  selectedOption={{ label: ruleForm.status, value: ruleForm.status }}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, status: detail.selectedOption.value as CreateBotRuleRequest['status'] })}
                  options={[
                    { label: 'active', value: 'active' },
                    { label: 'inactive', value: 'inactive' }
                  ]}
                />
              </FormField>

              <FormField 
                label="Rule Conditions (JSON)" 
                description="Define the conditions for this rule in JSON format"
              >
                <Textarea
                  value={conditionsJson}
                  onChange={({ detail }) => setConditionsJson(detail.value)}
                  placeholder='{"user_agent": ["GPTBot", "ChatGPT"]}'
                  rows={8}
                />
              </FormField>

              <ExpandableSection headerText="Rule Templates" variant="container">
                <SpaceBetween size="s">
                  {botControlService.getRuleTemplates().map((template, index) => (
                    <div key={index} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px', border: '1px solid #e9ebed', borderRadius: '4px' }}>
                      <div>
                        <Box variant="strong">{template.name}</Box>
                        <Box variant="small">{template.description}</Box>
                      </div>
                      <Button size="small" onClick={() => handleUseTemplate(template)}>
                        Use Template
                      </Button>
                    </div>
                  ))}
                </SpaceBetween>
              </ExpandableSection>
            </SpaceBetween>
          </Form>
        </Modal>

        {/* Edit Rule Modal */}
        <Modal
          visible={showEditRuleModal}
          onDismiss={() => {
            setShowEditRuleModal(false)
            resetRuleForm()
          }}
          header={`Edit Rule: ${selectedRule?.rule_name}`}
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button 
                  variant="link" 
                  onClick={() => {
                    setShowEditRuleModal(false)
                    resetRuleForm()
                  }}
                >
                  Cancel
                </Button>
                <Button 
                  variant="primary" 
                  onClick={handleUpdateRule}
                  loading={updateRuleLoading}
                >
                  Update Rule
                </Button>
              </SpaceBetween>
            </Box>
          }
          size="large"
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Rule Name">
                <Input
                  value={ruleForm.rule_name}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, rule_name: detail.value })}
                  placeholder="Enter rule name"
                />
              </FormField>

              <FormField label="Description">
                <Textarea
                  value={ruleForm.description}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, description: detail.value })}
                  placeholder="Enter rule description"
                  rows={3}
                />
              </FormField>

              <FormField label="Action">
                <Select
                  selectedOption={{ label: ruleForm.action, value: ruleForm.action }}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, action: detail.selectedOption.value as CreateBotRuleRequest['action'] })}
                  options={[
                    { label: 'block', value: 'block' },
                    { label: 'allow', value: 'allow' },
                    { label: 'count', value: 'count' },
                    { label: 'rate_limit', value: 'rate_limit' }
                  ]}
                />
              </FormField>

              <FormField label="Priority">
                <Input
                  type="number"
                  value={ruleForm.priority.toString()}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, priority: parseInt(detail.value) || 100 })}
                />
              </FormField>

              <FormField label="Status">
                <Select
                  selectedOption={{ label: ruleForm.status, value: ruleForm.status }}
                  onChange={({ detail }) => setRuleForm({ ...ruleForm, status: detail.selectedOption.value as CreateBotRuleRequest['status'] })}
                  options={[
                    { label: 'active', value: 'active' },
                    { label: 'inactive', value: 'inactive' }
                  ]}
                />
              </FormField>

              <FormField label="Rule Conditions (JSON)">
                <Textarea
                  value={conditionsJson}
                  onChange={({ detail }) => setConditionsJson(detail.value)}
                  placeholder='{"user_agent": ["GPTBot", "ChatGPT"]}'
                  rows={8}
                />
              </FormField>
            </SpaceBetween>
          </Form>
        </Modal>
      </SpaceBetween>
    </Container>
  )
}