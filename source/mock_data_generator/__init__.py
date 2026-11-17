"""
Mock Data Generator for AI Content Monetization Platform

This module provides comprehensive mock data generation for DynamoDB tables
including content management, partner authentication, and access logging systems.
"""

from .config import GeneratorConfig
from .generator import MockDataGenerator

__version__ = "1.0.0"
__all__ = ["MockDataGenerator", "GeneratorConfig"]
