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
  Alert,
  ProgressBar,
  StatusIndicator,
} from '@cloudscape-design/components'

const demoScenarios = [
  {
    id: 'publisher-workflow',
    title: 'Publisher Content Upload Workflow',
    description: 'Demonstrate the complete publisher workflow from content upload to monetization.',
    duration: '5 minutes',
    difficulty: 'Beginner',
    steps: [
      'Upload content via Publisher Dashboard',
      'Content processed through MediaLake pipeline',
      'IAB taxonomy classification applied',
      'Vector embeddings generated',
      'Content becomes discoverable via API'
    ],
    status: 'Available',
    estimatedTime: '5 minutes'
  },
  {
    id: 'ai-discovery',
    title: 'AI Agent Content Discovery',
    description: 'Show how AI training systems discover and access content through the API.',
    steps: [
      'AI agent searches for relevant content',
      'Semantic search returns ranked results',
      'Agent requests access to specific content',
      'Bot control validates legitimate access',
      'Content delivered with usage tracking'
    ],
    status: 'Available',
    estimatedTime: '3 minutes'
  },
  {
    id: 'bot-control',
    title: 'Bot Control & Security',
    description: 'Demonstrate the bot control system blocking unauthorized scrapers.',
    steps: [
      'Legitimate AI bot makes request',
      'Request passes through WAF rules',
      'Unauthorized scraper attempts access',
      'WAF blocks malicious request',
      'Analytics track all activity'
    ],
    status: 'Available',
    estimatedTime: '4 minutes'
  },
  {
    id: 'end-to-end',
    title: 'Complete End-to-End Flow',
    description: 'Full demonstration of the entire IAB CoMP API ecosystem.',
    steps: [
      'Publisher uploads content',
      'Content processing and classification',
      'AI agent discovers content',
      'Access control and validation',
      'Revenue tracking and analytics',
      'Real-time monitoring dashboard'
    ],
    status: 'Available',
    estimatedTime: '10 minutes'
  }
]

const systemComponents = [
  {
    name: 'MediaLake Processing Pipeline',
    status: 'healthy',
    description: 'Content ingestion and processing system',
    metrics: { processed: 1247, pending: 3, errors: 0 }
  },
  {
    name: 'Bot Control (AWS WAF)',
    status: 'healthy',
    description: 'AI bot filtering and access control',
    metrics: { allowed: 15420, blocked: 2340, rate: '4.5%' }
  },
  {
    name: 'Vector Search (OpenSearch)',
    status: 'healthy',
    description: 'Semantic content discovery',
    metrics: { indexed: 1247, queries: 8920, avgLatency: '45ms' }
  },
  {
    name: 'Access Control API',
    status: 'healthy',
    description: 'Authentication and authorization',
    metrics: { requests: 52120, success: '99.8%', errors: 12 }
  }
]

