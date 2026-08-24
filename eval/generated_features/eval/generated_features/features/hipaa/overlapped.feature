@REQ-HIPAA-101 @hipaa @password @audit
Feature: Password Expiration and Compliant Access Logging

  Scenario: User password expiration lockout and reset flow
    Given a user password has exceeded the 90-day compliance limit
    When the user attempts to sign into the system
    Then the login system blocks access and prompts for password reset

  Scenario: PHI access log auditing for compliant sessions
    Given a user successfully accesses a member medical chart
    When the user views the clinical timeline
    Then an automated audit entry records user ID and chart access timestamp