"""Tests for Corpus Versioning & Multi-Factor Semantic Cache Invalidation conforming to §8, §20, §21."""

import pytest
from pathlib import Path
from src.storage.state_db import StateDatabase
from src.parsers.gherkin_parser import GherkinParser


def test_corpus_versioning_and_cache_invalidation(tmp_path):
    db_path = tmp_path / "rag_state.db"
    db = StateDatabase(db_path=db_path)

    # 1. Register repo
    repo = db.register_repo("Reach Automation", tmp_path / "reach", repo_id="repo_reach")
    assert repo["repo_id"] == "repo_reach"

    # 2. Parse and save initial scenario
    sc1 = GherkinParser.parse_content(
        "Feature: Login\nScenario: Valid Pass\nGiven user\nWhen login\nThen ok",
        file_path="cypress/features/login.feature",
        repo_id="repo_reach",
    )
    db.save_scenarios(sc1)
    v1 = db.get_corpus_version("repo_reach")
    assert v1 >= 1

    # 3. Store cached judgment with corpus version v1
    db.store_cached_judgment(
        requirement_text="User can login with valid password",
        candidate_ids=[s.scenario_id for s in sc1],
        provider="mock",
        judgment={"overall_classification": "FULLY_COVERED", "match_percentage": 100},
        repo_id="repo_reach",
        corpus_version=v1,
    )

    # 4. Cache hit on same corpus version
    cached = db.get_cached_judgment(
        requirement_text="User can login with valid password",
        candidate_ids=[s.scenario_id for s in sc1],
        provider="mock",
        repo_id="repo_reach",
        corpus_version=v1,
    )
    assert cached is not None
    assert cached["overall_classification"] == "FULLY_COVERED"

    # 5. Modify scenario -> corpus version increments
    sc2 = GherkinParser.parse_content(
        "Feature: Login\nScenario: Valid Pass\nGiven user MFA enabled\nWhen login with OTP\nThen ok",
        file_path="cypress/features/login.feature",
        repo_id="repo_reach",
    )
    db.save_scenarios(sc2)
    v2 = db.increment_corpus_version("repo_reach")
    assert v2 > v1  # Corpus version incremented!

    # 6. Cache is automatically invalidated for new corpus version
    cached_after = db.get_cached_judgment(
        requirement_text="User can login with valid password",
        candidate_ids=[s.scenario_id for s in sc1],
        provider="mock",
        repo_id="repo_reach",
        corpus_version=v2,
    )
    assert cached_after is None
