@QOM0026 @QOM @Demographics @AlertsOverlap
Feature: [QOM0026] - Member Demographics Right Panel and Alert State Consistency

  Background:
    Given User "priority_roster_tester" login to "QOM" Portal with role "Quality Outreach Manager"
    And User navigates to member profile "Donald Draper"

  Scenario: Validate persistent alert indicators while editing member demographic details
    Given Member "Donald Draper" has active manual alert "Member request - Hold Outreach"
    When User clicks on "Edit Demographics" in member left panel
    Then Verify "Demographics Right Panel" is displayed
    And Verify member left panel still displays alert "Member request - Hold Outreach" in "orange" background
    When User updates phone number to "760-005-8907" and clicks "Save"
    Then Verify update success notification is displayed
    And Verify member summary details and active alerts remain synchronized on Left Panel