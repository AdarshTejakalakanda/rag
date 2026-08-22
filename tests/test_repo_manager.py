"""Tests for Multi-Repository Manager and Repo-Scoped Retrieval."""

import pytest
from pathlib import Path
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig, JudgeConfig, PathsConfig


def test_multi_repo_isolation(tmp_path):
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

    # Create Repo A (Auth)
    repo_a_dir = tmp_path / "repo_auth"
    repo_a_dir.mkdir(parents=True, exist_ok=True)
    (repo_a_dir / "auth.feature").write_text(
        "Feature: Authentication\nScenario: User Password Login\nGiven user has account\nWhen user enters password\nThen login succeeds",
        encoding="utf-8"
    )

    # Create Repo B (Billing)
    repo_b_dir = tmp_path / "repo_billing"
    repo_b_dir.mkdir(parents=True, exist_ok=True)
    (repo_b_dir / "billing.feature").write_text(
        "Feature: Billing\nScenario: Stripe Payment Charge\nGiven user cart total is 100\nWhen credit card is charged\nThen receipt is generated",
        encoding="utf-8"
    )

    # Index both repos
    pipeline.index_features(feature_dir=repo_a_dir, repo_id="auth_repo", repo_name="Auth Service")
    pipeline.index_features(feature_dir=repo_b_dir, repo_id="billing_repo", repo_name="Billing Service")

    # Verify registered repos
    repos = pipeline.repo_manager.list_repositories()
    assert len(repos) == 2
    repo_ids = [r["repo_id"] for r in repos]
    assert "auth_repo" in repo_ids
    assert "billing_repo" in repo_ids

    # Test Multi-Folder addition for auth_repo
    repo_a_folder2 = tmp_path / "repo_auth_oauth"
    repo_a_folder2.mkdir(parents=True, exist_ok=True)
    (repo_a_folder2 / "oauth.feature").write_text(
        "Feature: OAuth2\nScenario: Google OAuth SSO\nGiven user clicks Google Login\nWhen token returned\nThen session created",
        encoding="utf-8"
    )
    pipeline.repo_manager.add_folder_to_repo(repo_id="auth_repo", folder_path=repo_a_folder2)
    folders = pipeline.repo_manager.list_folders_for_repo("auth_repo")
    assert len(folders) >= 2

    # Index all folders for auth_repo
    count = pipeline.index_repo_folders("auth_repo")
    assert count == 2

    # Query scoped strictly to auth_repo
    results_auth = pipeline.retriever.retrieve("Google OAuth SSO", repo_id="auth_repo")
    assert len(results_auth) >= 1
    assert any(sc.scenario_name == "Google OAuth SSO" for sc, _, _ in results_auth)

    # Query scoped strictly to billing_repo
    results_billing = pipeline.retriever.retrieve("Stripe payment charge", repo_id="billing_repo")
    assert len(results_billing) >= 1
    assert all(sc.repo_id == "billing_repo" for sc, _, _ in results_billing)
