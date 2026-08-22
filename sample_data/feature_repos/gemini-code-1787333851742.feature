@QOM0032 @QOM @F1793489 @Alerts
Feature: [QOM0032] - Edit and Retire Manual Member Alerts

  Background:
    Given User "priority_roster_tester" login to "QOM" Portal with role "Quality Outreach Manager"
    And User navigates to member profile "Donald Draper" with active alert "Hospitalized"

  Scenario: Modify active alert end date from Right Panel
    When User clicks on "Edit Alerts" icon in member left panel
    And User toggles to "User" alerts tab in "Manage Member Alerts" panel
    And User updates end date to "Today" and notes "Discharged from care" for alert "Hospitalized"
    And User clicks on "Save" button
    Then User verifies alert "Hospitalized" is removed from Left Panel active view

  Scenario: Inactivate alert and verify audit trail in History Drawer
    When User clicks on "History" icon in "Manage Member Alerts" panel
    Then Verify "History Drawer" opens showing inactive alerts
    And User verifies the top row contains alert "Hospitalized"
    And Verify inactive alert displays last updated by "priority_roster_tester" with current timestamp