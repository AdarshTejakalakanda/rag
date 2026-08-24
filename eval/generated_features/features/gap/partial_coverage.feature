@REQ-GAP-101 @care_gap @telephonic
Feature: Basic Care Gap Outreach Logging

  Scenario: Identify open care gaps without calendar reminder integration
    Given an open care gap is identified for patient "PAT-5001"
    When the system creates the task entry
    Then the task appears on the care manager task list

  Scenario: Record telephonic outreach call outcome without gap status update
    Given an active outreach task for patient "PAT-5001"
    When the worker logs call duration and timestamp
    Then the call record is appended to the patient log