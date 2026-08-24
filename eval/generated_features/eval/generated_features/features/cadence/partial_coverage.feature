@REQ-CAD-101 @cadence @scheduling
Feature: Basic Recurring Cadence Management

  Scenario: Scheduler configures bi-weekly recurring appointment cadence without notifications
    Given a scheduler is logged into the scheduling console
    When they set a bi-weekly recurring pattern for patient "PT-99401" across 6 occurrences
    Then 6 appointment slots are reserved on the calendar

  Scenario: Scheduler generates sequence with overlapping provider slot conflicts in basic mode
    Given a provider calendar with an existing appointment on "2023-11-15"
    When a recurring sequence is generated for that provider on "2023-11-15"
    Then the conflict detection modal alerts the user of the overlap

  Scenario: Scheduler updates recurring cadence for future visits without audit logging
    Given an existing recurring sequence with pending future appointments
    When the cadence frequency is modified from weekly to monthly
    Then future uncompleted visits are updated on the calendar