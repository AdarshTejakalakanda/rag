"""Tests for Gherkin AST Parsing, Canonical Text & Domain Preservation conforming to §5, §6, §7."""

import pytest
from src.parsers.gherkin_parser import GherkinParser, ScenarioChunk


def test_canonical_text_and_domain_preservation():
    gherkin_content = """@PA @Regression @MVN
Feature: Authorization Cancellation PA012
  Member verbal notification for EDI 278 transactions.

  Background:
    Given user has logged in to Member 360 portal

  Scenario Outline: Auth Canceled - Member Verbal Notification Not Triggered
    Given an authorization exists with CDO code "PA012"
    When authorization is canceled with reason "Out of Country"
    Then member notification status should be "<status>"
    And UI label "HICN Verified" should display

    Examples:
      | status     |
      | Suppressed |
      | Canceled   |
"""
    scenarios = GherkinParser.parse_content(gherkin_content, file_path="cypress/features/PA/PA012.feature", repo_id="repo_1")
    assert len(scenarios) == 1
    sc = scenarios[0]

    # Verify schema fields (§6)
    assert sc.repository_id == "repo_1"
    assert sc.file_path == "cypress/features/PA/PA012.feature"
    assert sc.feature_name == "Authorization Cancellation PA012"
    assert sc.scenario_name == "Auth Canceled - Member Verbal Notification Not Triggered"
    assert "@PA" in sc.tags
    assert "@MVN" in sc.tags

    # Verify canonical text preserves exact domain codes (§7)
    canonical = sc.canonical_text
    assert "PA012" in canonical
    assert "EDI 278" in canonical
    assert "Member 360" in canonical
    assert "CDO" in canonical
    assert "Out of Country" in canonical
    assert "HICN Verified" in canonical

    # Verify raw Gherkin is preserved intact for evidence citations (§7, §19)
    assert "Scenario Outline: Auth Canceled - Member Verbal Notification Not Triggered" in sc.raw_gherkin
    assert "| Suppressed |" in sc.raw_gherkin
