/**
 * Pricing Preview Component
 * 
 * Shows a preview of pricing rules and calculates example costs
 */

import { useState, useEffect, useCallback } from 'react';
import {
  Container,
  Header,
  Table,
  Box,
  SpaceBetween,
  ColumnLayout,
  Button
} from '@cloudscape-design/components';

import { PricingRules, pricingService } from '../../services/pricingService';

// Category Multiplier Display Component
interface CategoryMultiplierDisplayProps {
  categoryId: string;
  multiplier: string;
  categoryName?: string;
}

function CategoryMultiplierDisplay({ categoryId, multiplier, categoryName }: CategoryMultiplierDisplayProps) {
  const displayName = categoryName || `Category ${categoryId}`;

  return (
    <div>
      <Box variant="awsui-key-label">{displayName}</Box>
      <div>{multiplier}x</div>
    </div>
  );
}

interface PricingPreviewProps {
  pricingRules: PricingRules;
  categoryMappings?: { [key: string]: string };
  readonly?: boolean;
}

interface PricingExample {
  contentType: string;
  basePrice: string;
  category: string;
  categoryMultiplier: string;
  recency: string;
  recencyMultiplier: string;
  finalPrice: string;
}

export default function PricingPreview({ pricingRules, categoryMappings = {}, readonly = false }: PricingPreviewProps) {
  const [examples, setExamples] = useState<PricingExample[]>([]);
  const [loading, setLoading] = useState(false);

  const generateExamples = useCallback(() => {
    const exampleData: PricingExample[] = [];
    
    // Generate examples for each content type
    Object.entries(pricingRules.per_access).forEach(([contentType, basePrice]) => {
      // Example with technology category and recent content
      const techMultiplier = pricingRules.multipliers?.iab_category?.technology || '1.0';
      const recentMultiplier = pricingRules.multipliers?.recency?.days_0_7 || '1.0';
      
      const finalPrice = (
        parseFloat(basePrice) * 
        parseFloat(techMultiplier) * 
        parseFloat(recentMultiplier)
      ).toFixed(3);

      exampleData.push({
        contentType,
        basePrice,
        category: 'Technology',
        categoryMultiplier: techMultiplier,
        recency: '0-7 days',
        recencyMultiplier: recentMultiplier,
        finalPrice
      });
    });

    setExamples(exampleData);
  }, [pricingRules]);

  useEffect(() => {
    generateExamples();
  }, [generateExamples]);

  const testPricingCalculation = async () => {
    try {
      setLoading(true);
      const result = await pricingService.calculatePricing({
        content_type: 'article',
        access_type: 'full'
      });
      console.log('Pricing calculation result:', result);
    } catch (error) {
      console.error('Failed to test pricing calculation:', error);
    } finally {
      setLoading(false);
    }
  };

  const columnDefinitions = [
    {
      id: 'contentType',
      header: 'Content Type',
      cell: (item: PricingExample) => item.contentType,
      sortingField: 'contentType'
    },
    {
      id: 'basePrice',
      header: 'Base Price ($)',
      cell: (item: PricingExample) => item.basePrice,
      sortingField: 'basePrice'
    },
    {
      id: 'category',
      header: 'Category',
      cell: (item: PricingExample) => item.category,
      sortingField: 'category'
    },
    {
      id: 'categoryMultiplier',
      header: 'Category Multiplier',
      cell: (item: PricingExample) => `${item.categoryMultiplier}x`,
      sortingField: 'categoryMultiplier'
    },
    {
      id: 'recency',
      header: 'Content Age',
      cell: (item: PricingExample) => item.recency,
      sortingField: 'recency'
    },
    {
      id: 'recencyMultiplier',
      header: 'Recency Multiplier',
      cell: (item: PricingExample) => `${item.recencyMultiplier}x`,
      sortingField: 'recencyMultiplier'
    },
    {
      id: 'finalPrice',
      header: 'Final Price ($)',
      cell: (item: PricingExample) => (
        <Box fontWeight="bold" color="text-status-success">
          ${item.finalPrice}
        </Box>
      ),
      sortingField: 'finalPrice'
    }
  ];

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h3"
            description="Preview how pricing rules will be applied"
            actions={
              !readonly && (
                <Button
                  loading={loading}
                  onClick={testPricingCalculation}
                >
                  Test Calculation
                </Button>
              )
            }
          >
            Pricing Examples
          </Header>
        }
      >
        <Table
          columnDefinitions={columnDefinitions}
          items={examples}
          loadingText="Generating examples..."
          empty={
            <Box textAlign="center" color="inherit">
              <b>No pricing examples</b>
              <Box variant="p" color="inherit">
                Configure pricing rules to see examples.
              </Box>
            </Box>
          }
        />
      </Container>

      <ColumnLayout columns={3}>
        <Container header={<Header variant="h4">Base Pricing</Header>}>
          <SpaceBetween size="s">
            {Object.entries(pricingRules.per_access).map(([type, price]) => (
              <div key={type}>
                <Box variant="awsui-key-label">{type}</Box>
                <div>${price}</div>
              </div>
            ))}
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h4">Category Multipliers</Header>}>
          <SpaceBetween size="s">
            {Object.entries(pricingRules.multipliers?.iab_category || {}).length > 0 ? (
              Object.entries(pricingRules.multipliers.iab_category).slice(0, 5).map(([categoryId, multiplier]) => (
                <CategoryMultiplierDisplay 
                  key={categoryId} 
                  categoryId={categoryId} 
                  multiplier={multiplier}
                  categoryName={categoryMappings[categoryId]}
                />
              ))
            ) : (
              <Box color="text-body-secondary">No category multipliers configured</Box>
            )}
            {Object.entries(pricingRules.multipliers?.iab_category || {}).length > 5 && (
              <Box color="text-body-secondary" fontSize="body-s">
                ... and {Object.entries(pricingRules.multipliers.iab_category).length - 5} more categories
              </Box>
            )}
          </SpaceBetween>
        </Container>

        <Container header={<Header variant="h4">Recency Multipliers</Header>}>
          <SpaceBetween size="s">
            {Object.entries(pricingRules.multipliers?.recency || {}).map(([period, multiplier]) => {
              // Map recency periods to friendly names
              const periodNames: { [key: string]: string } = {
                'last_24h': 'Last 24 Hours',
                'last_week': 'Last Week',
                'last_month': 'Last Month',
                'older': 'Older than 1 Month'
              };
              
              return (
                <div key={period}>
                  <Box variant="awsui-key-label">{periodNames[period] || period.replace(/_/g, ' ')}</Box>
                  <div>{multiplier}x</div>
                </div>
              );
            })}
          </SpaceBetween>
        </Container>

        {pricingRules.multipliers?.demand && (
          <Container header={<Header variant="h4">Demand Multipliers</Header>}>
            <SpaceBetween size="s">
              {Object.entries(pricingRules.multipliers.demand).map(([level, multiplier]) => {
                // Map demand levels to friendly names
                const levelNames: { [key: string]: string } = {
                  'high': 'High Demand',
                  'medium': 'Medium Demand',
                  'low': 'Low Demand'
                };
                
                return (
                  <div key={level}>
                    <Box variant="awsui-key-label">{levelNames[level] || level}</Box>
                    <div>{multiplier}x</div>
                  </div>
                );
              })}
            </SpaceBetween>
          </Container>
        )}
      </ColumnLayout>
    </SpaceBetween>
  );
}