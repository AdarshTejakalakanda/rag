@QOM0001 @QOM @Regression
Feature: [QOM0001] - Member Search Page and Left Panel Navigation

  Background:
    Given User "priority_roster_tester" login to "QOM" Portal with role "Quality Outreach Manager"
    And User clicks on "Member Search" icon from left navigation

  Scenario: Verify member search columns and navigate to Member 360
    Then User validates if below columns are visible on the screen
      | Curo ID       |
      | Member        |
      | Status        |
      | Date of Birth |
      | Phone         |
      | Subscriber ID |
      | Health Plan   |
    When User searches for member "Donald Draper" on member search page
    And User expands member "Donald Draper" details on member search page
    Then Validate user navigates to "Measures Dashboard" page
    And Verify page name is displayed as "Member 360"
    And Then validate member summary in member panel in left side
      | HICN             |
      | Campaign Type    |
      | CDO              |
      | CDO Subgroup     |
      | PN Campaign Name |