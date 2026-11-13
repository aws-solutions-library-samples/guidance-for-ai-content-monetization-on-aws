"""
Feature Flag System for Content Management

This module provides feature flag functionality to control the transition
between mock data and real DynamoDB integration. It supports graceful
fallback mechanisms and environment-based configuration.
"""

import logging
import os
from enum import Enum
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


class DataSource(Enum):
    """Enumeration of available data sources"""

    MOCK = "mock"
    REAL = "real"
    AUTO = "auto"  # Automatically choose based on availability


class FeatureFlagController:
    """
    Controls feature flags for content management operations

    This class manages the transition between mock and real data sources,
    providing graceful fallback mechanisms and centralized flag management.
    """

    def __init__(self):
        """Initialize feature flag controller with environment variables"""
        self._flags = self._load_feature_flags()
        self._fallback_enabled = True
        logger.info(f"Feature flags initialized: {self._flags}")

    def _load_feature_flags(self) -> Dict[str, Any]:
        """Load feature flags from environment variables"""
        return {
            # Content management flags
            "use_real_content_data": self._parse_bool_env("USE_REAL_CONTENT_DATA", False),
            "use_real_processing_data": self._parse_bool_env("USE_REAL_PROCESSING_DATA", False),
            "use_real_analytics_data": self._parse_bool_env("USE_REAL_ANALYTICS_DATA", False),
            "use_real_bot_control_data": self._parse_bool_env("USE_REAL_BOT_CONTROL_DATA", False),
            # Fallback and safety flags
            "enable_graceful_fallback": self._parse_bool_env("ENABLE_GRACEFUL_FALLBACK", True),
            "fallback_timeout_ms": int(os.getenv("FALLBACK_TIMEOUT_MS", "5000")),
            "max_retry_attempts": int(os.getenv("MAX_RETRY_ATTEMPTS", "3")),
            # Logging and monitoring flags
            "log_data_source_usage": self._parse_bool_env("LOG_DATA_SOURCE_USAGE", True),
            "enable_performance_monitoring": self._parse_bool_env("ENABLE_PERFORMANCE_MONITORING", True),
            # Component-specific flags
            "real_data_components": self._parse_list_env("REAL_DATA_COMPONENTS", []),
            "mock_data_components": self._parse_list_env("MOCK_DATA_COMPONENTS", []),
        }

    def _parse_bool_env(self, env_var: str, default: bool) -> bool:
        """Parse boolean environment variable with default fallback"""
        value = os.getenv(env_var, str(default)).lower()
        return value in ("true", "1", "yes", "on", "enabled")

    def _parse_list_env(self, env_var: str, default: list) -> list:
        """Parse comma-separated list environment variable"""
        value = os.getenv(env_var, "")
        if not value:
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    def use_real_data(self, component: str) -> bool:
        """
        Check if a component should use real data

        Args:
            component: Component name (e.g., 'content', 'processing', 'analytics')

        Returns:
            bool: True if component should use real data, False for mock data
        """
        # Check component-specific override
        if component in self._flags.get("real_data_components", []):
            logger.debug(f"Component {component} forced to use real data")
            return True

        if component in self._flags.get("mock_data_components", []):
            logger.debug(f"Component {component} forced to use mock data")
            return False

        # Check component-specific flag
        flag_key = f"use_real_{component}_data"
        if flag_key in self._flags:
            result = self._flags[flag_key]
            if self._flags.get("log_data_source_usage", True):
                logger.info(f"Component {component} using {'real' if result else 'mock'} data (flag: {flag_key})")
            return result

        # Default to mock data for safety
        logger.debug(f"Component {component} defaulting to mock data (no specific flag)")
        return False

    def get_data_source(self, component: str) -> DataSource:
        """
        Get the data source for a component

        Args:
            component: Component name

        Returns:
            DataSource: The data source to use
        """
        if self.use_real_data(component):
            return DataSource.REAL
        return DataSource.MOCK

    def enable_graceful_fallback(self) -> bool:
        """Check if graceful fallback is enabled"""
        return self._flags.get("enable_graceful_fallback", True)

    def get_fallback_timeout_ms(self) -> int:
        """Get fallback timeout in milliseconds"""
        return self._flags.get("fallback_timeout_ms", 5000)

    def get_max_retry_attempts(self) -> int:
        """Get maximum retry attempts for real data operations"""
        return self._flags.get("max_retry_attempts", 3)

    def should_log_data_source_usage(self) -> bool:
        """Check if data source usage should be logged"""
        return self._flags.get("log_data_source_usage", True)

    def enable_performance_monitoring(self) -> bool:
        """Check if performance monitoring is enabled"""
        return self._flags.get("enable_performance_monitoring", True)

    def update_flag(self, flag_name: str, value: Any) -> None:
        """
        Update a feature flag at runtime

        Args:
            flag_name: Name of the flag to update
            value: New value for the flag
        """
        old_value = self._flags.get(flag_name)
        self._flags[flag_name] = value
        logger.info(f"Feature flag {flag_name} updated from {old_value} to {value}")

    def get_all_flags(self) -> Dict[str, Any]:
        """Get all current feature flags"""
        return self._flags.copy()

    def reset_flags(self) -> None:
        """Reset all flags to environment defaults"""
        self._flags = self._load_feature_flags()
        logger.info("Feature flags reset to environment defaults")


