@REQ-AUTH-101 @auth @payer @edi
Feature: Automated Referral Authorization Workflow

  # Covers AC-1, AC-2
  Scenario: Submit automated prior authorization via 278 EDI transaction
    Given a care coordinator creates a specialty referral for patient "P-9921"
    When the system validates all clinical documentation criteria
    Then an EDI 278 request is sent to the electronic payer endpoint
    And the referral status changes to "Payer Pending" within 60 seconds

  # Covers AC-3, AC-5
  Scenario: Process incoming payer approval and update referral record
    Given a referral authorization is in "Payer Pending" state for patient "P-9921"
    When the payer returns an EDI 278 approval payload with reference "AUTH-88231"
    Then authorization reference "AUTH-88231" is attached to the referral record
    And an automated approval notification SMS is dispatched to patient "P-9921"

  # Covers AC-4, AC-5
  Scenario: Route rejected prior authorization request to task queue
    Given a referral authorization is pending payer response for patient "P-4402"
    When the payer returns an EDI 278 denial payload with reason code "AUTH_REJ_09"
    Then the request is routed to the prior auth coordinator task queue within 5 minutes
    And an automated denial notification SMS is dispatched to patient "P-4402"

  # Covers AC-1, AC-6
  Scenario: Retry failed prior authorization on transient EDI network timeout
    Given an automated 278 request encounter experiencing a transient EDI socket timeout
    When the gateway network error triggers the retry policy
    Then the system re-attempts submission up to 3 times before setting error state