"""Dataset KPI aggregation (E3)."""

from sensors_dcs.metrics.aggregate import aggregate_metrics, write_metrics_summary
from sensors_dcs.metrics.coverage import (
    build_coverage_block,
    extract_episode_coverage,
    summarize_coverage,
)

__all__ = [
    "aggregate_metrics",
    "write_metrics_summary",
    "build_coverage_block",
    "extract_episode_coverage",
    "summarize_coverage",
]
