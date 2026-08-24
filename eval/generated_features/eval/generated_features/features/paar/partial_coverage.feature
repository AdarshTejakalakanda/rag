@REQ-PAAR-101 @appeal @prior_auth
Feature: Partial Prior Authorization Appeal Handling

  Scenario: Care manager logs denial and initiates basic appeal
    Given a prior authorization request "PA-99201" for member "M-88301" is marked denied
    When the user logs denial code "DEN-404", date "2023-10-15", and rationale "Experimental treatment"
    Then the denial details are stored in the PA record
    And the system allows initiating a 1st-level appeal pre-filled with member demographics