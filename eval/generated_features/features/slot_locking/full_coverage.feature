@REQ-LOCK-101 @scheduling @slot_locking @realtime
Feature: Real-time slot reservation locking to prevent double-booking

  # Covers AC-1, AC-2
  Scenario: Patient initiates appointment booking and acquires temporary slot lock
    Given patient "PT-5011" selects an available appointment slot at "10:00 AM"
    When the booking workflow starts
    Then the system places a 10-minute temporary reservation lock on the slot
    And all other connected client interfaces show the slot status as "Held" in real time via WebSockets

  # Covers AC-3
  Scenario: Temporary slot lock expires automatically after timeout
    Given a 10-minute temporary reservation lock exists for slot "10:00 AM"
    When 10 minutes elapse without booking completion
    Then the reservation lock automatically expires
    And the slot status returns to "Available" on the schedule

  # Covers AC-2, AC-4
  Scenario: Concurrent users attempt simultaneous slot selection
    Given two patients select slot "02:00 PM" at the exact same millisecond
    When the backend receives both lock requests
    Then the lock is granted to the first request processed by the server
    And the second request receives a conflict error message
    And the slot status is broadcast as "Held" via WebSockets

  # Covers AC-1, AC-5
  Scenario: Patient completes booking conversion from temporary lock to confirmed state
    Given patient "PT-5011" holds a temporary reservation lock for slot "10:00 AM"
    When they submit payment and confirm the booking
    Then the temporary reservation lock converts to a permanent booked appointment