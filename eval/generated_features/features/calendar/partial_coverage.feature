@REQ-CAL-101 @calendar @caldav
Feature: Partial CalDAV Calendar Synchronization

  Scenario: Automated 15 minute CalDAV synchronization
    Given provider "PRV-8821" has an active CalDAV integration
    When the 15-minute automated timer elapses
    Then external appointments are pulled using CalDAV protocols

  Scenario: CalDAV sync audit logging
    Given a provider calendar sync operation completes
    When the result is processed
    Then an entry with provider ID and timestamp is added to the audit trail