class DataSourceManager:
    """
    Manages data source selection and fallback logic

    This class provides the core functionality for choosing between
    mock and real data sources with automatic fallback capabilities.
    """

    def __init__(self, feature_controller: Optional[FeatureFlagController] = None):
        """Initialize data source manager"""
        self.feature_controller = feature_controller or FeatureFlagController()
        self.performance_metrics = {}

    def get_data_with_fallback(
        self, component: str, real_data_func: callable, mock_data_func: callable, *args, **kwargs
    ) -> Dict[str, Any]:
        """
        Get data with automatic fallback between real and mock sources

        Args:
            component: Component name for feature flag lookup
            real_data_func: Function to call for real data
            mock_data_func: Function to call for mock data
            *args, **kwargs: Arguments to pass to data functions

        Returns:
            Dict containing data and metadata about the source used
        """
        start_time = self._get_current_time_ms()

        try:
            data_source = self.feature_controller.get_data_source(component)

            if data_source == DataSource.REAL:
                # Try real data first
                result = self._try_real_data(component, real_data_func, *args, **kwargs)
                if result["success"]:
                    self._record_performance(component, "real", start_time, True)
                    return result

                # Fall back to mock if enabled and real data failed
                if self.feature_controller.enable_graceful_fallback():
                    logger.warning(f"Real data failed for {component}, falling back to mock data")
                    fallback_result = self._try_mock_data(component, mock_data_func, *args, **kwargs)
                    if fallback_result["success"]:
                        fallback_result["data_source"] = "mock_fallback"  # Override data source to indicate fallback
                        self._record_performance(component, "mock_fallback", start_time, True)
                        return fallback_result
                    else:
                        # Both real and mock failed
                        self._record_performance(component, "mock_fallback", start_time, False)
                        return {
                            "success": False,
                            "data": None,
                            "data_source": "none",
                            "error": f"Real data failed: {result.get('error', 'Unknown error')}, Mock fallback failed: {fallback_result.get('error', 'Unknown error')}",
                            "timestamp": self._get_current_time_ms(),
                        }
                else:
                    self._record_performance(component, "real", start_time, False)
                    return result

            else:  # DataSource.MOCK
                result = self._try_mock_data(component, mock_data_func, *args, **kwargs)
                self._record_performance(component, "mock", start_time, result["success"])
                return result

        except Exception as e:
            logger.error(f"Unexpected error in data source manager for {component}: {str(e)}")
            # Emergency fallback to mock data
            try:
                result = self._try_mock_data(component, mock_data_func, *args, **kwargs)
                result["fallback_reason"] = f"Emergency fallback due to: {str(e)}"
                result["data_source"] = "emergency_fallback"  # Override data source to indicate emergency fallback
                self._record_performance(component, "emergency_fallback", start_time, result["success"])
                return result
            except Exception as fallback_error:
                logger.error(f"Emergency fallback also failed for {component}: {str(fallback_error)}")
                return {
                    "success": False,
                    "data": None,
                    "data_source": "none",
                    "error": f"All data sources failed: {str(e)}, fallback: {str(fallback_error)}",
                    "timestamp": self._get_current_time_ms(),
                }

    def _try_real_data(self, component: str, real_data_func: callable, *args, **kwargs) -> Dict[str, Any]:
        """Try to get real data with retry logic"""
        max_attempts = self.feature_controller.get_max_retry_attempts()

        for attempt in range(max_attempts):
            try:
                data = real_data_func(*args, **kwargs)
                return {
                    "success": True,
                    "data": data,
                    "data_source": "real",
                    "attempt": attempt + 1,
                    "timestamp": self._get_current_time_ms(),
                }
            except Exception as e:
                logger.warning(f"Real data attempt {attempt + 1}/{max_attempts} failed for {component}: {str(e)}")
                if attempt == max_attempts - 1:
                    return {
                        "success": False,
                        "data": None,
                        "data_source": "real",
                        "error": str(e),
                        "attempts": max_attempts,
                        "timestamp": self._get_current_time_ms(),
                    }

    def _try_mock_data(self, component: str, mock_data_func: callable, *args, **kwargs) -> Dict[str, Any]:
        """Try to get mock data"""
        try:
            data = mock_data_func(*args, **kwargs)
            return {"success": True, "data": data, "data_source": "mock", "timestamp": self._get_current_time_ms()}
        except Exception as e:
            logger.error(f"Mock data failed for {component}: {str(e)}")
            return {
                "success": False,
                "data": None,
                "data_source": "mock",
                "error": str(e),
                "timestamp": self._get_current_time_ms(),
            }

    def _record_performance(self, component: str, source: str, start_time: int, success: bool):
        """Record performance metrics if monitoring is enabled"""
        if not self.feature_controller.enable_performance_monitoring():
            return

        end_time = self._get_current_time_ms()
        duration = end_time - start_time

        if component not in self.performance_metrics:
            self.performance_metrics[component] = {}

        if source not in self.performance_metrics[component]:
            self.performance_metrics[component][source] = {
                "total_calls": 0,
                "successful_calls": 0,
                "total_duration_ms": 0,
                "avg_duration_ms": 0,
            }

        metrics = self.performance_metrics[component][source]
        metrics["total_calls"] += 1
        if success:
            metrics["successful_calls"] += 1
        metrics["total_duration_ms"] += duration
        metrics["avg_duration_ms"] = metrics["total_duration_ms"] / metrics["total_calls"]

        logger.debug(f"Performance: {component}.{source} - {duration}ms, success: {success}")

    def _get_current_time_ms(self) -> int:
        """Get current time in milliseconds"""
        import time

        return int(time.time() * 1000)

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for all components"""
        return self.performance_metrics.copy()


def use_real_data(component: str) -> bool:
    """Convenience function to check if component should use real data"""
    controller = FeatureFlagController()
    return controller.use_real_data(component)


def get_data_with_fallback(component: str, real_data_func: callable, mock_data_func: callable, *args, **kwargs):
    """Convenience function to get data with fallback"""
    controller = FeatureFlagController()
    manager = DataSourceManager(controller)
    return manager.get_data_with_fallback(component, real_data_func, mock_data_func, *args, **kwargs)
