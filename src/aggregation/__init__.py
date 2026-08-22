"""Aggregation and Reporting modules."""

from src.aggregation.aggregator import CoverageAggregator, GlobalCoverageReport
from src.aggregation.report_generator import ReportGenerator

__all__ = ["CoverageAggregator", "GlobalCoverageReport", "ReportGenerator"]
