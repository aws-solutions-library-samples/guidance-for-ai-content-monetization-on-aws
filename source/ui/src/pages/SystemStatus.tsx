import { useState, useEffect } from 'react'
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  Button,
  Badge,
  StatusIndicator,
  ProgressBar,
  Table,
  Alert,
  Tabs
} from '@cloudscape-design/components'

import { contentService, ProcessingJob } from '../services/contentService'
import { useFeatureFlags } from '../contexts/FeatureFlagContext'
import LlmsTxtManagement from '../components/LlmsTxtManagement'

export default function SystemStatus() {
  const { demoDataEnabled } = useFeatureFlags()
  const [processingQueue, setProcessingQueue] = useState<ProcessingJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load processing status on component mount
  useEffect(() => {
    loadProcessingStatus()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, [])

  const loadProcessingStatus = async () => {
    try {
      setLoading(true)
      setError(null)
      const jobs = await contentService.getProcessingStatus(demoDataEnabled)
      setProcessingQueue(jobs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load processing status')
      console.error('Error loading processing status:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleRefresh = () => {
    loadProcessingStatus()
  }
  const getProcessingStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'completed':
        return 'success'
      case 'processing':
        return 'in-progress'
      case 'queued':
        return 'pending'
      case 'failed':
        return 'error'
      default:
        return 'info'
    }
  }

  // Calculate summary statistics
  const totalProcessed = processingQueue.filter(job => job.status === 'completed').length
  const currentlyProcessing = processingQueue.filter(job => job.status === 'processing').length
  const inQueue = processingQueue.filter(job => job.status === 'queued').length
  const failed = processingQueue.filter(job => job.status === 'failed').length

  const processingContent = (
    <SpaceBetween size="l">
      {error && (
        <Alert type="error" header="Error Loading Processing Status">
          {error}
        </Alert>
      )}

      <Alert type="info" header="Content Processing Pipeline">
        Your content goes through validation, IAB taxonomy classification, and vector embedding generation before becoming available to AI agents.
      </Alert>

      <Container
        header={
          <Header
            variant="h2"
            description="Track your content through each stage of the processing pipeline"
            actions={
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh status
              </Button>
            }
          >
            Content Processing Queue
          </Header>
        }
      >
        <Table
          columnDefinitions={[
            {
              id: 'filename',
              header: 'Content File',
              cell: item => item.filename
            },
            {
              id: 'status',
              header: 'Status',
              cell: item => (
                <StatusIndicator type={getProcessingStatusColor(item.status)}>
                  {item.status}
                </StatusIndicator>
              )
            },
            {
              id: 'stage',
              header: 'Current Stage',
              cell: item => <Badge>{item.stage}</Badge>
            },
            {
              id: 'progress',
              header: 'Progress',
              cell: item => (
                <ProgressBar
                  value={item.progress}
                  additionalInfo={`${item.progress}%`}
                  description={item.estimated_completion || 'Processing...'}
                />
              )
            },
            {
              id: 'submitted',
              header: 'Submitted',
              cell: item => new Date(item.created_at).toLocaleString()
            },
            {
              id: 'error',
              header: 'Error',
              cell: item => item.error_message ? (
                <Badge color="red">{item.error_message}</Badge>
              ) : null
            }
          ]}
          items={processingQueue}
          loading={loading}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No content in processing queue</b>
              <Box variant="p" color="inherit">
                Upload content to see processing status here.
              </Box>
              <Button variant="primary" href="/content/add">
                Add Content
              </Button>
            </Box>
          }
        />
      </Container>

      <Container header={<Header variant="h2">Processing Summary</Header>}>
        <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
          <Box padding="l" variant="div">
            <Box variant="h3" color="text-status-success">{totalProcessed}</Box>
            <Box variant="small">Total Processed</Box>
          </Box>
          <Box padding="l" variant="div">
            <Box variant="h3" color="text-status-info">{currentlyProcessing}</Box>
            <Box variant="small">Currently Processing</Box>
          </Box>
          <Box padding="l" variant="div">
            <Box variant="h3" color="text-status-warning">{inQueue}</Box>
            <Box variant="small">In Queue</Box>
          </Box>
          <Box padding="l" variant="div">
            <Box variant="h3" color="text-status-error">{failed}</Box>
            <Box variant="small">Failed (Needs Attention)</Box>
          </Box>
        </Grid>
      </Container>

      <Container header={<Header variant="h2">Processing Stages</Header>}>
        <SpaceBetween size="m">
          <Box>
            <Box variant="h3">1. Validation</Box>
            <Box variant="p">Content is validated for format, structure, and required fields.</Box>
          </Box>
          <Box>
            <Box variant="h3">2. IAB Classification</Box>
            <Box variant="p">Content is automatically categorized using the IAB Content Taxonomy framework.</Box>
          </Box>
          <Box>
            <Box variant="h3">3. Vector Embedding</Box>
            <Box variant="p">AI-powered embeddings are generated for semantic search capabilities.</Box>
          </Box>
          <Box>
            <Box variant="h3">4. Available</Box>
            <Box variant="p">Content is ready for discovery by AI agents through the API.</Box>
          </Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  )

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Manage content processing and AI discovery manifests."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button href="/content/add">Add Content</Button>
            <Button variant="primary" href="/dashboard">View Analytics</Button>
          </SpaceBetween>
        }
      >
        Enrich
      </Header>
      <Tabs
        tabs={[
          {
            label: 'Processing status',
            id: 'processing',
            content: processingContent
          },
          {
            label: 'llms.txt',
            id: 'llms-txt',
            content: <LlmsTxtManagement />
          }
        ]}
      />
    </SpaceBetween>
  )
}
