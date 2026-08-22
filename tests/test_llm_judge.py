"""Tests for LLMJudge union scoring and CoverageAggregator."""

from src.parsers.gherkin_parser import ScenarioChunk
from src.parsers.requirement_parser import RequirementChunk
from src.judge.llm_judge import (
    LLMJudge,
    RequirementJudgeVerdict,
    classify_from_percentage,
)
from src.aggregation.aggregator import CoverageAggregator
from src.storage.state_db import StateDatabase
from src.config import JudgeConfig


def _req(**kwargs):
    defaults = dict(
        req_id="REQ-001",
        title="User Login",
        description="Users must login with valid password and receive authentication token.",
        acceptance_criteria=["Valid password login", "Invalid password rejection"],
        source_file="docs/auth.md",
        line_number=5,
    )
    defaults.update(kwargs)
    return RequirementChunk(**defaults)


def _scenario(scenario_id, name, path, gherkin):
    return ScenarioChunk(
        scenario_id=scenario_id,
        repository_id="repo_1",
        file_path=path,
        feature_name="User Authentication",
        scenario_name=name,
        scenario_type="Scenario",
        steps=[],
        canonical_text=gherkin,
        raw_gherkin=gherkin,
    )


def test_classify_does_not_force_100_or_0():
    assert classify_from_percentage(100) == "FULLY_COVERED"
    assert classify_from_percentage(95) == "PARTIALLY_COVERED"
    assert classify_from_percentage(90) == "PARTIALLY_COVERED"
    assert classify_from_percentage(20) == "PARTIALLY_COVERED"
    assert classify_from_percentage(1) == "PARTIALLY_COVERED"
    assert classify_from_percentage(0) == "NOT_COVERED"


def test_union_of_complementary_files_is_fully_covered():
    """Create in file A + reject in file B → union 100; individuals stay 50."""
    judge = LLMJudge(config=JudgeConfig(provider="mock"))
    req = _req(
        acceptance_criteria=["Valid password login", "Invalid password rejection"],
    )
    sc_a = _scenario(
        "s_create",
        "Valid user password login",
        "features/login_happy.feature",
        "Scenario: Valid user password login\nWhen user logs in with valid password",
    )
    sc_b = _scenario(
        "s_reject",
        "Invalid password is rejected",
        "features/login_negative.feature",
        "Scenario: Invalid password is rejected\nWhen user logs in with invalid password",
    )
    data = {
        "evaluations": [
            {
                "scenario_id": "s_create",
                "status": "PARTIALLY_COVERED",
                "match_percentage": 50,
                "reasoning": "Covers happy-path login only",
                "evidence": ["When user logs in with valid password"],
                "covered_criteria": ["Valid password login"],
                "missing_gaps": ["Invalid password rejection"],
            },
            {
                "scenario_id": "s_reject",
                "status": "PARTIALLY_COVERED",
                "match_percentage": 50,
                "reasoning": "Covers negative path only",
                "evidence": ["When user logs in with invalid password"],
                "covered_criteria": ["Invalid password rejection"],
                "missing_gaps": ["Valid password login"],
            },
        ],
        "overall_summary": {
            "union_match_percentage": 100,
            "connecting_narrative": "login_happy.feature covers valid login; login_negative.feature covers rejection.",
            "coverage_map": [
                {"scenario_id": "s_create", "file_path": "features/login_happy.feature", "covers": ["Valid password login"]},
                {"scenario_id": "s_reject", "file_path": "features/login_negative.feature", "covers": ["Invalid password rejection"]},
            ],
            "covered_criteria": ["Valid password login", "Invalid password rejection"],
            "missing_gaps": [],
            "suggested_test_intents": [],
        },
    }
    candidates = [
        (sc_a, 0.9, {"rrf_score": 0.03}),
        (sc_b, 0.8, {"rrf_score": 0.02}),
    ]
    verdict = judge._build_verdict_from_response(req, candidates, data)

    assert verdict.match_percentage == 100
    assert verdict.overall_classification == "FULLY_COVERED"
    assert verdict.citations[0].match_percentage == 50
    assert verdict.citations[1].match_percentage == 50
    assert "Valid password login" in verdict.covered_criteria
    assert "Invalid password rejection" in verdict.covered_criteria
    assert verdict.missing_gaps == []
    assert "login_happy.feature" in verdict.reasoning
    assert len(verdict.coverage_map) == 2


