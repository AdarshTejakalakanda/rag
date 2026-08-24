@REQ-GAP-101 @care_gap @sms @lab
Feature: Automated Care Gap Messaging and Lab Ingestion

  Scenario: Automated SMS care gap reminder dispatch
    Given a patient with an open mammogram care gap
    When the automated batch messaging job executes
    Then an automated SMS reminder is sent to the patient mobile phone

  Scenario: Clinical lab data import for automated care gap satisfaction
    Given an inbound HL7 lab result message for member "PAT-1092"
    When the lab result indicates an HbA1c value below target threshold
    Then the care gap is marked satisfied by laboratory data match