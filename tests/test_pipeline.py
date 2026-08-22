"""Tests for end-to-end RAG pipeline and SQLite session management."""

import pytest
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig, JudgeConfig, MilvusConfig, PathsConfig


def test_pipeline_end_to_end_and_sessions(tmp_path):
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

    # Setup directories
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True, exist_ok=True)

    # Write sample doc
    (docs_dir / "sample_req.md").write_text(
        "# Authentication\n\n## REQ-01: Valid Login\nUser must login with email and password.\n\nAcceptance Criteria:\n- Valid email\n- Valid password",
        encoding="utf-8"
    )

    # Write sample feature
    (features_dir / "sample_auth.feature").write_text(
        "Feature: Auth\nScenario: Valid Login\nGiven user exists with email\nWhen user enters valid password\nThen login succeeds",
        encoding="utf-8"
    )

    pipeline = RAGCoveragePipeline(config=config)
    report, report_files, session_id = pipeline.evaluate(
        docs_dir=docs_dir,
        feature_dir=features_dir,
        session_name="Test CI Run",
    )

    assert report.total_requirements == 1
    assert report.total_feature_scenarios == 1
    assert len(report.verdicts) == 1
    assert session_id.startswith(("analysis_", "sess_"))
    assert report_files["markdown"] != ""
    assert report_files["json"] != ""
    assert report_files["html"] != ""

    # Verify session persisted in SQLite
    session = pipeline.state_db.get_session(session_id)
    assert session is not None
    assert session["session_name"] == "Test CI Run"
    assert session["status"] == "COMPLETED"
    assert len(session["evaluations"]) == 1
    assert session["evaluations"][0]["requirement_id"] == "REQ-01"
