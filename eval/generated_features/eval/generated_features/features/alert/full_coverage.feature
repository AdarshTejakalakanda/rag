@REQ-ALERT-101 @alert @inactivation @audit
Feature: Reach Manual Member Alert Inactivation

  # Covers AC-1, AC-2, AC-3
  Scenario: Care coordinator manually inactivates a single member alert
    Given a care coordinator is logged into the Reach portal dashboard
    And an active alert exists for member "M-1002"
    When the care coordinator selects "Inactivate" with reason "Resolved by Phone"
    Then the alert is removed from the active alert queue
    And the alert is moved to the archived alerts tab
    And an audit log entry is recorded with user ID, timestamp, and reason "Resolved by Phone"

  # Covers AC-4
  Scenario: Supervisor approves reactivation of an inactivated member alert
    Given an archived alert exists for member "M-1002"
    When a supervisor enters a reactivation justification and confirms approval
    Then the alert status transitions back to active in the member dashboard

  # Covers AC-5
  Scenario: Inactivating an alert suspends pending automated SMS notifications
    Given a pending automated SMS message is scheduled for member alert "ALT-881"
    When the care coordinator inactivates alert "ALT-881"
    Then pending automated SMS notifications for alert "ALT-881" are suspended

  # Covers AC-1, AC-6
  Scenario: Care coordinator performs bulk alert inactivation via CSV upload
    Given a valid CSV file containing 250 active alert IDs
    When the care coordinator uploads the CSV to the bulk alert menu
    Then all 250 alerts transition to inactive status simultaneously
    And bulk inactivation audit entries are recorded
