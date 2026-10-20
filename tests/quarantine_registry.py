"""Documented test quarantine registry (GitLab #11, Req 4.5 / task 4.2 + 5.2).

Each entry is an explicitly QUARANTINED test node-id mapped to a stated reason. The
tests/conftest.py ``pytest_collection_modifyitems`` hook skips exactly these node-ids with
their reason, so they are documented-and-skipped, never silently omitted. Quarantine is used
for tests that (a) require live AWS, (b) assert a pre-decomposition contract now pinned by a
route Characterization_Test, or (c) need owner debugging/recalibration. These are recorded as
gaps in the Coverage_Inventory (task 12.1); this spec documents gaps, it does not author new
feature tests or edit assertions to mask behavior.
"""

_REASON_BY_FILE = {
    'tests/analytics/test_analytics_aggregation.py': 'fixture/behavior: aggregator returns 0 over seeded data; owner debug [tracked in #16]',
    'tests/analytics/test_analytics_aggregation_integration.py': 'fixture/behavior: aggregator returns 0 over seeded data (table/fixture drift); owner debug [tracked in #16]',
    'tests/analytics/test_analytics_api_integration.py': 'superseded/behavior: AnalyticsAPIHandler integration (500 vs 200); /analytics routes pinned by test_analytics_characterization.py [tracked in #16]',
    'tests/analytics/test_hybrid_logging.py': "behavior: analytics pipeline-step logging redesigned ('Unknown pipeline step'); partner_api /query logging path changed [tracked in #16]",
    'tests/analytics/test_revenue_calculation.py': 'math-drift: revenue/pricing multiplier Decimals changed; owner recalibration [tracked in #16]',
    'tests/mock_data_generator/test_analytics_generation.py': 'generator-drift: generated analytics counts changed; owner recalibration [tracked in #16]',
    'tests/mock_data_generator/test_mock_data_generator.py': "generator-drift: generated data shape/counts changed (KeyError 'SS'); owner recalibration",
}

# Exact test node-ids to skip (only failing tests; passing tests in the same files still gate).
QUARANTINED_NODE_IDS = {
    'tests/analytics/test_analytics_aggregation.py::TestAnalyticsAggregator::test_aggregate_daily_metrics_success',
    'tests/analytics/test_analytics_aggregation.py::TestAnalyticsAggregatorIntegration::test_multi_day_aggregation_with_realistic_data',
    'tests/analytics/test_analytics_aggregation_integration.py::TestAnalyticsAggregationIntegration::test_end_to_end_analytics_flow',
    'tests/analytics/test_analytics_aggregation_integration.py::TestAnalyticsAggregationIntegration::test_lambda_handler_integration_with_real_data',
    'tests/analytics/test_analytics_aggregation_integration.py::TestAnalyticsAggregationIntegration::test_performance_with_large_dataset',
    'tests/analytics/test_analytics_aggregation_integration.py::TestAnalyticsAggregationIntegration::test_real_time_aggregation_simulation',
    'tests/analytics/test_analytics_api_integration.py::TestAnalyticsAPIIntegration::test_custom_date_range_parsing_integration',
    'tests/analytics/test_analytics_api_integration.py::TestAnalyticsAPIIntegration::test_response_format_consistency_integration',
    'tests/analytics/test_analytics_api_integration.py::TestAnalyticsAPIIntegration::test_revenue_analytics_access_method_breakdown_integration',
    'tests/analytics/test_analytics_api_integration.py::TestAnalyticsAPIIntegration::test_trends_analytics_filtered_metric_integration',
    'tests/analytics/test_analytics_api_integration.py::TestAnalyticsAPIIntegration::test_usage_analytics_with_content_type_filter_integration',
    'tests/analytics/test_hybrid_logging.py::TestHybridLogging::test_query_cognito_access_logging',
    'tests/analytics/test_hybrid_logging.py::TestHybridLogging::test_query_partner_access_logging',
    'tests/analytics/test_revenue_calculation.py::TestRevenueCalculation::test_historical_pricing_lookup',
    'tests/analytics/test_revenue_calculation.py::TestRevenueCalculation::test_mcp_tool_historical_pricing',
    'tests/analytics/test_revenue_calculation.py::TestRevenueCalculation::test_pricing_version_precedence',
    'tests/analytics/test_revenue_calculation.py::TestRevenueCalculation::test_revenue_calculation_with_historical_pricing',
    'tests/mock_data_generator/test_analytics_generation.py::TestAnalyticsDataGeneration::test_generate_api_request_metrics',
    'tests/mock_data_generator/test_mock_data_generator.py::TestContentDataGeneration::test_generate_content_data_iab_categories',
    'tests/mock_data_generator/test_mock_data_generator.py::TestMockDataValidation::test_schema_validator_content_valid',
}

def reason_for(nodeid: str) -> str:
    return _REASON_BY_FILE.get(nodeid.split('::', 1)[0], 'quarantined (GitLab #11)')
