import React, { useState } from 'react'
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
  Tabs
} from '@cloudscape-design/components'

// Mock system status data
const systemComponents = [
  {
    id: 'content-ingestion',
    name: 'Content Ingestion',
    status: 'Operational',
    uptime: '99.9%',
    lastCheck: '2 minutes ago',
    description: 'S3 bucket monitoring and event processing',
    metrics: { processed: 1247, errors: 2, avgTime: '1.2s' }
  },
  {
    id: 'medialake-pipeline',
    name: 'MediaLake Pipeline',
    status: 'Operational',
    uptime: '99.7%',
    lastCheck: '1 minute ago',
    description: 'Content processing and IAB taxonomy classification',
    metrics: { processed: 1245, errors: 0, avgTime: '15.3s' }
  },
  {
    id: 'vector-database',
    name: 'Vector Database',
    status: 'Operational',
    uptime: '100%',
    lastCheck: '30 seconds ago',
    description: 'OpenSearch cluster for semantic embeddings',
    metrics: { indexed: 1245, queries: 15420, avgLatency: '45ms' }
  },
  {
    id: 'bot-control',
    name: 'Bot Control',
    status: 'Active',
    uptime: '100%',
    lastCheck: '15 seconds ago',
    description: 'AWS WAF with IAB bot management rules',
    metrics: { blocked: 1847, allowed: 98234, rules: 47 }
  },
  {
    id: 'api-gateway',
    name: 'API Gateway',
    status: 'Operational',
    uptime: '99.8%',
    lastCheck: '45 seconds ago',
    description: 'Content discovery API endpoints',
    metrics: { requests: 15420, errors: 12, avgLatency: '120ms' }
  },
  {
    id: 'analytics',
    name: 'Analytics Engine',
    status: 'Operational',
    uptime: '99.9%',
    lastCheck: '1 minute ago',
    description: 'Real-time monitoring and KPI tracking',
    metrics: { events: 45678, dashboards: 5, alerts: 0 }
  }
]

// Mock alerts data
const recentAlerts = [
  {
    id: 'alert-001',
    type: 'warning',
    message: 'High API request volume detected',
    component: 'API Gateway',
    timestamp: '2024-01-15T14:25:00Z',
    status: 'acknowledged'
  },
  {
    id: 'alert-002',
    type: 'info',
    message: 'Scheduled maintenance completed successfully',
    component: 'Vector Database',
    timestamp: '2024-01-15T14:20:00Z',
    status: 'resolved'
  },
  {
    id: 'alert-003',
    type: 'success',
    message: 'Bot control rules updated',
    component: 'Bot Control',
    timestamp: '2024-01-15T14:15:00Z',
    status: 'resolved'
  }
]

