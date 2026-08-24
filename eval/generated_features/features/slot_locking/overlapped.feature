@REQ-LOCK-101 @scheduling @cancellation
Feature: Appointment Cancellation and Rescheduling

  Scenario: Patient cancels an existing confirmed appointment slot
    Given patient "PT-5011" has a confirmed appointment at "10:00 AM"
    When they click "Cancel Appointment" in the portal
    Then the appointment is cancelled and notification is sent

  Scenario: Administrator reschedules patient to another provider
    Given an existing appointment for patient "PT-5011"
    When the admin selects a new provider and time
    Then the existing appointment is transferred without locking