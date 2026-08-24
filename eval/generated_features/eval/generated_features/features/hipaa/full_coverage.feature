@REQ-HIPAA-101 @hipaa @security @lockout
Feature: HIPAA Identity Verification Failure Session Lockout

  # Covers AC-1
  Scenario: Successful member identity verification grants access to PHI
    Given a care manager requests access to member "M-40291" chart
    When they correctly input DOB "1982-04-12", MRN "MRN-9021", and Zip Code "90210"
    Then the system verifies all 3 identifiers
    And access to member PHI is granted

  # Covers AC-2, AC-3, AC-4
  Scenario: Session lockout and security alert triggered after three consecutive verification failures
    Given a user attempts member verification for chart access
    When the user enters invalid identifiers 3 consecutive times in the current session
    Then the system logs a high-severity security alert in the audit log
    And the user session is immediately locked for a duration of 15 minutes
    And the user interface displays an active countdown timer reflecting the remaining lockout time

  # Covers AC-5
  Scenario: Security administrator manually unlocks locked user session
    Given a user session for coordinator "user_c1" is locked due to identity verification failure
    When a system administrator navigates to the Security Admin Portal and selects "Unlock Session" for user "user_c1"
    Then the session lockout is cleared prior to timer expiration
    And user "user_c1" is permitted to attempt login