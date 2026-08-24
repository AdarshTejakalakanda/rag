@REQ-RISK-101 @risk @manual_adjustment
Feature: Manual Member Risk Note Entry

  Scenario: Care manager logs manual risk observation note
    Given care manager selects member "MEM-88219"
    When they submit a qualitative risk narrative note "Patient shows signs of frailty"
    Then the note is saved to the clinical timeline
    And no automated risk recalculation or ICD-10 validation is executed

  Scenario: Annual risk model version migration schedule view
    Given an administrator views the CMS-HCC model configuration panel
    When they inspect the upcoming year risk weight tables
    Then the platform displays the version release schedule