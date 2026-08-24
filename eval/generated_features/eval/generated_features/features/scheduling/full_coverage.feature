@REQ-SCHED-101 @scheduling @reschedule @rules
Feature: Patient Self-Rescheduling Workflow

  # Covers AC-1, AC-3, AC-5
  Scenario: Patient self-reschedules outside restricted window
    Given patient "P-5011" has a "Specialty Care" appointment "APT-302" 72 hours in advance
    When the patient selects a new slot on "2025-06-15 at 10:00 AM"
    Then the appointment "APT-302" is updated to the new date and time
    And the original slot on "2025-06-12 at 02:00 PM" is immediately released to the available booking pool
    And a confirmation message is sent via patient preferred channel

  # Covers AC-2
  Scenario: Patient attempts self-reschedule within dynamic restricted window
    Given patient "P-5011" has a "Specialty Care" appointment "APT-302" scheduled in 24 hours
    When the patient attempts to self-reschedule via the portal
    Then the system displays a late cancellation fee warning
    And requires explicit penalty fee acknowledgement before allowing slot selection

  # Covers AC-3, AC-4
  Scenario: Patient reaches maximum self-reschedule limit
    Given patient "P-5011" has already rescheduled appointment "APT-302" 3 times
    When the patient attempts to reschedule appointment "APT-302" a 4th time
    Then the system blocks the online reschedule request
    And directs the patient to contact staff assistance