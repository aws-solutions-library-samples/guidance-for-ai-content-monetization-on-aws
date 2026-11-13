"""
Feature-flag subsystem — REMOVED (wave0 Phase 3 / GitLab #21b).

The mock/real data-source switching subsystem that previously lived here
(``FeatureFlagController``, ``DataSourceManager``, ``get_data_with_fallback``,
``use_real_data``, the ``DataSource`` enum, and the ``USE_REAL_*_DATA`` /
``ENABLE_GRACEFUL_FALLBACK`` flags) has been removed. The codebase now reads
real data only: read paths return real-source data or an empty result, and a
real-source failure surfaces as an error (wave0 Req 9).

This module is intentionally left in place (empty of symbols) so the package
layout is unchanged; it exports nothing.
"""

__all__: list = []
