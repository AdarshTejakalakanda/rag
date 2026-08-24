@REQ-MAMP-101 @pharmacy @refills
Feature: Retail Pharmacy Prescription Refill Processing

  Scenario: System processes electronic prescription refills from retail pharmacies
    Given a retail pharmacy sends an electronic refill confirmation for member "M-30044"
    When the system receives the NCPDP transaction
    Then the claim details are recorded in the member history log

  Scenario: Care manager reviews medication reconciliation list during patient intake
    Given a member completes a post-discharge care call
    When the care manager reviews active medications
    Then the reconciliation checklist status updates to Verified