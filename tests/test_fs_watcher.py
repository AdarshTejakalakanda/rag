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
    milvus.clear_repo("watch_repo")
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


def test_live_watchdog_new_file_addition(tmp_path):
    """Tests live watchdog observer reacting to new .feature files written to directory."""
    watch_dir = tmp_path / "live_features"
    watch_dir.mkdir(parents=True, exist_ok=True)

    state_db = StateDatabase(db_path=tmp_path / "live_state.db")
    state_db.register_repo(repo_name="Live Repo", repo_path=watch_dir, repo_id="live_repo")

    bm25 = BM25Index()
    milvus = MilvusStore()
    milvus.clear_repo("live_repo")
    embedding_model = EmbeddingModel(model_name="BAAI/bge-small-en-v1.5", device="cpu")
    # Warm up model so background thread doesn't hit cold-start latency
    embedding_model.encode(["warm up embedding model"])

    watcher = FeatureRepositoryWatcher(
        watch_dir=watch_dir,
        bm25_index=bm25,
        milvus_store=milvus,
        embedding_model=embedding_model,
        state_db=state_db,
        repo_id="live_repo",
        debounce_seconds=0.2,
    )

    try:
        watcher.start(blocking=False)
        time.sleep(0.3)

        # Write a brand new file into the watched folder
        new_file = watch_dir / "payments.feature"
        new_file.write_text("""
Feature: Payments
  Scenario: Successful credit card charge
    Given patient has balance
    When payment is submitted
    Then transaction receipt is generated
""", encoding="utf-8")

        # Wait for trailing debounce to settle and index
        for _ in range(50):
            if len(bm25.scenarios) >= 1:
                break
            time.sleep(0.1)

        assert len(bm25.scenarios) == 1
        assert milvus.count(repo_id="live_repo") == 1
        assert bm25.scenarios[0].scenario_name == "Successful credit card charge"

    finally:
        watcher.stop()


def test_live_watchdog_non_feature_files(tmp_path):
    """Tests live watchdog indexing markdown and text files added to repository."""
    watch_dir = tmp_path / "multi_doc_repo"
    watch_dir.mkdir(parents=True, exist_ok=True)

    state_db = StateDatabase(db_path=tmp_path / "multi_doc.db")
    state_db.register_repo(repo_name="Multi Doc Repo", repo_path=watch_dir, repo_id="multi_doc")

    bm25 = BM25Index()
    milvus = MilvusStore()
    milvus.clear_repo("multi_doc")
    embedding_model = EmbeddingModel(model_name="BAAI/bge-small-en-v1.5", device="cpu")
    embedding_model.encode(["warm up"])

    watcher = FeatureRepositoryWatcher(
        watch_dir=watch_dir,
        bm25_index=bm25,
        milvus_store=milvus,
        embedding_model=embedding_model,
        state_db=state_db,
        repo_id="multi_doc",
        debounce_seconds=0.2,
    )

    try:
        watcher.start(blocking=False)
        time.sleep(0.3)

        # 1. Add a Markdown test specification file
        md_file = watch_dir / "telehealth_spec.md"
        md_file.write_text("""# Telehealth Requirements
## Video Room Provisioning
System automatically creates WebRTC video consultation room upon appointment booking.

## Token Expiration
Consultation link tokens expire 15 minutes following appointment conclusion.
""", encoding="utf-8")

        # 2. Add a Plain Text test case file
        txt_file = watch_dir / "audit_log_cases.txt"
        txt_file.write_text("""Audit logging test cases:
Case 1: Ensure user ID and timestamp are recorded on member alert inactivation.
Case 2: Verify supervisor approval justification is captured.
""", encoding="utf-8")

        # Wait for watchdog to index both files
        for _ in range(50):
            if len(bm25.scenarios) >= 3:
                break
            time.sleep(0.1)

        assert len(bm25.scenarios) >= 3
        assert milvus.count(repo_id="multi_doc") >= 3

        # Verify search retrieves from both .md and .txt files
        results = bm25.search("WebRTC video consultation", top_k=5)
        assert len(results) > 0
        assert any("Video Room Provisioning" in r[0].scenario_name or "telehealth" in r[0].file_path.lower() for r in results)

    finally:
        watcher.stop()


