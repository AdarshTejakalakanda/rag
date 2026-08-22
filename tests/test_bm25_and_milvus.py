"""Tests for BM25 and Milvus vector store."""

import pytest
import numpy as np
from src.parsers.gherkin_parser import ScenarioChunk
from src.index.bm25_index import BM25Index
from src.index.milvus_store import MilvusStore
from src.index.embedding_model import EmbeddingModel


@pytest.fixture
def sample_scenarios():
    s1 = ScenarioChunk(
        scenario_id="s1",
        feature_title="User Authentication",
        feature_description="Login feature",
        scenario_name="Valid login with email",
        scenario_type="Scenario",
        tags=["@auth"],
        steps=["Given user exists", "When user logs in", "Then access is granted"],
        file_path="features/auth.feature",
        line_number=10,
        full_text="Feature: User Authentication\nScenario: Valid login with email\nGiven user exists\nWhen user logs in\nThen access is granted"
    )
    s2 = ScenarioChunk(
        scenario_id="s2",
        feature_title="Order Checkout",
        feature_description="Checkout cart",
        scenario_name="Credit card payment",
        scenario_type="Scenario",
        tags=["@checkout"],
        steps=["Given items in cart", "When user pays with visa", "Then order is confirmed"],
        file_path="features/checkout.feature",
        line_number=20,
        full_text="Feature: Order Checkout\nScenario: Credit card payment\nGiven items in cart\nWhen user pays with visa\nThen order is confirmed"
    )
    return [s1, s2]


def test_bm25_index(sample_scenarios):
    bm25 = BM25Index()
    bm25.index_scenarios(sample_scenarios)
    
    results = bm25.search("login authentication", top_k=5)
    assert len(results) >= 1
    assert results[0][0].scenario_id == "s1"
    assert results[0][1] > 0


def test_milvus_store_and_embeddings(sample_scenarios, tmp_path):
    emb_model = EmbeddingModel()
    store = MilvusStore(db_path=tmp_path / "test_milvus.db", dim=384)
    
    vectors = emb_model.encode([s.full_text for s in sample_scenarios])
    store.upsert(sample_scenarios, vectors)
    
    q_vec = emb_model.encode("credit card checkout payment")
    results = store.search(q_vec, top_k=2)
    
    assert len(results) == 2
    assert results[0][0] == "s2"
    assert results[0][2]["scenario_name"] == "Credit card payment"
