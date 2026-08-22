@QOM0031 @QOM @F1793489 @Alerts
Feature: [QOM0031] - Create and Display Manual Member Alerts in Left Panel

  Background:
    Given User "priority_roster_tester" login to "QOM" Portal with role "Quality Outreach Manager"
    And User searches and navigates to member profile "Donald Draper"

  Scenario Outline: Add new manual member alert and verify orange badge on Left Panel
    When User clicks on "Edit Alerts" icon in member left panel
    Then Verify "Manage Member Alerts" right panel drawer is displayed
    When User selects alert category "<AlertName>"
    And User enters start date "Today" and notes "<NoteText>"
    And User clicks on "Save" button in right panel
    Then User verifies alert "<AlertName>" is visible on the Left Panel with "orange" background
    And User hovers over alert info icon and verifies tooltip contains start date and notes "<NoteText>"

    Examples:
      | AlertName                       | NoteText                              |
      | Out of Country                  | Traveling outside US until next month |
      | Member request - Hold Outreach  | Member requested pause on phone calls |
      | Hospitalized                    | Admitted for inpatient observation    |