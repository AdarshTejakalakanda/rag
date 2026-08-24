@REQ-OOO-101 @ooo @reassignment
Feature: Provider Absence Blockouts Partial Flow

  Scenario: Provider sets single-day blockout without re-assignment notifications
    Given provider "DR-JONES" enters an out-of-office blockout for "2023-12-01"
    When the system scans for schedule overlaps
    Then 3 impacted patient appointments are identified
    And re-assigned to on-call provider "DR-WILSON"