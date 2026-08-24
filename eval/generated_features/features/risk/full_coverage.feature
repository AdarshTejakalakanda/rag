@REQ-RISK-101 @risk @hcc @diagnosis
Feature: Member Risk Score Recalculation on Diagnosis Code Update

  # Covers AC-1, AC-2, AC-6
  Scenario: Recalculate HCC risk score upon ICD-10 code addition
    Given a member "MEM-88219" has an existing risk score of 1.25
    When a clinical user adds ICD-10 code "E11.9" to the member record
    Then the system triggers an automated HCC risk score recalculation
    And the recalculation completes within 20 seconds with new score 1.68
    And the recalculation log records trigger source user, previous score 1.25, updated score 1.68, and effective date

  # Covers AC-3
  Scenario: Risk score increase triggers stratification alert on member timeline
    Given member "MEM-88219" transitions from risk category "Moderate" to "High"
    When the recalculated score 1.68 is finalized
    Then an automated risk stratification alert is logged on the member timeline

  # Covers AC-4, AC-6
  Scenario: Audit historical risk scores without overwriting previous calculations
    Given member "MEM-88219" has historical score 1.25 calculated on "2023-01-15"
    When the new risk score 1.68 is saved
    Then the historical score 1.25 remains unchanged in the risk history database
    And the audit entry links the calculation timestamp and source API details

  # Covers AC-5
  Scenario: Reject risk recalculation for invalid or deprecated ICD-10 code
    Given a clinical user attempts to add deprecated ICD-10 code "INVALID-999"
    When the ingestion engine validates the diagnosis entry
    Then the recalculation request is rejected
    And an ingestion queue error flag is created for data remediation

  # Covers AC-1, AC-2
  Scenario: Recalculate risk score upon ICD-10 code deletion
    Given member "MEM-88219" has ICD-10 code "E11.9" removed from their active conditions
    When the diagnosis deletion event is saved
    Then the risk score is recalculated within 15 seconds
    And the resulting score decreases to 1.25