import React from 'react'
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Cards,
  Icon
} from '@cloudscape-design/components'

import { SOLUTION_NAME } from '../config/solutionName'

// Workflow steps with navigation links - synced with Publisher Tools navigation.
// Verb vocabulary (canonical): Ingest → Enrich → Price → License → Onboard & Manage → Control → Measure.
const workflowSteps = [
  {
    id: 'ingest',
    title: '1. Ingest',
    description: 'Upload your content through the publisher dashboard. Content is automatically processed through the IAB framework pipeline.',
    iconName: 'upload',
    linkText: 'Ingest',
    linkHref: '/content/add'
  },
  {
    id: 'enrich',
    title: '2. Enrich',
    description: 'Track your content as it moves through validation, IAB taxonomy classification, and vector embedding generation.',
    iconName: 'status-info',
    linkText: 'Enrich',
    linkHref: '/system-status'
  },
  {
    id: 'price',
    title: '3. Price',
    description: 'Configure revenue models, pricing tiers, and monetization strategies for your content access.',
    iconName: 'settings',
    linkText: 'Price',
    linkHref: '/pricing'
  },
  {
    id: 'license',
    title: '4. License',
    description: 'Define license terms and entitlements governing how AI systems may use your content. (Coming soon — tracked in #3.)',
    iconName: 'file',
    linkText: 'License',
    linkHref: '/licenses'
  },
  {
    id: 'onboard',
    title: '5. Onboard & Manage',
    description: 'Create partners / licensees on a pricing tier, hand them their API key (shown once), then rotate or retire keys and suspend, reactivate or delete partners as the relationship changes.',
    iconName: 'user-profile',
    linkText: 'Onboard & Manage',
    linkHref: '/partners'
  },
  {
    id: 'control',
    title: '6. Control',
    description: 'Configure bot access policies and control which AI agents can access your content through WAF integration.',
    iconName: 'security',
    linkText: 'Control',
    linkHref: '/bot-control'
  },
  {
    id: 'test',
    title: '7. Test',
    description: 'Simulate how AI agents discover and access your content, and validate that your access controls behave as intended before going live.',
    iconName: 'search',
    linkText: 'Test',
    linkHref: '/test'
  },
  {
    id: 'measure',
    title: '8. Measure',
    description: 'Monitor usage, revenue, API requests, and content performance with advanced analytics.',
    iconName: 'view-full',
    linkText: 'Measure',
    linkHref: '/dashboard'
  }
]



export default function HomePage() {
  return (
    <Container>
      <SpaceBetween size="l">
        {/* Welcome Header */}
        <Header
          variant="h1"
          description="A comprehensive framework for publishers to monetize content through AI agent discovery while maintaining control and compliance."
        >
          {SOLUTION_NAME}
        </Header>



        {/* Workflow Steps */}
        <Container>
          <Header variant="h2">Getting Started - 8 Step Workflow</Header>
          <Cards
            cardDefinition={{
              header: item => item.title,
              sections: [
                {
                  id: 'description',
                  content: item => (
                    <SpaceBetween size="m">
                      <Box>
                        <Icon name={item.iconName} size="medium" />
                      </Box>
                      <Box variant="p">{item.description}</Box>
                      <Button 
                        href={item.linkHref}
                        variant={item.id === 'upload' ? 'primary' : 'normal'}
                        iconName="external"
                      >
                        {item.linkText}
                      </Button>
                    </SpaceBetween>
                  )
                }
              ]
            }}
            cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }, { minWidth: 800, cards: 3 }]}
            items={workflowSteps}
            loadingText="Loading workflow steps"
            empty={
              <Box textAlign="center" color="inherit">
                <b>No workflow steps available</b>
              </Box>
            }
          />
        </Container>


      </SpaceBetween>
    </Container>
  )
}