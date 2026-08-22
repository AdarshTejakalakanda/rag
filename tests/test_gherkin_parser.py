"""Tests for GherkinParser."""

import pytest
from src.parsers.gherkin_parser import GherkinParser, ScenarioChunk

SAMPLE_FEATURE_TEXT = """
@auth @security
Feature: User Login Feature
  As a customer
  I want to log in

  Background:
    Given authentication service is up
    And database is healthy

  @smoke
  Scenario: Valid Login
    Given user has valid email "test@example.com"
    When user enters password "Secret123"
    Then login is successful

  Scenario Outline: Invalid Login Attempts
    Given user enters email "<email>"
    When user enters password "<password>"
    Then error "<error_msg>" is displayed

    Examples:
      | email | password | error_msg |
      | bad@ex.com | 123 | Invalid |
      | test@ex.com | wrong | Invalid |
"""


def test_parse_gherkin_scenarios():
    scenarios = GherkinParser.parse_content(SAMPLE_FEATURE_TEXT, file_path="features/test.feature")
    assert len(scenarios) == 2

    # Scenario 1
    s1 = scenarios[0]
    assert s1.feature_title == "User Login Feature"
    assert s1.scenario_name == "Valid Login"
    assert s1.scenario_type == "Scenario"
    assert "@auth" in s1.tags and "@smoke" in s1.tags
    assert len(s1.background_steps) == 2
    assert len(s1.steps) == 3
    assert s1.file_path == "features/test.feature"

    # Scenario 2 (Outline)
    s2 = scenarios[1]
    assert s2.scenario_name == "Invalid Login Attempts"
    assert s2.scenario_type == "Scenario Outline"
    assert len(s2.examples) == 3  # Header + 2 rows
    assert "Examples:" in s2.full_text
