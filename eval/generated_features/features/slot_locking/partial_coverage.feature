@REQ-LOCK-101 @scheduling @slot_locking
Feature: Basic Slot Hold Functionality

  Scenario: Patient acquires temporary slot lock on selection
    Given a patient selects an available appointment slot
    When they enter the booking checkout
    Then a 10-minute temporary lock is applied to the time slot

  Scenario: Slot lock expires on inactivity
    Given an unconfirmed temporary lock on a slot
    When the timer reaches 10 minutes
    Then the lock expires and slot returns to "Available" status