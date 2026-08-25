@REQ-HBA1C-101 @hba1c @caregap @outreach
Feature: Clinical HbA1c Lab Gap Closure and Outreach Interlocking

  # Covers AC-1, AC-2, AC-3
  Scenario: Ingest LOINC HbA1c lab result and close diabetes care gap
    Given an incoming HL7 feed containing LOINC "4548-4" HbA1c result of 6.8% for member "M-201"
    When the clinical data pipeline processes the lab result
    Then the diabetes care gap status for member "M-201" is updated to "Closed"
    And automated outreach tasks for diabetes gap are placed on hold
    And a confirmation note is appended to the member care timeline

  # Covers AC-4, AC-5
  Scenario: Route high HbA1c lab result to clinical escalation queue
    Given an incoming lab feed with LOINC "4548-4" HbA1c result of 9.5% for member "M-202"
    When the clinical ingestion rules evaluate the high value
    Then the member is routed to the high-risk clinical escalation queue
    And the outreach hold is bypassed for immediate intervention
