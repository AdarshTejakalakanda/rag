@REQ-RISK-101 @risk @diagnosis
Feature: Partial Member Risk Recalculation Workflow

  Scenario: Recalculate risk score without historical retention
    Given a member "MEM-88219" has an existing risk score of 1.25
    When a clinical user adds ICD-10 code "E11.9" to the member record
    Then the system triggers an automated risk score recalculation within 25 seconds

  Scenario: Risk score update without logging user provenance
    Given member "MEM-88219" risk score increases from 1.25 to 1.68
    When the risk score update completes
    Then an automated risk stratification alert is logged on the member timeline