def test_union_does_not_force_partial_to_100_or_0():
    judge = LLMJudge(config=JudgeConfig(provider="mock"))
    req = _req(
        acceptance_criteria=["Create alert", "Edit alert", "Retire alert", "History drawer"],
    )
    sc = _scenario(
        "s_create",
        "Add new alert",
        "features/create.feature",
        "Scenario: Add new alert\nWhen user creates alert",
    )
    data = {
        "evaluations": [
            {
                "scenario_id": "s_create",
                "status": "PARTIALLY_COVERED",
                "match_percentage": 75,
                "reasoning": "Create, edit, retire present; history missing",
                "evidence": ["When user creates alert"],
                "covered_criteria": ["Create alert", "Edit alert", "Retire alert"],
                "missing_gaps": ["History drawer"],
            }
        ],
        "overall_summary": {
            "covered_criteria": ["Create alert", "Edit alert", "Retire alert"],
            "missing_gaps": ["History drawer"],
        },
    }
    verdict = judge._build_verdict_from_response(req, [(sc, 0.9, {"rrf_score": 0.03})], data)
    assert verdict.match_percentage == 75
    assert verdict.overall_classification == "PARTIALLY_COVERED"
    assert verdict.citations[0].match_percentage == 75

    low = {
        "evaluations": [
            {
                "scenario_id": "s_create",
                "status": "PARTIALLY_COVERED",
                "match_percentage": 25,
                "reasoning": "Only create",
                "evidence": [],
                "covered_criteria": ["Create alert"],
                "missing_gaps": ["Edit alert", "Retire alert", "History drawer"],
            }
        ],
        "overall_summary": {"covered_criteria": ["Create alert"]},
    }
    low_verdict = judge._build_verdict_from_response(req, [(sc, 0.4, {"rrf_score": 0.01})], low)
    assert low_verdict.match_percentage == 25
    assert low_verdict.overall_classification == "PARTIALLY_COVERED"


def test_batch_llm_judge_and_caching(tmp_path):
    state_db = StateDatabase(db_path=tmp_path / "test_state.db")
    config = JudgeConfig(provider="mock")
    judge = LLMJudge(config=config, state_db=state_db)

    req = _req()
    scenario = _scenario(
        "s1",
        "Valid user password login",
        "cypress/features/auth.feature",
        "Feature: User Authentication\nScenario: Valid user password login\nGiven user exists\nWhen user logs in with valid password\nThen authentication token is granted",
    )
    candidates = [(scenario, 0.85, {"rrf_score": 0.03})]

    v1 = judge.judge_requirement(req, candidates, repo_id="repo_1")
    assert v1.overall_classification in ("FULLY_COVERED", "PARTIALLY_COVERED")
    assert v1.match_percentage >= 50
    assert v1.cached is False
    assert v1.citations[0].match_percentage >= 0

    v2 = judge.judge_requirement(req, candidates, repo_id="repo_1")
    assert v2.overall_classification == v1.overall_classification
    assert v2.match_percentage == v1.match_percentage
    assert v2.cached is True


def test_aggregation():
    v1 = RequirementJudgeVerdict(
        req_id="REQ-1", title="R1", category="Auth", source_file="auth.md", line_number=1,
        match_percentage=100, overall_classification="FULLY_COVERED", reasoning="Valid",
        primary_citation=None, citations=[]
    )
    v2 = RequirementJudgeVerdict(
        req_id="REQ-2", title="R2", category="Orders", source_file="orders.md", line_number=1,
        match_percentage=0, overall_classification="NOT_COVERED", reasoning="Irrelevant",
        primary_citation=None, citations=[]
    )

    report = CoverageAggregator.aggregate([v1, v2], total_scenarios_count=5)
    assert report.total_requirements == 2
    assert report.covered_count == 1
    assert report.uncovered_count == 1
    assert report.coverage_rate == 50.0
    assert report.average_match_pct == 50.0
