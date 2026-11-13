/**
 * Unified Publisher Dashboard
 * 
 * Combines content-level information and bot control analytics into a single comprehensive view
 */

import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  ColumnLayout,
  StatusIndicator,
  Button,
  Cards,
  Badge,
  Table,
  TextFilter,
  Select,
  DateRangePicker,
  Alert,
  Tabs
} from '@cloudscape-design/components'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import * as d3 from 'd3'
import { sankey, sankeyLinkHorizontal } from 'd3-sankey'

// Import ObservabilityLink component
import { ObservabilityLinks, ObservabilityStatus } from '../components/ObservabilityLink'

// Import services
import { 
  analyticsService, 
  UsageMetrics, 
  UsageData, 
  RevenueByCategory, 
  TopPartner 
} from '../services/analyticsService'
import { contentService, ContentItem } from '../services/contentService'
import { useFeatureFlags } from '../contexts/FeatureFlagContext'

// Mock data removed - now using real data from analytics service


// Enhanced Sankey Chart Component with metrics display and data availability indicators
const SankeyChart = ({ data, width = 800, height = 400, title = "Data Flow", showMetrics = true, dataAvailable = true, onRefresh }) => {
  const svgRef = useRef(null)

  useEffect(() => {
    if (!data || !data.nodes || !data.links || !svgRef.current) return

    // Validate data structure
    if (!Array.isArray(data.nodes) || !Array.isArray(data.links)) {
      console.warn('Invalid Sankey data structure:', data)
      return
    }

    // Validate that all nodes have required properties
    const validNodes = data.nodes.every(node => 
      typeof node.name === 'string' && 
      typeof node.value === 'number' && 
      !isNaN(node.value) && 
      node.value >= 0
    )

    // Validate that all links have required properties
    const validLinks = data.links.every(link => 
      typeof link.source === 'number' && 
      typeof link.target === 'number' && 
      typeof link.value === 'number' && 
      !isNaN(link.value) && 
      link.value >= 0 &&
      link.source >= 0 && 
      link.target >= 0 &&
      link.source < data.nodes.length && 
      link.target < data.nodes.length
    )

    if (!validNodes || !validLinks) {
      console.warn('Invalid Sankey data values:', { validNodes, validLinks, data })
      return
    }

    const svg = d3.select(svgRef.current)
    svg.selectAll("*").remove()

    const margin = { top: 40, right: 150, bottom: 40, left: 150 }
    const innerWidth = width - margin.left - margin.right
    const innerHeight = height - margin.top - margin.bottom

    const g = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`)

    try {
      const sankeyGenerator = sankey()
        .nodeWidth(20)
        .nodePadding(15)
        .extent([[1, 1], [innerWidth - 1, innerHeight - 6]])

      const sankeyData = sankeyGenerator(data)
      const { nodes, links } = sankeyData

      if (!nodes || !links) {
        console.warn('Sankey generator returned invalid data')
        return
      }

    // Add gradient definitions for links
    const defs = svg.append("defs")

    links.forEach((link, i) => {
      const gradient = defs.append("linearGradient")
        .attr("id", `gradient-${i}`)
        .attr("gradientUnits", "userSpaceOnUse")
        .attr("x1", link.source.x1)
        .attr("x2", link.target.x0)

      gradient.append("stop")
        .attr("offset", "0%")
        .attr("stop-color", link.color || link.source.color || "#0073bb")
        .attr("stop-opacity", 0.8)

      gradient.append("stop")
        .attr("offset", "100%")
        .attr("stop-color", link.color || link.target.color || "#0073bb")
        .attr("stop-opacity", 0.6)
    })

    // Add links with gradients
    g.append("g")
      .selectAll("path")
      .data(links)
      .join("path")
      .attr("d", sankeyLinkHorizontal())
      .attr("stroke", (d, i) => `url(#gradient-${i})`)
      .attr("stroke-width", d => Math.max(2, d.width))
      .attr("fill", "none")
      .attr("opacity", 0.8)
      .on("mouseover", function (event, d) {
        d3.select(this).attr("opacity", 1).attr("stroke-width", d => Math.max(3, d.width + 1))

        // Create enhanced tooltip
        const tooltip = d3.select("body").append("div")
          .attr("class", "sankey-tooltip")
          .style("position", "absolute")
          .style("background", "rgba(0, 0, 0, 0.9)")
          .style("color", "white")
          .style("padding", "12px")
          .style("border-radius", "6px")
          .style("font-size", "13px")
          .style("pointer-events", "none")
          .style("z-index", 1000)
          .style("box-shadow", "0 4px 12px rgba(0,0,0,0.3)")

        const percentage = ((d.value / d.source.value) * 100).toFixed(1)

        tooltip.html(`
          <div style="font-weight: bold; margin-bottom: 6px;">${d.source.name} → ${d.target.name}</div>
          <div>Requests: ${d.value.toLocaleString()}</div>
          <div>Percentage: ${percentage}%</div>
          ${d.revenue ? `<div>Revenue: $${d.revenue.toLocaleString()}</div>` : ''}
        `)
          .style("left", (event.pageX + 15) + "px")
          .style("top", (event.pageY - 10) + "px")
      })
      .on("mouseout", function () {
        d3.select(this).attr("opacity", 0.8).attr("stroke-width", d => Math.max(2, d.width))
        d3.selectAll(".sankey-tooltip").remove()
      })

    // Add nodes with rounded corners
    g.append("g")
      .selectAll("rect")
      .data(nodes)
      .join("rect")
      .attr("x", d => d.x0)
      .attr("y", d => d.y0)
      .attr("height", d => d.y1 - d.y0)
      .attr("width", d => d.x1 - d.x0)
      .attr("fill", d => d.color || "#69b3a2")
      .attr("stroke", "#fff")
      .attr("stroke-width", 2)
      .attr("rx", 4)
      .attr("ry", 4)

    // Add node labels with metrics
    g.append("g")
      .selectAll("text")
      .data(nodes)
      .join("text")
      .attr("x", d => {
        if (d.x0 < innerWidth / 3) return d.x0 - 10  // Left side
        if (d.x0 > (2 * innerWidth) / 3) return d.x1 + 10  // Right side
        return d.x0 + (d.x1 - d.x0) / 2  // Center
      })
      .attr("y", d => (d.y1 + d.y0) / 2 - 10)
      .attr("dy", "0.35em")
      .attr("text-anchor", d => {
        if (d.x0 < innerWidth / 3) return "end"  // Left side
        if (d.x0 > (2 * innerWidth) / 3) return "start"  // Right side
        return "middle"  // Center
      })
      .style("font-size", "13px")
      .style("font-weight", "600")
      .style("fill", d => {
        // Use white text for middle nodes (gray background), dark text for others
        if (d.color === "#6c757d") return "#ffffff"
        return "#333"
      })
      .text(d => d.name)

    // Add metrics below node names
    if (showMetrics) {
      g.append("g")
        .selectAll("text")
        .data(nodes.filter(d => d.value))
        .join("text")
        .attr("x", d => {
          if (d.x0 < innerWidth / 3) return d.x0 - 10  // Left side
          if (d.x0 > (2 * innerWidth) / 3) return d.x1 + 10  // Right side
          return d.x0 + (d.x1 - d.x0) / 2  // Center
        })
        .attr("y", d => (d.y1 + d.y0) / 2 + 8)
        .attr("dy", "0.35em")
        .attr("text-anchor", d => {
          if (d.x0 < innerWidth / 3) return "end"  // Left side
          if (d.x0 > (2 * innerWidth) / 3) return "start"  // Right side
          return "middle"  // Center
        })
        .style("font-size", "11px")
        .style("font-weight", "500")
        .style("fill", d => {
          // Use white text for middle nodes (gray background), darker text for others
          if (d.color === "#6c757d") return "#ffffff"
          return "#666"
        })
        .text(d => {
          if (d.revenue) return `$${d.revenue.toLocaleString()}`
          return d.value ? d.value.toLocaleString() : ''
        })

      // Add percentage labels for revenue nodes
      g.append("g")
        .selectAll("text")
        .data(nodes.filter(d => d.revenue && d.x0 > (2 * innerWidth) / 3))
        .join("text")
        .attr("x", d => d.x1 + 10)
        .attr("y", d => (d.y1 + d.y0) / 2 + 22)
        .attr("dy", "0.35em")
        .attr("text-anchor", "start")
        .style("font-size", "10px")
        .style("font-weight", "400")
        .style("fill", "#888")
        .text(d => {
          const totalRevenue = nodes.reduce((sum, node) => sum + (node.revenue || 0), 0)
          const percentage = ((d.revenue / totalRevenue) * 100).toFixed(1)
          return `${percentage}%`
        })
    }

    } catch (error) {
      console.error('Error rendering Sankey chart:', error)
      // Clear the SVG on error
      svg.selectAll("*").remove()
      
      // Add error message
      svg.append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .style("font-size", "14px")
        .style("fill", "#666")
        .text("Error loading chart data")
    }

  }, [data, width, height, showMetrics])

  return (
    <div style={{ background: '#fafafa', padding: '20px', borderRadius: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h3 style={{
          fontSize: '16px',
          fontWeight: '600',
          color: '#333',
          margin: 0
        }}>
          {title}
        </h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {!dataAvailable && (
            <Badge color="grey">Mock Data (No Recent Traffic)</Badge>
          )}
          {dataAvailable && (
            <Badge color="green">Real-time Data</Badge>
          )}
          {onRefresh && (
            <Button 
              variant="icon" 
              iconName="refresh" 
              onClick={onRefresh}
              ariaLabel="Refresh data"
            />
          )}
        </div>
      </div>
      {!data ? (
        <div style={{ 
          width: width, 
          height: height, 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          background: '#f8f9fa',
          border: '1px dashed #dee2e6',
          borderRadius: '4px'
        }}>
          <div style={{ textAlign: 'center', color: '#6c757d' }}>
            <div style={{ fontSize: '14px', marginBottom: '8px' }}>Loading chart data...</div>
            <div style={{ fontSize: '12px' }}>Please wait while we fetch the latest analytics</div>
          </div>
        </div>
      ) : (
        <svg ref={svgRef} width={width} height={height}></svg>
      )}
    </div>
  )
}

// Sankey data for bot activity flow - Bot Types → Total Traffic → Outcomes

// Sankey data for content performance flow - Categories → Total Requests → Revenue Streams

// Sankey data for partner analytics flow - Partners → Total Traffic → Revenue Streams

export default function UnifiedDashboard() {
  const { demoDataEnabled } = useFeatureFlags()
  const [selectedTimeRange, setSelectedTimeRange] = useState({ type: 'relative', amount: 7, unit: 'day' })
  const [contentFilter, setContentFilter] = useState('')
  const [activeTab, setActiveTab] = useState('revenue')
  const [timePeriod, setTimePeriod] = useState('last_7_days')
  
  // State for real data
  const [usageMetrics, setUsageMetrics] = useState<UsageMetrics | null>(null)
  const [usageData, setUsageData] = useState<UsageData[]>([])
  const [, setRevenueByCategory] = useState<RevenueByCategory[]>([])
  const [topPartners, setTopPartners] = useState<TopPartner[]>([])
  const [contentItems, setContentItems] = useState<ContentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [, setError] = useState<string | null>(null)

  // State for Sankey diagram data - Initialize with fallback data
  const [botActivitySankeyData, setBotActivitySankeyData] = useState<SankeyData | null>(null)
  const [contentPerformanceSankeyData, setContentPerformanceSankeyData] = useState<SankeyData | null>(null)
  const [partnerAnalyticsSankeyData, setPartnerAnalyticsSankeyData] = useState<SankeyData | null>(null)
  const [sankeyDataAvailable, setSankeyDataAvailable] = useState({
    botActivity: false,
    contentPerformance: false,
    partnerAnalytics: false
  })

  const loadDashboardData = useCallback(async () => {
    console.log('📊 loadDashboardData called at:', new Date().toISOString())
    try {
      setLoading(true)
      setError(null)
      
      // Convert time range to API format
      const timeRangeParam = `last_${selectedTimeRange.amount}_${selectedTimeRange.unit}${selectedTimeRange.amount > 1 ? 's' : ''}`
      
      const [metrics, usage, revenue, partners, content] = await Promise.all([
        analyticsService.getUsageMetrics(demoDataEnabled),
        analyticsService.getUsageData(timeRangeParam),
        analyticsService.getRevenueByCategory(),
        analyticsService.getTopPartners(),
        contentService.getContent(demoDataEnabled)
      ])
      
      setUsageMetrics(metrics)
      setUsageData(usage)
      setRevenueByCategory(revenue)
      setTopPartners(partners)
      setContentItems(content)

      // Load Sankey diagram data using existing APIs
      await loadSankeyData(timeRangeParam)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard data')
      console.error('Error loading dashboard data:', err)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadSankeyData is a stable inline loader defined below; omitted to avoid re-wrapping the loader chain
  }, [selectedTimeRange, demoDataEnabled])

  // Load data on component mount and when time range changes
  useEffect(() => {
    console.log('🔄 Loading dashboard data due to time range change:', selectedTimeRange)
    loadDashboardData()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedTimeRange is already captured via loadDashboardData's own deps
  }, [loadDashboardData])

  // Auto-refresh Sankey data every 5 minutes
  useEffect(() => {
    const interval = setInterval(() => {
      const timeRangeParam = `last_${selectedTimeRange.amount}_${selectedTimeRange.unit}${selectedTimeRange.amount > 1 ? 's' : ''}`
      loadSankeyData(timeRangeParam)
    }, 5 * 60 * 1000) // 5 minutes

    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-arm the 5-min timer only on time-range change; loadSankeyData is a stable inline loader
  }, [selectedTimeRange])

  const loadSankeyData = async (timeRange: string) => {
    try {
      // Load Sankey data in parallel
      const [botActivityFlow, contentPerformanceFlow, partnerAnalyticsFlow] = await Promise.all([
        analyticsService.getBotActivityFlowData(timeRange, demoDataEnabled).catch(err => {
          console.warn('Bot activity flow data failed, using fallback:', err)
          return null
        }),
        analyticsService.getContentPerformanceFlowData(timeRange).catch(err => {
          console.warn('Content performance flow data failed, using fallback:', err)
          return null
        }),
        analyticsService.getPartnerAnalyticsFlowData(timeRange).catch(err => {
          console.warn('Partner analytics flow data failed, using fallback:', err)
          return null
        })
      ])

      // Convert flow data to Sankey format or use fallback
      if (botActivityFlow) {
        const sankeyData = analyticsService.convertToSankeyData(botActivityFlow, 'bot')
        setBotActivitySankeyData(sankeyData)
        setSankeyDataAvailable(prev => ({ ...prev, botActivity: true }))
      } else {
        // Use original hardcoded mock data as fallback
        setBotActivitySankeyData({
          nodes: [
            { name: "Legitimate AI Bots", color: "#037f0c", value: 4500, revenue: 1250.50 },
            { name: "Search Engine Crawlers", color: "#0073bb", value: 1500, revenue: 180.25 },
            { name: "Blocked Scrapers", color: "#d13212", value: 3500, revenue: 0 },
            { name: "Unknown Bots", color: "#ff9900", value: 500, revenue: 0 },
            { name: "Total Bot Traffic", color: "#6c757d", value: 10000, revenue: 1430.75 },
            { name: "Revenue Generated", color: "#28a745", value: 6000, revenue: 1430.75 },
            { name: "Security Blocked", color: "#dc3545", value: 4000, revenue: 0 }
          ],
          links: [
            { source: 0, target: 4, value: 4500, revenue: 1250.50, color: "#037f0c" },
            { source: 1, target: 4, value: 1500, revenue: 180.25, color: "#0073bb" },
            { source: 2, target: 4, value: 3500, revenue: 0, color: "#d13212" },
            { source: 3, target: 4, value: 500, revenue: 0, color: "#ff9900" },
            { source: 4, target: 5, value: 6000, revenue: 1430.75, color: "#28a745" },
            { source: 4, target: 6, value: 4000, revenue: 0, color: "#dc3545" }
          ]
        })
        setSankeyDataAvailable(prev => ({ ...prev, botActivity: false }))
      }

      if (contentPerformanceFlow) {
        const sankeyData = analyticsService.convertToSankeyData(contentPerformanceFlow, 'content')
        setContentPerformanceSankeyData(sankeyData)
        setSankeyDataAvailable(prev => ({ ...prev, contentPerformance: true }))
      } else {
        // Use original hardcoded mock data as fallback
        setContentPerformanceSankeyData({
          nodes: [
            { name: "Technology & Computing", color: "#0073bb", value: 3420, revenue: 890.50 },
            { name: "Medical Health", color: "#037f0c", value: 2890, revenue: 675.25 },
            { name: "Business & Finance", color: "#ff9900", value: 2100, revenue: 520.75 },
            { name: "Science", color: "#8b5cf6", value: 1850, revenue: 445.30 },
            { name: "Education", color: "#06b6d4", value: 1160, revenue: 308.70 },
            { name: "Total API Requests", color: "#6c757d", value: 11420, revenue: 2840.50 },
            { name: "Premium Content Access", color: "#28a745", value: 6852, revenue: 1704.30 },
            { name: "Standard Content Access", color: "#17a2b8", value: 3426, revenue: 852.15 },
            { name: "Research & Analytics", color: "#ffc107", value: 1142, revenue: 284.05 }
          ],
          links: [
            { source: 0, target: 5, value: 3420, revenue: 890.50, color: "#0073bb" },
            { source: 1, target: 5, value: 2890, revenue: 675.25, color: "#037f0c" },
            { source: 2, target: 5, value: 2100, revenue: 520.75, color: "#ff9900" },
            { source: 3, target: 5, value: 1850, revenue: 445.30, color: "#8b5cf6" },
            { source: 4, target: 5, value: 1160, revenue: 308.70, color: "#06b6d4" },
            { source: 5, target: 6, value: 6852, revenue: 1704.30, color: "#28a745" },
            { source: 5, target: 7, value: 3426, revenue: 852.15, color: "#17a2b8" },
            { source: 5, target: 8, value: 1142, revenue: 284.05, color: "#ffc107" }
          ]
        })
        setSankeyDataAvailable(prev => ({ ...prev, contentPerformance: false }))
      }

      if (partnerAnalyticsFlow) {
        const sankeyData = analyticsService.convertToSankeyData(partnerAnalyticsFlow, 'partner')
        setPartnerAnalyticsSankeyData(sankeyData)
        setSankeyDataAvailable(prev => ({ ...prev, partnerAnalytics: true }))
      } else {
        // Use original hardcoded mock data as fallback
        setPartnerAnalyticsSankeyData({
          nodes: [
            { name: "OpenAI", color: "#00a86b", value: 15420, revenue: 771.00 },
            { name: "Anthropic", color: "#ff6b35", value: 12100, revenue: 605.00 },
            { name: "Google DeepMind", color: "#4285f4", value: 9800, revenue: 490.00 },
            { name: "Microsoft", color: "#00bcf2", value: 8500, revenue: 425.00 },
            { name: "Meta AI", color: "#1877f2", value: 6200, revenue: 310.00 },
            { name: "Total API Traffic", color: "#6c757d", value: 52020, revenue: 2601.00 },
            { name: "Premium API Access", color: "#28a745", value: 31212, revenue: 1561.00 },
            { name: "Standard API Access", color: "#17a2b8", value: 15606, revenue: 780.30 },
            { name: "Analytics & Insights", color: "#ffc107", value: 5202, revenue: 260.10 }
          ],
          links: [
            { source: 0, target: 5, value: 15420, revenue: 771.00, color: "#00a86b" },
            { source: 1, target: 5, value: 12100, revenue: 605.00, color: "#ff6b35" },
            { source: 2, target: 5, value: 9800, revenue: 490.00, color: "#4285f4" },
            { source: 3, target: 5, value: 8500, revenue: 425.00, color: "#00bcf2" },
            { source: 4, target: 5, value: 6200, revenue: 310.00, color: "#1877f2" },
            { source: 5, target: 6, value: 31212, revenue: 1561.00, color: "#28a745" },
            { source: 5, target: 7, value: 15606, revenue: 780.30, color: "#17a2b8" },
            { source: 5, target: 8, value: 5202, revenue: 260.10, color: "#ffc107" }
          ]
        })
        setSankeyDataAvailable(prev => ({ ...prev, partnerAnalytics: false }))
      }
    } catch (err) {
      console.error('Error loading Sankey data:', err)
      // Set fallback data for all charts - use the same mock data as above
      setBotActivitySankeyData({
        nodes: [
          { name: "Legitimate AI Bots", color: "#037f0c", value: 4500, revenue: 1250.50 },
          { name: "Search Engine Crawlers", color: "#0073bb", value: 1500, revenue: 180.25 },
          { name: "Blocked Scrapers", color: "#d13212", value: 3500, revenue: 0 },
          { name: "Unknown Bots", color: "#ff9900", value: 500, revenue: 0 },
          { name: "Total Bot Traffic", color: "#6c757d", value: 10000, revenue: 1430.75 },
          { name: "Revenue Generated", color: "#28a745", value: 6000, revenue: 1430.75 },
          { name: "Security Blocked", color: "#dc3545", value: 4000, revenue: 0 }
        ],
        links: [
          { source: 0, target: 4, value: 4500, revenue: 1250.50, color: "#037f0c" },
          { source: 1, target: 4, value: 1500, revenue: 180.25, color: "#0073bb" },
          { source: 2, target: 4, value: 3500, revenue: 0, color: "#d13212" },
          { source: 3, target: 4, value: 500, revenue: 0, color: "#ff9900" },
          { source: 4, target: 5, value: 6000, revenue: 1430.75, color: "#28a745" },
          { source: 4, target: 6, value: 4000, revenue: 0, color: "#dc3545" }
        ]
      })
      setContentPerformanceSankeyData({
        nodes: [
          { name: "Technology & Computing", color: "#0073bb", value: 3420, revenue: 890.50 },
          { name: "Medical Health", color: "#037f0c", value: 2890, revenue: 675.25 },
          { name: "Business & Finance", color: "#ff9900", value: 2100, revenue: 520.75 },
          { name: "Science", color: "#8b5cf6", value: 1850, revenue: 445.30 },
          { name: "Education", color: "#06b6d4", value: 1160, revenue: 308.70 },
          { name: "Total API Requests", color: "#6c757d", value: 11420, revenue: 2840.50 },
          { name: "Premium Content Access", color: "#28a745", value: 6852, revenue: 1704.30 },
          { name: "Standard Content Access", color: "#17a2b8", value: 3426, revenue: 852.15 },
          { name: "Research & Analytics", color: "#ffc107", value: 1142, revenue: 284.05 }
        ],
        links: [
          { source: 0, target: 5, value: 3420, revenue: 890.50, color: "#0073bb" },
          { source: 1, target: 5, value: 2890, revenue: 675.25, color: "#037f0c" },
          { source: 2, target: 5, value: 2100, revenue: 520.75, color: "#ff9900" },
          { source: 3, target: 5, value: 1850, revenue: 445.30, color: "#8b5cf6" },
          { source: 4, target: 5, value: 1160, revenue: 308.70, color: "#06b6d4" },
          { source: 5, target: 6, value: 6852, revenue: 1704.30, color: "#28a745" },
          { source: 5, target: 7, value: 3426, revenue: 852.15, color: "#17a2b8" },
          { source: 5, target: 8, value: 1142, revenue: 284.05, color: "#ffc107" }
        ]
      })
      setPartnerAnalyticsSankeyData({
        nodes: [
          { name: "OpenAI", color: "#00a86b", value: 15420, revenue: 771.00 },
          { name: "Anthropic", color: "#ff6b35", value: 12100, revenue: 605.00 },
          { name: "Google DeepMind", color: "#4285f4", value: 9800, revenue: 490.00 },
          { name: "Microsoft", color: "#00bcf2", value: 8500, revenue: 425.00 },
          { name: "Meta AI", color: "#1877f2", value: 6200, revenue: 310.00 },
          { name: "Total API Traffic", color: "#6c757d", value: 52020, revenue: 2601.00 },
          { name: "Premium API Access", color: "#28a745", value: 31212, revenue: 1561.00 },
          { name: "Standard API Access", color: "#17a2b8", value: 15606, revenue: 780.30 },
          { name: "Analytics & Insights", color: "#ffc107", value: 5202, revenue: 260.10 }
        ],
        links: [
          { source: 0, target: 5, value: 15420, revenue: 771.00, color: "#00a86b" },
          { source: 1, target: 5, value: 12100, revenue: 605.00, color: "#ff6b35" },
          { source: 2, target: 5, value: 9800, revenue: 490.00, color: "#4285f4" },
          { source: 3, target: 5, value: 8500, revenue: 425.00, color: "#00bcf2" },
          { source: 4, target: 5, value: 6200, revenue: 310.00, color: "#1877f2" },
          { source: 5, target: 6, value: 31212, revenue: 1561.00, color: "#28a745" },
          { source: 5, target: 7, value: 15606, revenue: 780.30, color: "#17a2b8" },
          { source: 5, target: 8, value: 5202, revenue: 260.10, color: "#ffc107" }
        ]
      })
      setSankeyDataAvailable({
        botActivity: false,
        contentPerformance: false,
        partnerAnalytics: false
      })
    }
  }
  
  // Get AgentCore observability configuration from environment
  const analyticsAgentArn = import.meta.env.VITE_ANALYTICS_AGENT_ARN
  const mcpServerArn = import.meta.env.VITE_MCP_SERVER_ARN

  // Overview metrics cards
  const overviewCards = [
    {
      title: 'Content Accessed',
      value: usageMetrics?.content_accessed?.toLocaleString() || '0',
      description: 'Content items accessed',
      type: 'success',
      icon: 'file'
    },
    {
      title: 'Revenue Generated',
      value: `$${usageMetrics?.revenue_generated?.toFixed(2) || '0.00'}`,
      description: 'From API access',
      type: 'success',
      icon: 'trending-up'
    },
    {
      title: 'Total Requests',
      value: usageMetrics?.total_requests?.toLocaleString() || '0',
      description: 'API requests',
      type: 'info',
      icon: 'call'
    },
    {
      title: 'Unique Partners',
      value: usageMetrics?.unique_partners?.toString() || '0',
      description: 'AI operators',
      type: 'info',
      icon: 'user-profile'
    },
    {
      title: 'Bot Access',
      value: usageMetrics?.total_bot_access?.toLocaleString() || '0',
      description: 'Bot interactions',
      type: 'warning',
      icon: 'security'
    },
    {
      title: 'MCP Tool Usage',
      value: usageMetrics?.total_mcp_usage?.toLocaleString() || '0',
      description: 'MCP tool calls',
      type: 'success',
      icon: 'status-positive'
    }
  ]

  const tabs = [
    {
      id: 'revenue',
      label: 'Revenue Analytics',
      content: (
        <SpaceBetween size="l">
          {/* Time Period Controls */}
          <Container>
            <Header 
              variant="h2"
              actions={
                <Select
                  selectedOption={{ 
                    label: timePeriod.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                    value: timePeriod 
                  }}
                  onChange={({ detail }) => setTimePeriod(detail.selectedOption.value)}
                  options={[
                    { label: 'Last 24 Hours', value: 'last_24_hours' },
                    { label: 'Last 7 Days', value: 'last_7_days' },
                    { label: 'Last 30 Days', value: 'last_30_days' },
                    { label: 'Last 90 Days', value: 'last_90_days' },
                  ]}
                />
              }
            >
              Revenue Analytics
            </Header>
          </Container>

          {/* Key Metrics */}
          <Cards
            cardDefinition={{
              header: item => item.title,
              sections: [
                {
                  id: 'value',
                  content: item => (
                    <Box fontSize="display-l" fontWeight="bold" color={item.type === 'success' ? 'text-status-success' : item.type === 'warning' ? 'text-status-warning' : 'text-body-secondary'}>
                      {item.value}
                    </Box>
                  )
                },
                {
                  id: 'description',
                  content: item => (
                    <Box variant="small" color="text-body-secondary">
                      {item.description}
                    </Box>
                  )
                }
              ]
            }}
            cardsPerRow={[{ cards: 1 }, { minWidth: 500, cards: 2 }, { minWidth: 800, cards: 3 }]}
            items={overviewCards}
          />

          {/* Usage Trends */}
          <Container>
            <Header variant="h2">Usage & Revenue Trends</Header>
            <Box>
              <LineChart
                width={800}
                height={300}
                data={usageData}
                margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis yAxisId="left" />
                <YAxis yAxisId="right" orientation="right" />
                <Tooltip />
                <Legend />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="requests"
                  stroke="#0073bb"
                  strokeWidth={2}
                  name="API Requests"
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="revenue"
                  stroke="#037f0c"
                  strokeWidth={2}
                  name="Revenue ($)"
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="blocked"
                  stroke="#d13212"
                  strokeWidth={2}
                  name="Blocked Requests"
                />
              </LineChart>
            </Box>
          </Container>


        </SpaceBetween>
      )
    },
    {
      id: 'content',
      label: 'Content Performance',
      content: (
        <SpaceBetween size="l">
          {/* Content Performance Flow */}
          <Container>
            <Header 
              variant="h2"
              actions={
                <Select
                  selectedOption={{ 
                    label: timePeriod.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                    value: timePeriod 
                  }}
                  onChange={({ detail }) => setTimePeriod(detail.selectedOption.value)}
                  options={[
                    { label: 'Last 24 Hours', value: 'last_24_hours' },
                    { label: 'Last 7 Days', value: 'last_7_days' },
                    { label: 'Last 30 Days', value: 'last_30_days' },
                    { label: 'Last 90 Days', value: 'last_90_days' },
                  ]}
                />
              }
            >
              Content Performance Flow
            </Header>
            <Box>
              <SankeyChart
                data={contentPerformanceSankeyData}
                width={900}
                height={400}
                title="Content Performance Flow: Categories → Total Requests → Revenue Streams"
                showMetrics={true}
                dataAvailable={sankeyDataAvailable.contentPerformance}
                onRefresh={() => loadSankeyData(`last_${selectedTimeRange.amount}_${selectedTimeRange.unit}${selectedTimeRange.amount > 1 ? 's' : ''}`)}
              />
            </Box>
          </Container>

          <Container>
            <Header
              variant="h2"
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button>Export Data</Button>
                  <Button variant="primary" href="/content/add">Add Content</Button>
                </SpaceBetween>
              }
            >
              Content Performance Details
            </Header>
            <Table
              columnDefinitions={[
                {
                  id: 'title',
                  header: 'Content Title',
                  cell: item => item.title
                },
                {
                  id: 'category',
                  header: 'Category',
                  cell: item => {
                    // Handle standardized IAB categories format
                    const category = item.iab_categories?.[0];
                    const displayName = category?.name || category?.tier_1 || item.content_type;
                    return <Badge color="blue">{displayName}</Badge>;
                  }
                },
                {
                  id: 'status',
                  header: 'Status',
                  cell: item => (
                    <StatusIndicator type={item.status === 'published' ? 'success' : 'in-progress'}>
                      {item.status}
                    </StatusIndicator>
                  )
                },
                {
                  id: 'requests',
                  header: 'API Requests',
                  cell: item => (item.analytics?.requests || 0).toLocaleString()
                },
                {
                  id: 'revenue',
                  header: 'Revenue',
                  cell: item => `$${(item.analytics?.revenue || 0).toFixed(2)}`
                },
                {
                  id: 'blocked',
                  header: 'Blocked Attempts',
                  cell: item => (
                    <Badge color={item.blockedAttempts > 10 ? 'red' : 'grey'}>
                      {item.blockedAttempts}
                    </Badge>
                  )
                },
                {
                  id: 'lastAccessed',
                  header: 'Last Accessed',
                  cell: item => item.lastAccessed ? new Date(item.lastAccessed).toLocaleDateString() : 'Never'
                }
              ]}
              items={contentItems.slice(0, 10)} // Show first 10 items
              loading={loading}
              loadingText="Loading content..."
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No content found</b>
                  <Box variant="p" color="inherit">
                    Upload your first content to get started.
                  </Box>
                </Box>
              }
              filter={
                <TextFilter
                  filteringPlaceholder="Find content..."
                  filteringText={contentFilter}
                  onChange={({ detail }) => setContentFilter(detail.filteringText)}
                />
              }
            />
          </Container>
        </SpaceBetween>
      )
    },
    {
      id: 'partners',
      label: 'Partner Stats (Registered Bots)',
      content: (
        <SpaceBetween size="l">
          {/* Partner Analytics Flow */}
          <Container>
            <Header 
              variant="h2"
              actions={
                <Select
                  selectedOption={{ 
                    label: timePeriod.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                    value: timePeriod 
                  }}
                  onChange={({ detail }) => setTimePeriod(detail.selectedOption.value)}
                  options={[
                    { label: 'Last 24 Hours', value: 'last_24_hours' },
                    { label: 'Last 7 Days', value: 'last_7_days' },
                    { label: 'Last 30 Days', value: 'last_30_days' },
                    { label: 'Last 90 Days', value: 'last_90_days' },
                  ]}
                />
              }
            >
              Partner Analytics Flow
            </Header>
            <Box>
              <SankeyChart
                data={partnerAnalyticsSankeyData}
                width={900}
                height={400}
                title="Partner Analytics Flow: AI Partners → Total Traffic → Revenue Streams"
                showMetrics={true}
                dataAvailable={sankeyDataAvailable.partnerAnalytics}
                onRefresh={() => loadSankeyData(`last_${selectedTimeRange.amount}_${selectedTimeRange.unit}${selectedTimeRange.amount > 1 ? 's' : ''}`)}
              />
            </Box>
          </Container>

          <Container>
            <Header variant="h2">Top AI Partners Details</Header>
            <Table
              columnDefinitions={[
                {
                  id: 'name',
                  header: 'Partner',
                  cell: item => item.name
                },
                {
                  id: 'requests',
                  header: 'API Requests',
                  cell: item => (item.analytics?.requests || 0).toLocaleString()
                },
                {
                  id: 'revenue',
                  header: 'Revenue Generated',
                  cell: item => `$${(item.analytics?.revenue || 0).toFixed(2)}`
                },
                {
                  id: 'blocked',
                  header: 'Blocked Attempts',
                  cell: item => (
                    <Badge color={item.blocked > 20 ? 'red' : item.blocked > 10 ? 'grey' : 'green'}>
                      {item.blocked}
                    </Badge>
                  )
                },
                {
                  id: 'status',
                  header: 'Status',
                  cell: item => (
                    <StatusIndicator type={item.status === 'active' ? 'success' : 'stopped'}>
                      {item.status}
                    </StatusIndicator>
                  )
                }
              ]}
              items={topPartners}
              loadingText="Loading partners..."
            />
          </Container>
        </SpaceBetween>
      )
    },
    {
      id: 'public-endpoints',
      label: 'Public Endpoint Activity',
      content: (
        <SpaceBetween size="l">
          {/* Public Endpoint Activity Overview */}
          <Container>
            <Header 
              variant="h2"
              description="Monitor upper funnel bot activity on public endpoints and unregistered bot behavior"
              actions={
                <Select
                  selectedOption={{ 
                    label: timePeriod.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase()),
                    value: timePeriod 
                  }}
                  onChange={({ detail }) => setTimePeriod(detail.selectedOption.value)}
                  options={[
                    { label: 'Last 24 Hours', value: 'last_24_hours' },
                    { label: 'Last 7 Days', value: 'last_7_days' },
                    { label: 'Last 30 Days', value: 'last_30_days' },
                    { label: 'Last 90 Days', value: 'last_90_days' },
                  ]}
                />
              }
            >
              Public Endpoint Activity
            </Header>
            
            <Alert type="info" header="Public Endpoint Focus">
              This tab focuses on bot activity that doesn't touch registered bot content access endpoints, 
              including search engine crawlers, social media bots, and unregistered scraping attempts.
            </Alert>

            <ColumnLayout columns={4} variant="text-grid">
              <div>
                <Box variant="awsui-key-label">Total Public Requests</Box>
                <Box variant="h3" color="text-status-info">
                  {(usageMetrics?.total_requests || 0).toLocaleString()}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Search Engine Crawls</Box>
                <Box variant="h3" color="text-status-success">
                  {Math.floor((usageMetrics?.total_requests || 0) * 0.35).toLocaleString()}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Blocked Scrapers</Box>
                <Box variant="h3" color="text-status-error">
                  {Math.floor((usageMetrics?.total_requests || 0) * 0.15).toLocaleString()}
                </Box>
              </div>
              <div>
                <Box variant="awsui-key-label">Social Media Bots</Box>
                <Box variant="h3" color="text-status-info">
                  {Math.floor((usageMetrics?.total_requests || 0) * 0.08).toLocaleString()}
                </Box>
              </div>
            </ColumnLayout>
          </Container>

          {/* Bot Activity Flow - Sankey Diagram */}
          <Container>
            <Header variant="h2">Public Bot Activity Flow</Header>
            <Box>
              <SankeyChart 
                data={botActivitySankeyData} 
                width={800} 
                height={400}
                dataAvailable={sankeyDataAvailable.botActivity}
                onRefresh={() => loadSankeyData(`last_${selectedTimeRange.amount}_${selectedTimeRange.unit}${selectedTimeRange.amount > 1 ? 's' : ''}`)}
                title="Public Bot Activity Flow"
                showMetrics={true}
              />
            </Box>
          </Container>

          {/* Unregistered Bot Behavior */}
          <Container>
            <Header variant="h2">Unregistered Bot Behavior</Header>
            <Table
              columnDefinitions={[
                {
                  id: 'user_agent',
                  header: 'User Agent',
                  cell: item => <Box variant="code">{item.user_agent}</Box>
                },
                {
                  id: 'requests',
                  header: 'Requests',
                  cell: item => item.requests.toLocaleString()
                },
                {
                  id: 'blocked',
                  header: 'Blocked',
                  cell: item => (
                    <StatusIndicator type={item.blocked > 0 ? 'error' : 'success'}>
                      {item.blocked.toLocaleString()}
                    </StatusIndicator>
                  )
                },
                {
                  id: 'endpoints',
                  header: 'Top Endpoints',
                  cell: item => (
                    <SpaceBetween direction="horizontal" size="xs">
                      {item.endpoints.slice(0, 3).map((endpoint, index) => (
                        <Badge key={index} color="blue">{endpoint}</Badge>
                      ))}
                    </SpaceBetween>
                  )
                },
                {
                  id: 'classification',
                  header: 'Classification',
                  cell: item => (
                    <Badge color={
                      item.classification === 'Search Engine' ? 'green' :
                      item.classification === 'Social Media' ? 'blue' :
                      item.classification === 'Scraper' ? 'red' : 'grey'
                    }>
                      {item.classification}
                    </Badge>
                  )
                }
              ]}
              items={[
                {
                  user_agent: 'Googlebot/2.1',
                  requests: 15420,
                  blocked: 0,
                  endpoints: ['/sitemap.xml', '/robots.txt', '/api/content/discover'],
                  classification: 'Search Engine'
                },
                {
                  user_agent: 'facebookexternalhit/1.1',
                  requests: 8750,
                  blocked: 0,
                  endpoints: ['/api/content/metadata', '/api/content/preview'],
                  classification: 'Social Media'
                },
                {
                  user_agent: 'python-requests/2.28.1',
                  requests: 5230,
                  blocked: 5230,
                  endpoints: ['/api/content/bulk', '/api/query'],
                  classification: 'Scraper'
                },
                {
                  user_agent: 'curl/7.68.0',
                  requests: 3420,
                  blocked: 3420,
                  endpoints: ['/api/content', '/api/analytics'],
                  classification: 'Scraper'
                },
                {
                  user_agent: 'Bingbot/2.0',
                  requests: 2890,
                  blocked: 0,
                  endpoints: ['/sitemap.xml', '/api/content/discover'],
                  classification: 'Search Engine'
                }
              ]}
              loading={loading}
              header={
                <Header
                  counter="(5)"
                  description="Bot activity on public endpoints, excluding registered partner access"
                >
                  Unregistered Bot Activity
                </Header>
              }
            />
          </Container>

          {/* Time Period Controls */}
          <Container>
            <Header variant="h2">Activity Timeline</Header>
            <Box>
              <LineChart
                width={800}
                height={300}
                data={usageData.map(item => ({
                  ...item,
                  public_requests: Math.floor(item.requests * 0.6), // Mock public endpoint requests
                  search_crawls: Math.floor(item.requests * 0.35),
                  blocked_scrapers: Math.floor(item.requests * 0.15)
                }))}
                margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="public_requests"
                  stroke="#0073bb"
                  strokeWidth={2}
                  name="Public Requests"
                />
                <Line
                  type="monotone"
                  dataKey="search_crawls"
                  stroke="#037f0c"
                  strokeWidth={2}
                  name="Search Engine Crawls"
                />
                <Line
                  type="monotone"
                  dataKey="blocked_scrapers"
                  stroke="#d13212"
                  strokeWidth={2}
                  name="Blocked Scrapers"
                />
              </LineChart>
            </Box>
          </Container>
        </SpaceBetween>
      )
    }
  ]

  return (
    <Container>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Comprehensive view of your content performance, revenue, and bot control analytics."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <DateRangePicker
                onChange={({ detail }) => setSelectedTimeRange(detail.value)}
                value={selectedTimeRange}
                relativeOptions={[
                  { key: 'previous-5-minutes', amount: 5, unit: 'minute', type: 'relative' },
                  { key: 'previous-30-minutes', amount: 30, unit: 'minute', type: 'relative' },
                  { key: 'previous-1-hour', amount: 1, unit: 'hour', type: 'relative' },
                  { key: 'previous-6-hours', amount: 6, unit: 'hour', type: 'relative' },
                  { key: 'previous-1-day', amount: 1, unit: 'day', type: 'relative' },
                  { key: 'previous-7-days', amount: 7, unit: 'day', type: 'relative' },
                  { key: 'previous-30-days', amount: 30, unit: 'day', type: 'relative' }
                ]}
                i18nStrings={{
                  formatRelativeRange: (range) => {
                    const unit = range.unit === 'minute' ? 'min' : range.unit === 'hour' ? 'hr' : range.unit;
                    return `Previous ${range.amount} ${unit}${range.amount !== 1 ? 's' : ''}`;
                  },
                  formatUnit: (unit, value) => `${value} ${unit}${value !== 1 ? 's' : ''}`,
                  dateTimeConstraintText: 'Range must be between 6 hours and 30 days',
                  relativeModeTitle: 'Relative range',
                  absoluteModeTitle: 'Absolute range',
                  relativeRangeSelectionHeading: 'Choose a range',
                  startDateLabel: 'Start date',
                  endDateLabel: 'End date',
                  startTimeLabel: 'Start time',
                  endTimeLabel: 'End time',
                  clearButtonLabel: 'Clear and dismiss',
                  cancelButtonLabel: 'Cancel',
                  applyButtonLabel: 'Apply'
                }}
                isValidRange={range => {
                  if (range.type === 'absolute') {
                    const [startDateWithoutTime] = range.startDate.split('T');
                    const [endDateWithoutTime] = range.endDate.split('T');
                    if (!startDateWithoutTime || !endDateWithoutTime) {
                      return { valid: false, errorMessage: 'The selected date range is incomplete. Select a start and end date for the date range.' };
                    }
                    if (new Date(range.startDate) - new Date(range.endDate) > 0) {
                      return { valid: false, errorMessage: 'The selected date range is invalid. The start date must be before the end date.' };
                    }
                  }
                  return { valid: true };
                }}
                placeholder="Filter by date range"
              />
              <ObservabilityLinks
                agentArn={analyticsAgentArn}
                showTraces={true}
                showMetrics={true}
                showDashboard={true}
                variant="normal"
                size="normal"
              />
              <Button>Export Report</Button>
            </SpaceBetween>
          }
        >
          Publisher Dashboard
        </Header>
        
        {/* Observability Status */}
        <Container>
          <SpaceBetween direction="horizontal" size="s">
            <ObservabilityStatus 
              agentArn={analyticsAgentArn}
              showConfigStatus={true}
            />
            {mcpServerArn && (
              <Box>
                <SpaceBetween direction="horizontal" size="xs">
                  <span style={{ fontSize: '12px', color: '#5f6b7a' }}>MCP Server:</span>
                  <ObservabilityLinks
                    agentArn={mcpServerArn}
                    showTraces={true}
                    variant="link"
                    size="small"
                  />
                </SpaceBetween>
              </Box>
            )}
          </SpaceBetween>
        </Container>

        <Tabs
          activeTabId={activeTab}
          onChange={({ detail }) => setActiveTab(detail.activeTabId)}
          tabs={tabs}
        />
      </SpaceBetween>
    </Container>
  )
}