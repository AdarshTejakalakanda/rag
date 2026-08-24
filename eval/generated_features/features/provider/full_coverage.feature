@REQ-PROV-101 @provider @credentialing @caqh
Feature: Provider Network Credentialing and Enrollment Status Sync

  # Covers AC-1, AC-4
  Scenario: Synchronize CAQH provider credentialing changes on scheduled 24-hour cycle
    Given the CAQH data bridge is connected
    When the scheduled 24-hour sync batch executes for provider "NPI-199203910"
    Then the system updates the credentialing record in the provider directory
    And the integration audit ledger logs status "SUCCESS", current timestamp, and payload ID "PL-88102"

  # Covers AC-2
  Scenario: Suspend provider enrollment upon license expiration
    Given provider "NPI-199203910" has a state medical license expiring on "2023-10-31"
    When the sync engine detects the expired license status
    Then the provider network enrollment status changes to "Suspended-Pending-Review"

  # Covers AC-3, AC-4
  Scenario: Approve provider credentialing upon primary source verification completion
    Given Primary Source Verification (PSV) is marked complete for provider "NPI-199203910"
    When the status update is processed by the platform
    Then the credentialing status updates to "Approved"
    And an automated confirmation email is sent to the registered provider portal user email
    And the transaction timestamp and payload ID are written to the audit ledger

  # Covers AC-5
  Scenario: Retry failed synchronization attempt and escalate to IT operational ticket
    Given a network timeout occurs during the CAQH synchronization batch
    When the sync attempt fails on initial execution
    Then the system schedules an automated retry 15 minutes later
    And after 3 failed retries, an IT operational ticket is automatically generated