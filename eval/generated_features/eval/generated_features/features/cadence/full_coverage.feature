@REQ-CAD-101 @cadence @scheduling @appointments
Feature: Recurring Appointment Sequence Cadence Configurations

  # Covers AC-1
  Scenario: Scheduler configures weekly recurring appointment cadence
    Given a scheduler is authenticated in the care scheduling portal
    When they configure a recurring sequence for patient "PT-99401" with weekly frequency for 12 weeks
    Then the system generates 12 appointment instances scheduled 7 days apart
    And all instances appear in the provider daily schedule view

  # Covers AC-2
  Scenario: Scheduler attempts to create sequence exceeding maximum occurrence limit
    Given a scheduler is creating a recurring appointment sequence for patient "PT-99401"
    When they enter 53 occurrences for a weekly cadence
    Then the system displays an error message "Maximum occurrence limit of 52 reached"
    And the sequence generation is blocked

  # Covers AC-3
  Scenario: Scheduler generates sequence with overlapping provider slot conflicts
    Given provider "DR-SMITH" has an existing appointment on "2023-11-15 10:00 AM"
    When the scheduler generates a bi-weekly sequence for patient "PT-99401" overlapping on "2023-11-15 10:00 AM"
    Then the system flags the slot on "2023-11-15 10:00 AM" as a schedule conflict
    And prompts the scheduler to select an alternate time slot before saving

  # Covers AC-1, AC-4, AC-6
  Scenario: Scheduler updates recurring cadence for future uncompleted visits
    Given an active 24-week sequence for patient "PT-99401" with 18 uncompleted visits remaining
    When the scheduler changes the cadence from weekly to bi-weekly starting from visit 7
    Then all 18 future uncompleted visits are rescheduled to bi-weekly intervals
    And an audit log is created recording user ID "SCHED-881", timestamp, and previous weekly parameters

  # Covers AC-5
  Scenario: System dispatches notification reminders prior to scheduled instance
    Given a recurring appointment instance is scheduled for patient "PT-99401" on "2023-11-20 09:00 AM"
    When the system time reaches 48 hours prior to the appointment instance
    Then an automated SMS and email reminder is dispatched to patient "PT-99401"