"""Reciprocal Rank Fusion (RRF) for combining Sparse and Dense retrieval results."""

from typing import List, Dict, Tuple, Any, Optional
from src.parsers.gherkin_parser import ScenarioChunk


class RRFFusion:
    """Combines ranked retrieval results using Reciprocal Rank Fusion."""

    @staticmethod
    def fuse(
        ranked_lists: Optional[List[List[Tuple[ScenarioChunk, float, int]]]] = None,
        k: int = 60,
        top_n: int = 20,
        rankings: Optional[List[List[Tuple[ScenarioChunk, float, int]]]] = None,
    ) -> List[Tuple[ScenarioChunk, float, Dict[str, Any]]]:
        """
        Fuses multiple ranked lists into a single ranked list.

        Args:
            ranked_lists: List of rankings, where each item is (ScenarioChunk, score, 1-indexed rank)
            k: Smoothing constant (default 60)
            top_n: Number of top items to return (default 20 as in architecture)

        Returns:
            List of (ScenarioChunk, rrf_score, details_dict) sorted descending by rrf_score.
        """
        target_lists = ranked_lists if ranked_lists is not None else (rankings or [])
        rrf_scores: Dict[str, float] = {}
        scenario_map: Dict[str, ScenarioChunk] = {}
        details: Dict[str, Dict[str, Any]] = {}

        for list_idx, rank_list in enumerate(target_lists):
            source_name = f"source_{list_idx}"
            for item in rank_list:
                scenario, raw_score, rank = item
                sid = scenario.scenario_id
                scenario_map[sid] = scenario

                if sid not in rrf_scores:
                    rrf_scores[sid] = 0.0
                    details[sid] = {"sources": {}, "ranks": {}, "raw_scores": {}}

                score_contrib = 1.0 / (k + rank)
                rrf_scores[sid] += score_contrib
                details[sid]["sources"][source_name] = score_contrib
                details[sid]["ranks"][source_name] = rank
                details[sid]["raw_scores"][source_name] = raw_score

        # Sort by RRF score descending
        sorted_sids = sorted(rrf_scores.keys(), key=lambda s: rrf_scores[s], reverse=True)

        results = []
        for sid in sorted_sids[:top_n]:
            results.append((scenario_map[sid], rrf_scores[sid], details[sid]))

        return results
