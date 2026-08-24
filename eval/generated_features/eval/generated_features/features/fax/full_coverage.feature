@REQ-FAX-101 @fax @outbound @care_team
Feature: Outbound Fax Dispatch to Assigned Care Team Provider

  # Covers AC-1, AC-3, AC-6
  Scenario: Automatic fax dispatch to primary provider with audit logging and HIPAA cover sheet
    Given an outbound clinical summary is queued for member "M-99201"
    And member "M-99201" has an assigned primary care provider with verified fax number "555-019-2831"
    When the dispatch engine processes the outbound transmission
    Then the document is transmitted to fax number "555-019-2831"
    And the generated payload includes a HIPAA cover sheet with redacted member SSN and primary provider NPI "1928374650"
    And a transmission audit record is logged with timestamp, page count, recipient fax number, and status "SUCCESS"

  # Covers AC-2, AC-4
  Scenario: Fax delivery retry logic and fallback to central review queue
    Given an outbound fax transmission to primary care provider fax "555-019-8800" fails on initial attempt
    When the retry engine executes automatic retries
    Then the system attempts delivery up to 3 times spaced 5 minutes apart
    And when all 3 retries fail, the outbound fax is automatically routed to the central unassigned fax review queue

  # Covers AC-5
  Scenario: Manual override selection of alternate care team provider
    Given a care coordinator is preparing an outbound care plan for member "M-99201"
    When the coordinator opens the care team panel and selects alternate provider Dr. Sarah Ellis
    And selects "Dispatch Outbound Fax"
    Then the outbound fax is dispatched directly to Dr. Sarah Ellis's registered fax number