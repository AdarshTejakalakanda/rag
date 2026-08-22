"""Deterministic Requirement Coverage Aggregator conforming to Specification §17."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from src.judge.llm_judge import RequirementJudgeVerdict


@dataclass
class GlobalCoverageReport:
    """Global aggregate summary across all evaluated business requirements."""
    total_requirements: int
    covered_count: int
    partial_count: int
    uncovered_count: int
    average_match_pct: float
    coverage_rate: float
    partial_rate: float
    uncovered_rate: float
    total_feature_scenarios: int
    category_breakdown: Dict[str, Any]
    verdicts: List[RequirementJudgeVerdict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requirements": self.total_requirements,
            "covered_count": self.covered_count,
            "partial_count": self.partial_count,
            "uncovered_count": self.uncovered_count,
            "average_match_pct": round(self.average_match_pct, 2),
            "coverage_rate": round(self.coverage_rate, 2),
            "partial_rate": round(self.partial_rate, 2),
            "uncovered_rate": round(self.uncovered_rate, 2),
            "total_feature_scenarios": self.total_feature_scenarios,
            "category_breakdown": self.category_breakdown,
            "requirements": [v.to_dict() for v in self.verdicts],
        }


class CoverageAggregator:
    """Performs deterministic aggregation of independent evidence verdicts."""

    @classmethod
    def aggregate(
        cls,
        verdicts: List[RequirementJudgeVerdict],
        total_scenarios_count: int = 0
    ) -> GlobalCoverageReport:
        if not verdicts:
            return GlobalCoverageReport(
                total_requirements=0,
                covered_count=0,
                partial_count=0,
                uncovered_count=0,
                average_match_pct=0.0,
                coverage_rate=0.0,
                partial_rate=0.0,
                uncovered_rate=0.0,
                total_feature_scenarios=total_scenarios_count,
                category_breakdown={},
                verdicts=[],
            )

        total_reqs = len(verdicts)
        covered = sum(1 for v in verdicts if v.overall_classification == "FULLY_COVERED")
        partial = sum(1 for v in verdicts if v.overall_classification == "PARTIALLY_COVERED")
        uncovered = sum(1 for v in verdicts if v.overall_classification in ("NOT_COVERED", "NOT_RELEVANT"))

        avg_match = sum(v.match_percentage for v in verdicts) / total_reqs

        # Category Breakdown
        categories: Dict[str, Dict[str, Any]] = {}
        for v in verdicts:
            cat = v.category or "General"
            if cat not in categories:
                categories[cat] = {
                    "total": 0, "covered": 0, "partial": 0, "uncovered": 0,
                    "total_match": 0
                }
            c_stat = categories[cat]
            c_stat["total"] += 1
            c_stat["total_match"] += v.match_percentage
            if v.overall_classification == "FULLY_COVERED":
                c_stat["covered"] += 1
            elif v.overall_classification == "PARTIALLY_COVERED":
                c_stat["partial"] += 1
            else:
                c_stat["uncovered"] += 1

        for cat, data in categories.items():
            t = data["total"]
            data["avg_match_pct"] = round(data["total_match"] / t, 1) if t else 0.0
            data["coverage_rate"] = round((data["covered"] / t) * 100, 1) if t else 0.0

        return GlobalCoverageReport(
            total_requirements=total_reqs,
            covered_count=covered,
            partial_count=partial,
            uncovered_count=uncovered,
            average_match_pct=avg_match,
            coverage_rate=(covered / total_reqs) * 100,
            partial_rate=(partial / total_reqs) * 100,
            uncovered_rate=(uncovered / total_reqs) * 100,
            total_feature_scenarios=total_scenarios_count,
            category_breakdown=categories,
            verdicts=verdicts,
        )
