"""Tests for Cross-Encoder reranking."""

import pytest
from src.parsers.gherkin_parser import ScenarioChunk
from src.retrieval.reranker import CrossEncoderReranker


def test_cross_encoder_rerank():
    reranker = CrossEncoderReranker(top_k=5)
    
    s1 = ScenarioChunk(
        scenario_id="s1", feature_title="Auth", feature_description="",
        scenario_name="Login Valid", scenario_type="Scenario",
        full_text="Scenario: Valid Login with user email and password"
    )
    s2 = ScenarioChunk(
        scenario_id="s2", feature_title="Cart", feature_description="",
        scenario_name="Add to Cart", scenario_type="Scenario",
        full_text="Scenario: Add item to shopping basket"
    )

    candidates = [
        (s2, 0.03, {"source": "rrf"}),
        (s1, 0.02, {"source": "rrf"}),
    ]

    reranked = reranker.rerank(query="User authentication and login credentials", candidates=candidates, top_n=2)
    assert len(reranked) == 2
    # s1 should be ranked above s2 due to query relevance
    assert reranked[0][0].scenario_id == "s1"
    assert "cross_encoder_score" in reranked[0][2]
