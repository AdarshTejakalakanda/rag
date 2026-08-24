@REQ-OOO-101 @ooo @reassignment @provider
Feature: Provider Out-Of-Office Blockouts and Automated Re-Assignment

  # Covers AC-1, AC-2, AC-3, AC-5, AC-6
  Scenario: Provider sets multi-day out-of-office blockout
    Given provider "DR-JONES" (Cardiology) schedules an OOO blockout from "2023-12-01" to "2023-12-05"
    When the blockout request is submitted
    Then the system identifies 8 patient appointments impacted during that timeframe
    And automatically re-assigns all 8 appointments to available Cardiology on-call provider "DR-WILSON"
    And sends an SMS notification to all 8 patients within 15 minutes detailing the provider update
    And records the blockout and re-assignment events in the system audit log

  # Covers AC-2, AC-4, AC-6
  Scenario: System routes impacted appointments to unassigned pending queue when no on-call provider is available
    Given provider "DR-LEE" (Neurology) enters an OOO blockout for "2023-12-10"
    And no Cardiology or Neurology on-call providers are available on "2023-12-10"
    When the system processes the 4 impacted patient appointments
    Then all 4 appointments are routed to the unassigned pending queue
    And an audit log records the blockout creation and queue routing event