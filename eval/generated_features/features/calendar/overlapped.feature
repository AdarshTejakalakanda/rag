@REQ-CAL-101 @calendar @export
Feature: Static Calendar Export and Roster Import

  Scenario: Provider exports static iCal feed for personal schedule
    Given provider "PRV-8821" views their EHR schedule settings
    When they click "Export Static iCal URL"
    Then the system generates a read-only iCal link for personal calendars

  Scenario: Import legacy CSV appointment roster
    Given a clinic admin has a legacy appointment file "roster.csv"
    When they upload the file to the administrative portal
    Then static appointments are created in the EHR database