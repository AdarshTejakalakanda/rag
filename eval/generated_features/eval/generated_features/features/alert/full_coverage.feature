@REQ-ALERT-101 @alert @inactivation @audit
Feature: Reach Manual Member Alert Inactivation

  # Covers AC-1, AC-2, AC-3
  Scenario: Care coordinator manually inactivates a single member alert
    Given a care coordinator is logged into the Reach portal dashboard
    And an active alert 