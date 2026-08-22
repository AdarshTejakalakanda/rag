"""Tests for RRF Fusion ranking."""

import pytest
from src.parsers.gherkin_parser import ScenarioChunk
from src.retrieval.rrf_fusion import RRFFusion


def test_rrf_fusion():
    s1 = ScenarioChunk(scenario_id="s1", feature_title="F1", feature_description="", scenario_name="S1", scenario_type="Scenario")
    s2 = ScenarioChunk(scenario_id="s2", feature_title="F2", feature_description="", scenario_name="S2", scenario_type="Scenario")
    s3 = ScenarioChunk(scenario_id="s3", feature_title="F3", feature_description="", scenario_name="S3", scenario_type="Scenario")

    bm25_list = [(s1, 10.0, 1), (s2, 5.0, 2)]
    dense_list = [(s2, 0.9, 1), (s1, 0.8, 2), (s3, 0.5, 3)]

    # s1 score: 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.01639 + 0.01613 = 0.03252
    # s2 score: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.03252
    # s3 score: 1/(60+3) = 0.01587

    results = RRFFusion.fuse([bm25_list, dense_list], k=60, top_n=20)
    assert len(results) == 3
    # Top 2 should be s1 and s2 (tied) and s3 last
    assert results[2][0].scenario_id == "s3"
    assert results[0][1] > results[2][1]
