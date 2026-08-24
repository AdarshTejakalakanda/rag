@REQ-CAL-101 @calendar @caldav @synchronization
Feature: Multi-provider calendar synchronization via CalDAV protocols

  # Covers AC-1, AC-2, AC-3, AC-5
  Scenario: Sync provider schedule with external CalDAV server
    Given provider "PRV-8821" has linked an external CalDAV account
    When the automated 15-minute sync cycle executes
    Then the system retrieves external calendar entries via CalDAV
    And conflicting appointments are flagged as "Conflict Pending Review" without overwriting EHR appointments
    And an audit log entry is recorded with provider ID "PRV-8821", timestamp, and status

  # Covers AC-4
  Scenario: Secure credential storage for CalDAV integration
    Given a provider submits external CalDAV server authentication credentials
    When the credentials are saved to the platform
    Then the credentials are encrypted at rest using AES-256 encryption

  # Covers AC-1, AC-2, AC-5
  Scenario: Manual trigger sync for multi-provider CalDAV integration
    Given a clinic coordinator views the scheduling dashboard
    When they select "Sync Now" for provider "PRV-8821"
    Then a manual sync cycle executes immediately using CalDAV protocols
    And the sync result status is recorded in the system audit trail

  # Covers AC-6
  Scenario: Handle network timeout failure during CalDAV synchronization
    Given provider "PRV-9012" external CalDAV server is unreachable
    When the sync engine encounters a network timeout
    Then the sync operation fails
    And a system notification is sent to the clinic administrator