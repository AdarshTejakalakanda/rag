@REQ-AUTH-101 @auth @manual @peer
Feature: Manual Prior Authorization and Peer-to-Peer Workflow

  Scenario: Manual prior authorization entry by clinic administrative staff
    Given an administrative user is logged into the clinic portal
    When they enter fax approval details for member "M-9912" manually
    Then the referral note updates to reflect manual fax clearance

  Scenario: Provider peer-to-peer consultation scheduling for authorization appeal
    Given an authorization request has been formally denied by payer
    When the attending physician requests a peer-to-peer appeal session
    Then a consultation calendar event is scheduled with the medical director