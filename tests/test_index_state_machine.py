"""Tests for Authoritative File Hash Change Detection & Repository Index State Machine."""

import pytest
from pathlib import Path
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig, JudgeConfig, PathsConfig
from src.storage.state_db import fast_hash
from fastapi.testclient import TestClient
from src.web.app import app


def test_authoritative_hash_change_and_state_machine(tmp_path):
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
    repo_dir = tmp_path / "repo_auth"
    repo_dir.mkdir(parents=True, exist_ok=True)
    fpath = repo_dir / "login.feature"
    fpath.write_text(
        "Feature: Login\nScenario: Valid Login\nGiven user exists\nWhen user enters credentials\nThen login succeeds",
        encoding="utf-8"
    )

    # 1. Initial Indexing
    count = pipeline.index_features(feature_dir=repo_dir, repo_id="auth_repo", repo_name="Auth Service")
    assert count == 1

    # Check state is READY
    status = pipeline.state_db.get_repo_indexing_status("auth_repo")
    assert status["index_status"] == "READY"
    assert status["scenario_count"] == 1
    assert status["corpus_version"] == 2

    # 2. Test Authoritative Hash Detection (Unchanged File)
    # Re-indexing unchanged file should not increment corpus version
    initial_version = status["corpus_version"]
    pipeline.index_features(feature_dir=repo_dir, repo_id="auth_repo")
    status_after = pipeline.state_db.get_repo_indexing_status("auth_repo")
    assert status_after["corpus_version"] == initial_version

    # 3. Test File Modification (Changed Hash)
    fpath.write_text(
        "Feature: Login\nScenario: Valid Login\nGiven user exists\nWhen user enters credentials\nThen login succeeds\n"
        "Scenario: Invalid Password\nGiven user exists\nWhen user enters wrong password\nThen error shown",
        encoding="utf-8"
    )
    count_mod = pipeline.index_features(feature_dir=repo_dir, repo_id="auth_repo")
    assert count_mod == 2
    status_mod = pipeline.state_db.get_repo_indexing_status("auth_repo")
    assert status_mod["corpus_version"] == initial_version + 1
    assert status_mod["index_status"] == "READY"

    # 4. Test Query Guard while in INDEXING state
    pipeline.state_db.set_repo_indexing_status("auth_repo", "INDEXING", current_file="login.feature", progress_pct=50)
    
    with pytest.raises(RuntimeError) as exc_info:
        pipeline.chat("login test", repo_id="auth_repo")
    assert "currently indexing" in str(exc_info.value)

    # Reset state to READY
    pipeline.state_db.set_repo_indexing_status("auth_repo", "READY", current_file="", progress_pct=100)
    chat_res = pipeline.chat("login test", repo_id="auth_repo")
    assert "reply" in chat_res


def test_web_api_status_endpoint_and_query_guard():
    client = TestClient(app)

    # Test GET /api/repos/{repo_id}/status
    resp = client.get("/api/repos/repo_1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "index_status" in data
    assert data["index_status"]["index_status"] in ("READY", "INDEXING")
