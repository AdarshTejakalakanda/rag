@REQ-PROV-101 @provider @caqh
Feature: Partial Provider Credentialing Sync Workflow

  Scenario: Scheduled CAQH provider sync without ledger logging
    Given the CAQH data bridge triggers an update
    When the scheduled 24-hour sync batch executes for provider "NPI-199203910"
    Then the system updates the credentialing record in the provider directory

  Scenario: License expiration changes enrollment status to suspended
    Given provider "NPI-199203910" has an expired medical license
    When the sync engine runs
    Then the provider network enrollment status changes to "Suspended-Pending-Review"