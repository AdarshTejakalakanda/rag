@REQ-FAX-101 @fax @inbound @directory
Feature: Inbound Fax Processing and Provider Directory Updates

  Scenario: Inbound fax document processing and chart attachment
    Given an inbound fax arrives from sender number "555-019-2831"
    When the optical character recognition service parses member ID "M-99201"
    Then the document is automatically attached to member "M-99201" medical chart under Inbound Documents

  Scenario: Care team provider directory fax number modification
    Given a provider relations manager is viewing Dr. Sarah Ellis in the directory
    When they update the fax number field to "555-014-9922" and click Save
    Then the provider directory persists the updated fax contact details