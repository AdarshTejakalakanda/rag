"""Unit tests for Agentic Retrieval Sufficiency Judge and Controlled Weighted-RRF Retry."""

from dataclasses import dataclass
from typing import List, Dict, Any, Tuple
from unittest.mock import MagicMock

from src.parsers.requirement_parser import RequirementChunk
from src.parsers.gherkin_parser import ScenarioChunk
from src.retrieval.rrf_fusion import RRFFusion
from src.retrieval.hybrid_retriever import HybridRetriever
from src.judge.llm_judge import LLMJudge, RequirementJudgeVerdict
from src.config import RetrievalConfig, JudgeConfig


def test_rrf_weighted_fusion():
    sc1 = ScenarioChunk(scenario_id="sc_1", scenario_name="Scenario One")
    sc2 = ScenarioChunk(scenario_id="sc_2", scenario_name="Scenario Two")
    sc3 = ScenarioChunk(scenario_id="sc_3", scenario_name="Scenario Three")

    # BM25 ranking: sc1 (rank 1), sc2 (rank 2)
    bm25_list = [(sc1, 10.0, 1), (sc2, 5.0, 2)]
    # Dense ranking: sc3 (rank 1), sc2 (rank 2)
    dense_list = [(sc3, 0.9, 1), (sc2, 0.7, 2)]

    # 1. Balanced RRF (weights [1.0, 1.0])
    balanced = RRFFusion.fuse(rankings=[bm25_list, dense_list], weights=[1.0, 1.0], k=60, top_n=3)
    balanced_ids = [s.scenario_id for s, _, _ in balanced]
    assert balanced_ids[0] == "sc_2"

    # 2. Lexical-heavy RRF (weights [2.0, 0.5])
    lexical = RRFFusion.fuse(rankings=[bm25_list, dense_list], weights=[2.0, 0.5], k=60, top_n=3)
    lexical_ids = [s.scenario_id for s, _, _ in lexical]
    assert lexical_ids.index("sc_1") < lexical_ids.index("sc_3")

    # 3. Dense-heavy RRF (weights [0.5, 2.0])
    dense_heavy = RRFFusion.fuse(rankings=[bm25_list, dense_list], weights=[0.5, 2.0], k=60, top_n=3)
    dense_ids = [s.scenario_id for s, _, _ in dense_heavy]
    assert dense_ids.index("sc_3") < dense_ids.index("sc_1")


def test_hybrid_retriever_cached_pool_and_retry():
    sc_bm = [ScenarioChunk(scenario_id=f"bm_{i}", scenario_name=f"BM Scenario {i}") for i in range(50)]
    sc_dn = [ScenarioChunk(scenario_id=f"dn_{i}", scenario_name=f"Dense Scenario {i}") for i in range(50)]

    mock_bm25 = MagicMock()
    mock_bm25.search.return_value = [(sc, 50 - idx, idx + 1) for idx, sc in enumerate(sc_bm)]

    mock_milvus = MagicMock()
    mock_milvus.search.return_value = [(sc.scenario_id, 0.9 - (idx * 0.01), {"repo_id": "test"}) for idx, sc in enumerate(sc_dn)]

    mock_embedder = MagicMock()
    mock_embedder.encode_query.return_value = [0.1] * 384

    mock_reranker = MagicMock()
    # Reranker returns candidates in given order
    mock_reranker.rerank.side_effect = lambda query, candidates, top_k: candidates[:top_k]

    retriever = HybridRetriever(
        bm25_index=mock_bm25,
        milvus_store=mock_milvus,
        embedding_model=mock_embedder,
        reranker=mock_reranker,
        config=RetrievalConfig(bm25_top_k=50, dense_top_k=50, rrf_top_k=25, reranker_top_k=10),
    )

    req = RequirementChunk(req_id="REQ-1", title="User Login", description="User Login authentication", full_text="User Login authentication")
    top10, pool = retriever.retrieve_with_pool(req)

    assert len(top10) == 10
    assert len(pool["bm25_hits"]) == 50
    assert len(pool["dense_hits"]) == 50
    assert mock_bm25.search.call_count == 1
    assert mock_milvus.search.call_count == 1

    retry_top10 = retriever.retry_with_strategy(pool, strategy="LEXICAL_HEAVY")
    assert len(retry_top10) == 10
    assert mock_bm25.search.call_count == 1
    assert mock_milvus.search.call_count == 1


