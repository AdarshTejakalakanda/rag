@REQ-HIPAA-101 @hipaa @security @partial
Feature: HIPAA Verification Failure Partial Handling

  # Covers AC-1, AC-3
  Scenario: Session locked without countdown timer upon verification failure
    Given a user is attempting to access a patient record
    When the user fails 3 consecutive identity verification attempts
    Then the user session is immediately locked out from the portal
