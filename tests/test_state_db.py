"""Tests for SQLite Relational Schema and Integrity."""

import pytest
from pathlib import Path
from src.storage.state_db import StateDatabase
from src.parsers.gherkin_parser import ScenarioChunk
from src.parsers.requirement_parser import RequirementChunk


def test_sqlite_relational_schema_and_foreign_keys(tmp_path):
    db_path = tmp_path / "test_rag.db"
    db = StateDatabase(db_path=db_path)

    # 1. Register repository
    repo = db.register_repo(repo_name="Reach Automation", repo_path=tmp_path / "reach", repo_id="repo_reach")
    assert repo["repo_id"] == "repo_reach"
    assert repo["corpus_version"] == 1

    # 2. Register document
    doc_path = tmp_path / "brd.md"
    doc_path.write_text("# BRD\nREQ-01: User Login\nPassword login", encoding="utf-8")
    doc = db.register_document(doc_path)
    assert doc["document_id"] != ""

    # 3. Update feature file
    feat_path = tmp_path / "reach" / "auth.feature"
    ff_id = db.update_feature_file(
        repo_id="repo_reach",
        file_path=feat_path,
        file_hash="hash123",
        scenario_count=2,
        last_modified="1234567.0",
    )
    assert ff_id.startswith("ff_")

    # 4. Save scenarios & test hydration
    sc = ScenarioChunk(
        scenario_id="sc_001",
        repository_id="repo_reach",
        file_path=str(feat_path),
        line_number=10,
        feature_name="Authentication",
        scenario_name="Valid user password login",
        scenario_type="Scenario",
        tags=["@Auth", "@Smoke"],
        canonical_text="Feature: Auth\nScenario: Valid login\nGiven user",
        raw_gherkin="Feature: Auth\nScenario: Valid login\nGiven user\nWhen login\nThen ok",
        content_hash="chash001",
    )
    db.save_scenarios([sc])

    # Hydrate single
    hydrated = db.get_scenario_by_id("sc_001")
    assert hydrated is not None
    assert hydrated.scenario_name == "Valid user password login"
    assert hydrated.raw_gherkin.startswith("Feature: Auth")
    assert hydrated.line_number == 10

    # Bulk hydrate
    bulk_hydrated = db.get_scenarios_by_ids(["sc_001", "non_existent"])
    assert "sc_001" in bulk_hydrated
    assert len(bulk_hydrated) == 1

    # 5. Create Analysis Session & Requirements & Evaluations
    analysis_id = db.create_analysis_session(
        repo_id="repo_reach",
        document_id=doc["document_id"],
        corpus_version=1,
        session_name="CI Run 1",
    )
    assert analysis_id.startswith("analysis_")

    req = RequirementChunk(
        req_id="REQ-001",
        title="User Password Login",
        description="Users must login with password",
        acceptance_criteria=["Valid password"],
        business_rules=["5 attempts limit"],
        category="Auth",
        source_file=str(doc_path),
        line_number=2,
        full_text="User must login with password",
    )
    db.save_requirements(analysis_id=analysis_id, requirements=[req])

    # Save evaluation
    db.save_evaluations(
        analysis_id=analysis_id,
        verdict={
            "req_id": "REQ-001",
            "overall_classification": "FULLY_COVERED",
            "match_percentage": 100.0,
            "reasoning": "Scenario sc_001 covers password login",
            "primary_citation": {"scenario_id": "sc_001"},
            "citations": [],
            "covered_criteria": ["Valid password"],
            "missing_gaps": [],
            "suggested_tests": [],
        },
        model_version="mock",
    )

    # 6. Verify full session query with requirements & evaluations
    session = db.get_analysis_session(analysis_id)
    assert session is not None
    assert len(session["requirements"]) == 1
    assert len(session["evaluations"]) == 1
    assert session["evaluations"][0]["match_percentage"] == 100.0
    assert session["evaluations"][0]["scenario_id"] == "sc_001"

    # 7. Chat Sessions & Messages
    chat_id = db.create_chat_session(repo_id="repo_reach", title="Test Chat", analysis_id=analysis_id)
    db.add_chat_message(chat_id=chat_id, role="user", content="How is login tested?")
    db.add_chat_message(chat_id=chat_id, role="assistant", content="Login is tested in sc_001", citations=[{"scenario_id": "sc_001"}])

    history = db.get_chat_history(chat_id)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[1]["citations"][0]["scenario_id"] == "sc_001"