export default function DemoController() {
  const [currentDemo, setCurrentDemo] = useState(null)
  const [demoProgress, setDemoProgress] = useState(0)
  const [isRunningDemo, setIsRunningDemo] = useState(false)

  const startDemo = async (demoId) => {
    setCurrentDemo(demoId)
    setIsRunningDemo(true)
    setDemoProgress(0)

    // Simulate demo progress
    for (let i = 0; i <= 100; i += 20) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      setDemoProgress(i)
    }

    setIsRunningDemo(false)
  }

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Interactive demonstration of the AI Content Monetization API framework"
      >
        IAB CoMP API - Demo Controller
      </Header>

      {/* Welcome Section */}
      <Container>
        <SpaceBetween size="m">
          <Box variant="h2">Welcome to the IAB CoMP API implementation on AWS Guidance</Box>
          <Box variant="p">
            This demonstration showcases a comprehensive implementation of the IAB (Interactive Advertising Bureau) 
            framework for managing AI bot access to publisher content. The system provides publishers with tools 
            to monetize their content while giving AI operators transparent access to high-quality training data.
          </Box>
          <Alert type="info" header="Demo Features">
            This demo includes real working components of the AI Content Monetization API framework, including 
            bot control, content discovery, access management, and real-time observability.
          </Alert>
        </SpaceBetween>
      </Container>

      {/* System Status */}
      <Container
        header={
          <Header variant="h2" description="Current system health and metrics">
            System Status
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
          {systemComponents.map((component, index) => (
            <Box key={index}>
              <SpaceBetween size="xs">
                <Box variant="strong">{component.name}</Box>
                <StatusIndicator type={component.status === 'healthy' ? 'success' : 'error'}>
                  {component.status.charAt(0).toUpperCase() + component.status.slice(1)}
                </StatusIndicator>
                <Box variant="small" color="text-body-secondary">
                  {component.description}
                </Box>
                <Box variant="small">
                  {Object.entries(component.metrics).map(([key, value]) => (
                    <div key={key}>{key}: {value}</div>
                  ))}
                </Box>
              </SpaceBetween>
            </Box>
          ))}
        </Grid>
      </Container>

      {/* Demo Scenarios */}
      <Container
        header={
          <Header variant="h2" description="Choose a demonstration scenario">
            Demo Scenarios
          </Header>
        }
      >
        <Cards
          ariaLabels={{
            itemSelectionLabel: (e, t) => `select ${t.title}`,
            selectionGroupLabel: "Demo selection"
          }}
          cardDefinition={{
            header: item => item.title,
            sections: [
              {
                id: "description",
                content: item => item.description
              },
              {
                id: "steps",
                header: "Demo Steps",
                content: item => (
                  <SpaceBetween size="xs">
                    {item.steps.map((step, index) => (
                      <Box key={index} variant="small">
                        {index + 1}. {step}
                      </Box>
                    ))}
                  </SpaceBetween>
                )
              },
              {
                id: "details",
                content: item => (
                  <SpaceBetween size="xs" direction="horizontal">
                    <Badge color="green">{item.status}</Badge>
                    <Box variant="small">~{item.estimatedTime}</Box>
                  </SpaceBetween>
                )
              },
              {
                id: "action",
                content: item => (
                  <Button 
                    variant="primary"
                    onClick={() => startDemo(item.id)}
                    disabled={isRunningDemo}
                    loading={isRunningDemo && currentDemo === item.id}
                  >
                    {isRunningDemo && currentDemo === item.id ? 'Running...' : 'Start Demo'}
                  </Button>
                )
              }
            ]
          }}
          cardsPerRow={[
            { cards: 1 },
            { minWidth: 500, cards: 2 }
          ]}
          items={demoScenarios}
          loadingText="Loading scenarios"
        />
      </Container>

      {/* Demo Progress */}
      {isRunningDemo && (
        <Container
          header={
            <Header variant="h2">
              Demo in Progress: {demoScenarios.find(d => d.id === currentDemo)?.title}
            </Header>
          }
        >
          <SpaceBetween size="m">
            <ProgressBar
              value={demoProgress}
              additionalInfo={`${demoProgress}% complete`}
              description="Demo execution progress"
            />
            <Alert type="info">
              The demo is running. You can monitor the progress above and see real-time 
              updates in the system components.
            </Alert>
          </SpaceBetween>
        </Container>
      )}

      {/* Quick Navigation */}
      <Container
        header={
          <Header variant="h2" description="Direct access to system components">
            Quick Navigation
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
          <SpaceBetween size="s">
            <Box variant="h3">Publisher Tools</Box>
            <Button href="/dashboard">Analytics Dashboard</Button>
            <Button href="/content/add">Add Content</Button>
          </SpaceBetween>
          <SpaceBetween size="s">
            <Box variant="h3">AI Operator Tools</Box>
            <Button href="/test">Test Console</Button>
            <Button variant="link" external>API Documentation</Button>
          </SpaceBetween>
          <SpaceBetween size="s">
            <Box variant="h3">Analytics</Box>
            <Button href="/dashboard">Analytics Dashboard</Button>
            <Button variant="link" external>System Logs</Button>
          </SpaceBetween>
          <SpaceBetween size="s">
            <Box variant="h3">Resources</Box>
            <Button variant="link" external>IAB Framework Guide</Button>
            <Button variant="link" external>GitHub Repository</Button>
          </SpaceBetween>
        </Grid>
      </Container>

      {/* Architecture Overview */}
      <Container
        header={
          <Header variant="h2" description="System architecture and data flow">
            Architecture Overview
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
          <Box>
            <Box variant="h3">Content Ingestion</Box>
            <Box variant="p">
              Publishers upload content through the web interface. Content is processed 
              through MediaLake pipelines for classification, embedding generation, and indexing.
            </Box>
          </Box>
          <Box>
            <Box variant="h3">Access Control</Box>
            <Box variant="p">
              AWS WAF Bot Control filters requests based on IAB framework recommendations. 
              Legitimate AI bots are allowed while scrapers and unauthorized access is blocked.
            </Box>
          </Box>
          <Box>
            <Box variant="h3">Content Discovery</Box>
            <Box variant="p">
              AI operators discover content through semantic search APIs. Vector embeddings 
              enable intelligent content matching based on training requirements.
            </Box>
          </Box>
        </Grid>
      </Container>
    </SpaceBetween>
  )
}