def test_agentic_judge_single_call_when_sufficient():
    mock_client = MagicMock()
    mock_client.provider = "mock"
    mock_client.generate_json.return_value = {
        "retrieval_sufficiency": {
            "decision": "SUFFICIENT_EVIDENCE",
            "reason": "Top candidates contain complete evidence for login and password validation.",
            "retry_strategy": "NONE"
        },
        "evaluations": [
            {
                "scenario_id": "sc_1",
                "status": "FULLY_COVERED",
                "covered_criteria": ["AC-1"],
                "missing_criteria": [],
                "reasoning": "Validates password authentication perfectly.",
                "evidence": ["Then user is authenticated"]
            }
        ],
        "reasoning_summary": "Requirement fully covered by scenario 1."
    }

    judge = LLMJudge(client=mock_client)
    req = RequirementChunk(
        req_id="REQ-1",
        title="Login",
        description="Login requirement",
        acceptance_criteria=["[AC-1] Valid credentials allow login"],
        full_text="Login requirement"
    )
    sc1 = ScenarioChunk(scenario_id="sc_1", scenario_name="Valid password authentication", canonical_text="Then user is authenticated")
    candidates = [(sc1, 0.95, {})]

    retrieval_pool = {"bm25_hits": [(sc1, 10.0, 1)], "dense_hits": [(sc1, 0.9, 1)]}
    mock_retriever = MagicMock()

    verdict = judge.judge_requirement(
        requirement=req,
        candidates=candidates,
        retrieval_pool=retrieval_pool,
        retriever=mock_retriever,
        bypass_cache=True,
    )

    assert verdict.overall_classification == "FULLY_COVERED"
    assert verdict.retrieval_decision == "SUFFICIENT_EVIDENCE"
    assert verdict.retry_strategy == "NONE"
    assert verdict.was_retried is False
    assert verdict.llm_calls_count == 1
    assert mock_client.generate_json.call_count == 1
    assert mock_retriever.retry_with_strategy.call_count == 0


def test_agentic_judge_controlled_retry_on_insufficient():
    mock_client = MagicMock()
    mock_client.provider = "mock"

    call1_response = {
        "retrieval_sufficiency": {
            "decision": "INSUFFICIENT_EVIDENCE",
            "reason": "Candidates contain alert creation but lack inactivation steps.",
            "retry_strategy": "LEXICAL_HEAVY"
        },
        "evaluations": [
            {
                "scenario_id": "sc_1",
                "status": "PARTIALLY_COVERED",
                "covered_criteria": ["AC-1"],
                "missing_criteria": ["AC-2"],
                "reasoning": "Covers alert creation only.",
                "evidence": ["When alert is generated"]
            }
        ],
        "reasoning_summary": "Missing alert inactivation."
    }

    call2_response = {
        "retrieval_sufficiency": {
            "decision": "SUFFICIENT_EVIDENCE",
            "reason": "Surfaced scenario with explicit alert inactivation.",
            "retry_strategy": "NONE"
        },
        "evaluations": [
            {
                "scenario_id": "sc_2",
                "status": "FULLY_COVERED",
                "covered_criteria": ["AC-1", "AC-2"],
                "missing_criteria": [],
                "reasoning": "Covers both alert creation and inactivation.",
                "evidence": ["When alert is inactivated"]
            }
        ],
        "reasoning_summary": "Full workflow covered across candidates."
    }

    mock_client.generate_json.side_effect = [call1_response, call2_response]

    judge = LLMJudge(client=mock_client)
    req = RequirementChunk(
        req_id="REQ-2",
        title="Alert Management",
        description="Alert generation and inactivation",
        acceptance_criteria=["[AC-1] Alert generation", "[AC-2] Alert inactivation"],
        full_text="Alert generation and inactivation"
    )
    sc1 = ScenarioChunk(scenario_id="sc_1", scenario_name="Alert Creation", canonical_text="When alert is generated")
    sc2 = ScenarioChunk(scenario_id="sc_2", scenario_name="Alert Inactivation", canonical_text="When alert is inactivated")

    initial_candidates = [(sc1, 0.8, {})]
    retrieval_pool = {"bm25_hits": [(sc2, 12.0, 1)], "dense_hits": [(sc1, 0.9, 1)]}

    mock_retriever = MagicMock()
    mock_retriever.retry_with_strategy.return_value = [(sc2, 0.92, {}), (sc1, 0.8, {})]

    verdict = judge.judge_requirement(
        requirement=req,
        candidates=initial_candidates,
        retrieval_pool=retrieval_pool,
        retriever=mock_retriever,
        bypass_cache=True,
    )

    assert verdict.overall_classification == "FULLY_COVERED"
    assert verdict.was_retried is True
    assert verdict.retry_strategy == "LEXICAL_HEAVY"
    assert verdict.llm_calls_count == 2
    assert mock_client.generate_json.call_count == 2
    assert mock_retriever.retry_with_strategy.call_count == 1
