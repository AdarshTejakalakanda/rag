@REQ-PAAR-101 @prior_auth @submission
Feature: Initial Prior Authorization Submission

  Scenario: Provider submits new prior authorization request
    Given a provider accesses the medical management portal
    When they submit an initial prior authorization request for procedure "CPT-27447"
    Then the portal generates a tracking reference number "PA-10099"

  Scenario: Payer requests additional clinical information for initial prior authorization
    Given an initial prior authorization "PA-10099" is in "Under Review" status
    When the payer flags the request for additional clinical details
    Then a notification task is routed to the provider office queue