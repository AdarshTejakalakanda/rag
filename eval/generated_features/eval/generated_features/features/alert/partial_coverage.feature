@REQ-ALERT-101 @alert @partial
Feature: Partial Member Alert Inactivation

  # Covers AC-1
  Scenario: Care coordinator inactivates alert without persistent audit verification
    Given a care coordinator views active member alert "ALT-901"
    When the coordinator clicks inactivate on the dashboard
    Then the alert is removed from the active list
