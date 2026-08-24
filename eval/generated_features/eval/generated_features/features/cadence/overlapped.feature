@REQ-CAD-101 @appointments @cancellation
Feature: Single Appointment Cancellation and Provider Availability

  Scenario: Patient requests single appointment cancellation via self-service portal
    Given patient "PT-99401" views their upcoming appointments list
    When they click "Cancel Appointment" for a single scheduled visit
    Then the visit status updates to "Cancelled"
    And the provider schedule frees the slot

  Scenario: System checks provider availability for ad-hoc consultation
    Given a care coordinator requires an urgent ad-hoc consultation slot
    When they query provider availability for today
    Then the system returns available 15-minute open slots