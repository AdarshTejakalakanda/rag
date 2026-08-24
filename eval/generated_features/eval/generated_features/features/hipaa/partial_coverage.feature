@REQ-HIPAA-101 @hipaa @security
Feature: Identity Verification Partial Flow

  Scenario: Session locked without countdown timer upon verification failure
    Given a user is attempting to access a patient record
    When the user fails identity verification 3 consecutive times
    Then the system logs a security alert entry
    And the session state changes to Locked for 15 minutes