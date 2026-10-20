import React, { useState } from 'react'
import {
  Container,
  Header,
  SpaceBetween,
  Grid,
  Box,
  Button,
  Input,
  Select,
  Table,
  Badge,
  Alert,
  FormField,
  CodeEditor,
  ExpandableSection,
  StatusIndicator
} from '@cloudscape-design/components'

// Mock content discovery results
const mockSearchResults = [
  {
    id: 'content-1',
    title: 'AI in Healthcare: Future Trends',
    category: 'Technology',
    type: 'Article',
    publisher: 'TechMed Publications',
    price: '$0.05',
    relevanceScore: 0.95,
    summary: 'Comprehensive analysis of AI applications in modern healthcare systems.',
    wordCount: 2500,
    language: 'English'
  },
  {
    id: 'content-2',
    title: 'Machine Learning in Medical Diagnosis',
    category: 'Technology',
    type: 'Research Paper',
    publisher: 'Medical AI Research',
    price: '$0.12',
    relevanceScore: 0.89,
    summary: 'Research findings on ML applications for medical diagnosis.',
    wordCount: 4200,
    language: 'English'
  },
  {
    id: 'content-3',
    title: 'Healthcare Data Privacy Regulations',
    category: 'Law & Government',
    type: 'White Paper',
    publisher: 'Legal Tech Insights',
    price: '$0.08',
    relevanceScore: 0.76,
    summary: 'Analysis of privacy regulations affecting healthcare data.',
    wordCount: 3100,
    language: 'English'
  }
]

const searchMethods = [
  { label: 'Semantic Search', value: 'semantic' },
  { label: 'Keyword Search', value: 'keyword' },
  { label: 'Category Browse', value: 'category' },
  { label: 'llms.txt Discovery', value: 'llmstxt' }
]