export default function SystemManagement() {
  const [selectedTab, setSelectedTab] = useState('overview')

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'operational':
      case 'active':
        return 'success'
      case 'warning':
        return 'warning'
      case 'error':
      case 'down':
        return 'error'
      default:
        return 'info'
    }
  }

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Technical system monitoring and performance metrics for administrators and technical users."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh">Refresh Status</Button>
              <Button href="/dashboard">View Analytics</Button>
            </SpaceBetween>
          }
        >
          System Management
        </Header>

        <Alert type="success" header="All Systems Operational">
          The AI Content Monetization API framework is running normally. All core components are operational and processing content as expected.
        </Alert>

        <Tabs
          activeTabId={selectedTab}
          onChange={({ detail }) => setSelectedTab(detail.activeTabId)}
          tabs={[
            {
              id: 'overview',
              label: 'System Overview',
              content: (
                <SpaceBetween size="l">
                  {/* System Components Status */}
                  <Container>
                    <Header variant="h2">Component Status</Header>
                    <Cards
                      cardDefinition={{
                        header: item => (
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Box variant="h3">{item.name}</Box>
                            <StatusIndicator type={getStatusColor(item.status)}>
                              {item.status}
                            </StatusIndicator>
                          </div>
                        ),
                        sections: [
                          {
                            id: 'description',
                            content: item => <Box variant="small">{item.description}</Box>
                          },
                          {
                            id: 'metrics',
                            content: item => (
                              <SpaceBetween size="xs">
                                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                  <Box variant="awsui-key-label">Uptime</Box>
                                  <Box>{item.uptime}</Box>
                                </div>
                                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                  <Box variant="awsui-key-label">Last Check</Box>
                                  <Box>{item.lastCheck}</Box>
                                </div>
                              </SpaceBetween>
                            )
                          }
                        ]
                      }}
                      cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }, { minWidth: 800, cards: 3 }]}
                      items={systemComponents}
                    />
                  </Container>

                  {/* Recent Alerts */}
                  <Container>
                    <Header variant="h2">Recent Alerts</Header>
                    <Table
                      columnDefinitions={[
                        {
                          id: 'type',
                          header: 'Type',
                          cell: item => (
                            <StatusIndicator type={
                              item.type === 'warning' ? 'warning' :
                              item.type === 'error' ? 'error' :
                              item.type === 'success' ? 'success' : 'info'
                            }>
                              {item.type.charAt(0).toUpperCase() + item.type.slice(1)}
                            </StatusIndicator>
                          )
                        },
                        {
                          id: 'message',
                          header: 'Message',
                          cell: item => item.message
                        },
                        {
                          id: 'component',
                          header: 'Component',
                          cell: item => <Badge>{item.component}</Badge>
                        },
                        {
                          id: 'timestamp',
                          header: 'Time',
                          cell: item => new Date(item.timestamp).toLocaleString()
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: item => (
                            <Badge color={
                              item.status === 'resolved' ? 'green' :
                              item.status === 'acknowledged' ? 'blue' : 'grey'
                            }>
                              {item.status}
                            </Badge>
                          )
                        }
                      ]}
                      items={recentAlerts}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <b>No recent alerts</b>
                          <Box variant="p" color="inherit">
                            System is running smoothly with no alerts.
                          </Box>
                        </Box>
                      }
                    />
                  </Container>
                </SpaceBetween>
              )
            },
            {
              id: 'performance',
              label: 'Performance Metrics',
              content: (
                <SpaceBetween size="l">
                  {/* Performance Overview */}
                  <Container>
                    <Header variant="h2">System Performance</Header>
                    <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                      <SpaceBetween size="m">
                        <Box variant="h3">API Performance</Box>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Average Latency</Box>
                          <Box>120ms</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Requests/Hour</Box>
                          <Box>15,420</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Error Rate</Box>
                          <Box>0.08%</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Uptime</Box>
                          <Box>99.8%</Box>
                        </div>
                      </SpaceBetween>
                      <SpaceBetween size="m">
                        <Box variant="h3">Processing Performance</Box>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Avg Processing Time</Box>
                          <Box>15.3s</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Queue Length</Box>
                          <Box>4 items</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Success Rate</Box>
                          <Box>99.84%</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Throughput</Box>
                          <Box>240/hour</Box>
                        </div>
                      </SpaceBetween>
                    </Grid>
                  </Container>

                  {/* Resource Utilization */}
                  <Container>
                    <Header variant="h2">Resource Utilization</Header>
                    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
                      <SpaceBetween size="m">
                        <Box variant="h3">Lambda Functions</Box>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Invocations/Hour</Box>
                          <Box>15,420</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Avg Duration</Box>
                          <Box>1.2s</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Error Rate</Box>
                          <Box>0.02%</Box>
                        </div>
                      </SpaceBetween>
                      <SpaceBetween size="m">
                        <Box variant="h3">DynamoDB</Box>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Read Capacity</Box>
                          <Box>85% utilized</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Write Capacity</Box>
                          <Box>42% utilized</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Throttled Requests</Box>
                          <Box>0</Box>
                        </div>
                      </SpaceBetween>
                      <SpaceBetween size="m">
                        <Box variant="h3">OpenSearch</Box>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Cluster Health</Box>
                          <Box>Green</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Storage Used</Box>
                          <Box>2.4 GB</Box>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Box variant="awsui-key-label">Search Latency</Box>
                          <Box>45ms</Box>
                        </div>
                      </SpaceBetween>
                    </Grid>
                  </Container>

                  {/* Cost Analysis */}
                  <Container>
                    <Header variant="h2">Cost Analysis (Last 30 Days)</Header>
                    <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-info">$127.45</Box>
                        <Box variant="small">Lambda Costs</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-info">$89.32</Box>
                        <Box variant="small">DynamoDB Costs</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-info">$156.78</Box>
                        <Box variant="small">OpenSearch Costs</Box>
                      </Box>
                      <Box padding="l" variant="div">
                        <Box variant="h3" color="text-status-success">$373.55</Box>
                        <Box variant="small">Total Monthly Cost</Box>
                      </Box>
                    </Grid>
                  </Container>
                </SpaceBetween>
              )
            }
          ]}
        />
      </SpaceBetween>
    </Container>
  )
}