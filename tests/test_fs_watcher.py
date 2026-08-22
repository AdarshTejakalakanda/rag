"""Unit tests for FeatureRepositoryWatcher and real-time filesystem synchronization."""

import time
from pathlib import Path
from src.watcher.fs_watcher import FeatureRepositoryWatcher
from src.index.bm25_index import BM25Index
from src.index.milvus_store import MilvusStore
from src.index.embedding_model import EmbeddingModel
from src.storage.state_db import StateDatabase


def test_fs_watcher_create_modify_delete(tmp_path):
    repo_dir = tmp_path / "features"
    repo_dir.mkdir(parents=True, exist_ok=True)

    state_db = StateDatabase(db_path=tmp_path / "test_state.db")
    state_db.register_repo(repo_name="Watch Repo", repo_path=repo_dir, repo_id="watch_repo")

    bm25 = BM25Index()
    milvus = MilvusStore()
    embedding_model = EmbeddingModel(model_name="BAAI/bge-small-en-v1.5", device="cpu")

    watcher = FeatureRepositoryWatcher(
        watch_dir=repo_dir,
        bm25_index=bm25,
        milvus_store=milvus,
        embedding_model=embedding_model,
        state_db=state_db,
        repo_id="watch_repo",
        debounce_seconds=0.1,
    )

    # 1. Create a new .feature file
    f1 = repo_dir / "login.feature"
    f1.write_text("""
Feature: User Login
  Scenario: Valid password authentication
    Given user has credentials
    When user logs in
    Then dashboard is displayed
""", encoding="utf-8")

    watcher.handle_file_change(str(f1), "created")

    # Verify indexed in BM25, Milvus, and SQLite
    assert len(bm25.scenarios) == 1
    assert milvus.count(repo_id="watch_repo") == 1
    repo_meta = state_db.get_repo("watch_repo")
    assert repo_meta["corpus_version"] >= 2
    scenarios_db = state_db.get_repo_scenarios("watch_repo")
    assert len(scenarios_db) == 1
    assert scenarios_db[0].scenario_name == "Valid password authentication"

    # 2. Modify the .feature file (add a second scenario)
    f1.write_text("""
Feature: User Login
  Scenario: Valid password authentication
    Given user has credentials
    When user logs in
    Then dashboard is displayed

  Scenario: Invalid password failure
    Given user has bad password
    When user logs in
    Then error message is shown
""", encoding="utf-8")

    watcher.handle_file_change(str(f1), "modified")

    # Verify updated
    assert len(bm25.scenarios) == 2
    assert milvus.count(repo_id="watch_repo") == 2
    scenarios_db2 = state_db.get_repo_scenarios("watch_repo")
    assert len(scenarios_db2) == 2
    repo_meta2 = state_db.get_repo("watch_repo")
    assert repo_meta2["corpus_version"] >= 3

    # 3. Delete the .feature file
    f1.unlink()
    watcher.handle_file_change(str(f1), "deleted")

    assert len(bm25.scenarios) == 0
    assert milvus.count(repo_id="watch_repo") == 0