export default function TestConsole() {
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMethod, setSearchMethod] = useState(searchMethods[0])
  const [selectedContent, setSelectedContent] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [searchResults, setSearchResults] = useState([])
  const [showApiCall, setShowApiCall] = useState(false)

  const handleSearch = async () => {
    setIsSearching(true)
    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 1500))
    setSearchResults(mockSearchResults)
    setIsSearching(false)
    setShowApiCall(true)
  }

  const generateApiCall = () => {
    // The canonical partner discovery call: POST /query with the partner's API key.
    const apiCall = {
      method: 'POST',
      url: '/v1/query',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': 'YOUR_API_KEY',
        'User-Agent': 'AI-Training-Bot/1.0'
      },
      body: {
        query: searchQuery,
        content_type: ['article'],
        max_results: 10
      }
    }
    return JSON.stringify(apiCall, null, 2)
  }

  const columnDefinitions = [
    {
      id: 'title',
      header: 'Content',
      cell: item => (
        <div>
          <Box variant="strong">{item.title}</Box>
          <Box variant="small" color="text-body-secondary">{item.summary}</Box>
          <Box variant="small">
            <Badge color="blue">{item.type}</Badge>
            {' '}
            <Badge>{item.wordCount} words</Badge>
          </Box>
        </div>
      ),
      isRowHeader: true
    },
    {
      id: 'publisher',
      header: 'Publisher',
      cell: item => item.publisher
    },
    {
      id: 'relevance',
      header: 'Relevance',
      cell: item => (
        <Box>
          <StatusIndicator type={item.relevanceScore > 0.8 ? 'success' : 'info'}>
            {(item.relevanceScore * 100).toFixed(0)}%
          </StatusIndicator>
        </Box>
      )
    },
    {
      id: 'price',
      header: 'Price',
      cell: item => <Box variant="strong" color="text-status-success">{item.price}</Box>
    }
  ]

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Simulate AI agent content discovery and access workflows"
      >
        AI Agent Simulator
      </Header>

      <Alert type="info" header="AI Agent Simulation">
        This interface simulates how AI training systems would discover and access content 
        through the AI Content Monetization API framework. Use different search methods to explore 
        content discovery capabilities.
      </Alert>

      {/* Search Interface */}
      <Container
        header={
          <Header variant="h2" description="Simulate content discovery requests">
            Content Discovery
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Grid gridDefinition={[{ colspan: 6 }, { colspan: 3 }, { colspan: 3 }]}>
            <FormField label="Search Query">
              <Input
                value={searchQuery}
                onChange={({ detail }) => setSearchQuery(detail.value)}
                placeholder="Enter search terms (e.g., 'AI healthcare applications')"
              />
            </FormField>
            <FormField label="Search Method">
              <Select
                selectedOption={searchMethod}
                onChange={({ detail }) => setSearchMethod(detail.selectedOption)}
                options={searchMethods}
              />
            </FormField>
            <FormField label="Action">
              <Button 
                variant="primary" 
                onClick={handleSearch}
                disabled={!searchQuery || isSearching}
                loading={isSearching}
              >
                {isSearching ? 'Searching...' : 'Search Content'}
              </Button>
            </FormField>
          </Grid>
        </SpaceBetween>
      </Container>

      {/* API Call Preview */}
      {showApiCall && (
        <ExpandableSection headerText="Generated API Call" defaultExpanded={false}>
          <Container>
            <SpaceBetween size="m">
              <Alert type="info">
                This shows the actual API call that would be made by an AI training system.
              </Alert>
              <CodeEditor
                ace={undefined}
                language="json"
                value={generateApiCall()}
                readOnly
                preferences={{
                  fontSize: 14,
                  tabSize: 2
                }}
              />
            </SpaceBetween>
          </Container>
        </ExpandableSection>
      )}

      {/* Search Results */}
      {searchResults.length > 0 && (
        <Table
          columnDefinitions={columnDefinitions}
          items={searchResults}
          loadingText="Searching content"
          selectionType="multi"
          selectedItems={selectedContent}
          onSelectionChange={({ detail }) => setSelectedContent(detail.selectedItems)}
          ariaLabels={{
            selectionGroupLabel: "Content selection",
            allItemsSelectionLabel: ({ selectedItems }) =>
              `${selectedItems.length} ${
                selectedItems.length === 1 ? "item" : "items"
              } selected`,
            itemSelectionLabel: ({ selectedItems }, item) => {
              const isItemSelected = selectedItems.filter(
                i => i.id === item.id
              ).length;
              return `${item.title} is ${
                isItemSelected ? "" : "not "
              }selected`;
            }
          }}
          variant="full-page"
          stickyHeader
          header={
            <Header
              counter={`(${searchResults.length})`}
              description="Content discovery results"
              actions={
                <Button 
                  variant="primary"
                  disabled={selectedContent.length === 0}
                >
                  Request Access ({selectedContent.length})
                </Button>
              }
            >
              Search Results
            </Header>
          }
          empty={
            <Box textAlign="center" color="inherit">
              <b>No content found</b>
              <Box variant="p" color="inherit">
                Try adjusting your search query or method.
              </Box>
            </Box>
          }
        />
      )}

      {/* Simulation Controls */}
      <Container
        header={
          <Header variant="h2" description="Simulate different AI agent behaviors">
            Agent Behavior Simulation
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
          <SpaceBetween size="s">
            <Box variant="h3">Legitimate AI Bot</Box>
            <Box variant="p">Simulates proper bot behavior with correct headers and rate limiting.</Box>
            <Button>Simulate GPT Bot</Button>
          </SpaceBetween>
          <SpaceBetween size="s">
            <Box variant="h3">Blocked AI Bot</Box>
            <Box variant="p">Simulates blocked bot behavior to test WAF rules.</Box>
            <Button>Simulate Blocked Bot</Button>
          </SpaceBetween>
          <SpaceBetween size="s">
            <Box variant="h3">Search Engine Bot</Box>
            <Box variant="p">Simulates legitimate search engine crawling.</Box>
            <Button>Simulate Googlebot</Button>
          </SpaceBetween>
        </Grid>
      </Container>
    </SpaceBetween>
  )
}