"""
Configuration management for mock data generator.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GeneratorConfig:
    """Configuration for mock data generation with AWS settings and data counts."""

    # Data volume configuration
    content_count: int = 25
    partner_count: int = 5
    access_log_count: int = 100
    job_count: int = 50
    analytics_count: int = 0  # Calculated based on date_range_months

    # Time range configuration
    date_range_months: int = 1  # Only 1 month of data

    # Data management options
    clear_existing: bool = False
    # How long a clear waits for the content deindexer to remove the soft-deleted content records
    # before deleting what is left directly.
    deindex_wait_seconds: int = 180

    # AWS configuration
    # None uses the standard AWS credential chain (AWS_PROFILE, default profile, or role).
    aws_profile: Optional[str] = None
    aws_region: str = "us-west-2"

    # Content type configuration
    content_types: List[str] = field(default_factory=lambda: ["article", "report", "video", "image", "document"])

    # Partner type configuration
    partner_types: List[str] = field(default_factory=lambda: ["publisher", "ai_operator", "analytics_provider"])

    # DynamoDB table names (will be populated from CDK stack)
    content_table_name: str = ""
    partner_table_name: str = ""
    access_logs_table_name: str = ""
    jobs_table_name: str = ""
    analytics_table_name: str = ""

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.content_count <= 0:
            raise ValueError("content_count must be positive")
        if self.partner_count <= 0:
            raise ValueError("partner_count must be positive")
        if self.access_log_count <= 0:
            raise ValueError("access_log_count must be positive")
        if self.date_range_months <= 0:
            raise ValueError("date_range_months must be positive")
