"""Tests for conversational RAG Chat Engine."""

import pytest
from pathlib import Path
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig, JudgeConfig, PathsConfig


def test_rag_chat_engine(tmp_path):
    config = AppConfig(
        paths=PathsConfig(
            business_docs_dir=tmp_path / "docs",
            feature_repos_dir=tmp_path / "features",
            reports_dir=tmp_path / "reports",
            milvus_db_path=tmp_path / "milvus.db",
            cache_dir=tmp_path / "cache",
        ),
        judge=JudgeConfig(provider="mock"),
    )

    pipeline = RAGCoveragePipeline(config=config)

    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / "checkout.feature").write_text(
        "Feature: Checkout\nScenario: Apply 10% Discount Code\nGiven cart has items\nWhen discount code SAVE10 applied\nThen total decreases by 10 percent",
        encoding="utf-8"
    )

    pipeline.index_features(feature_dir=features_dir, repo_id="checkout_repo")

    # Chat with chatbot
    res = pipeline.chat("How are promotional discount coupons verified?", repo_id="checkout_repo")

    assert "reply" in res
    assert "citations" in res
    assert res["repo_id"] == "checkout_repo"
    assert len(res["citations"]) >= 1
    assert res["citations"][0]["scenario_name"] == "Apply 10% Discount Code"

    # Verify conversation history in SQLite
    history = pipeline.state_db.get_chat_history(res["chat_id"])
    assert len(history) == 2  # user + assistant
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
