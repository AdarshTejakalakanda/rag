@REQ-AUTH-101 @auth @payer
Feature: Partial Prior Authorization Workflow

  Scenario: Submit automated prior authorization without status latency check
    Given a care coordinator creates a referral for member "M-3001"
    When system transmits the 278 transaction payload to the clearinghouse
    Then the request transaction is logged successfully

  Scenario: Process payer authorization approval without sending SMS notification
    Given a pending referral for member "M-3001"
    When approval payload with code "AUTH-7712" is received
    Then the referral status is updated with authorization reference "AUTH-7712"