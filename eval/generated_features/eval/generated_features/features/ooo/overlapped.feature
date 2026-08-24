@REQ-OOO-101 @shifts @emergency
Feature: Provider Shift Schedule Management and Emergency Intake

  Scenario: Provider updates ongoing clinical shift schedule
    Given provider "DR-JONES" is logged into the shift management module
    When they swap their weekend shift with provider "DR-ADAMS"
    Then the monthly shift roster is updated

  Scenario: On-call provider accepts direct patient transfer from emergency intake
    Given provider "DR-WILSON" is marked as active on-call
    When emergency intake transfers an urgent patient
    Then provider "DR-WILSON" receives a direct push notification