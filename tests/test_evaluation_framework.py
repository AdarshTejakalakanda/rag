"""Tests for Decoupled Evaluation Framework (Retrieval vs. Judge) & Benchmark Runner."""

import pytest
import json
from pathlib import Path
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig, JudgeConfig, PathsConfig
from eval.retrieval.evaluate_retrieval import RetrievalEvaluator
from eval.judge.evaluate_judge import JudgeEvaluator
from eval.benchmarks.run_all_benchmarks import BenchmarkRunner


def test_retrieval_and_judge_evaluators_with_benchmark_runner(tmp_path):
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
    repo_dir = tmp_path / "features"
    repo_dir.mkdir(parents=True, exist_ok=True)
    
    # Create sample feature files matching gold.json
    (repo_dir / "auth.feature").write_text(
        "Feature: User Authentication and Login\n"
        "Scenario: Successful user login with valid credentials\n"
        "Given registered user exists\nWhen user enters credentials\nThen dashboard displayed\n\n"
        "Scenario: Failed login with invalid password\n"
        "Given user exists\nWhen user enters wrong password\nThen error shown\n\n"
        "Scenario: Account lockout after 5 consecutive failed attempts\n"
        "Given user exists\nWhen 5 failures occur\nThen lockout occurs\n\n"
        "Scenario: Prompt for MFA OTP code on login\n"
        "Given user with MFA\nWhen login attempted\nThen OTP prompted\n",
        encoding="utf-8"
    )
    (repo_dir / "checkout.feature").write_text(
        "Feature: Shopping Cart Checkout and Payments\n"
        "Scenario: Complete checkout with valid Credit Card\n"
        "Given items in cart\nWhen checkout completed\nThen payment succeeds\n\n"
        "Scenario: Apply valid promotional discount coupon\n"
        "Given items in cart\nWhen promo applied\nThen discount deducted\n",
        encoding="utf-8"
    )

    pipeline.index_features(feature_dir=repo_dir, repo_id="default")

    # Hermetic test datasets for unit test
    test_gold = tmp_path / "test_gold.json"
    test_gold.write_text(
        json.dumps([
            {
                "requirement_id": "REQ-1",
                "query": "User authentication with email and password",
                "gold_scenario_names": ["Successful user login with valid credentials"],
                "gold_files": ["auth.feature"],
            },
            {
                "requirement_id": "REQ-2",
                "query": "Checkout payment with valid credit card",
                "gold_scenario_names": ["Complete checkout with valid Credit Card"],
                "gold_files": ["checkout.feature"],
            },
        ]),
        encoding="utf-8",
    )

    test_labels = tmp_path / "test_labels.json"
    test_labels.write_text(
        json.dumps([
            {
                "case_id": "TEST-1",
                "requirement_id": "REQ-1",
                "requirement": {
                    "id": "REQ-1",
                    "title": "User Login",
                    "description": "User login authentication",
                    "acceptance_criteria": ["User enters credentials and logs in"],
                },
                "candidate_scenarios": [
                    {
                        "scenario_name": "Successful user login with valid credentials",
                        "file_path": "auth.feature",
                        "raw_gherkin": "Scenario: Successful user login with valid credentials\nWhen user logs in\nThen user authenticated",
                    }
                ],
                "gold_status": "COVERED",
                "gold_match_percentage": 100.0,
                "expected_gaps": [],
            }
        ]),
        encoding="utf-8",
    )

    # 1. Test Retrieval Evaluator
    r_eval = RetrievalEvaluator(retriever=pipeline.retriever, gold_dataset_path=str(test_gold))
    r_metrics = r_eval.evaluate(repo_id="default")
    assert r_metrics["total_queries"] == 2
    assert "recall_at_5" in r_metrics
    assert "recall_at_10" in r_metrics
    assert "mrr" in r_metrics
    assert "ndcg_at_10" in r_metrics
    assert r_metrics["recall_at_10"] >= 0.5

    # 2. Test Judge Evaluator
    j_eval = JudgeEvaluator(judge=pipeline.judge, gold_dataset_path=str(test_labels))
    j_metrics = j_eval.evaluate(bypass_cache=True)
    assert j_metrics["total_cases"] == 1
    assert "accuracy" in j_metrics
    assert "macro_f1" in j_metrics
    assert "confusion_matrix" in j_metrics
    assert "COVERED" in j_metrics["per_class"]

    # 3. Test Benchmark Runner
    results_dir = tmp_path / "results"
    runner = BenchmarkRunner(pipeline=pipeline, results_dir=results_dir)
    bench_results = runner.run(target="all", repo_id="default", bypass_cache=True)

    assert "config_snapshot" in bench_results
    assert "retrieval_metrics" in bench_results
    assert "judge_metrics" in bench_results

    saved_jsons = list(results_dir.glob("*_benchmark.json"))
    saved_mds = list(results_dir.glob("*_benchmark.md"))
    assert len(saved_jsons) == 1
    assert len(saved_mds) == 1

    with open(saved_jsons[0], "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["target"] == "all"
        assert "retrieval_metrics" in data
        assert "judge_metrics" in data
