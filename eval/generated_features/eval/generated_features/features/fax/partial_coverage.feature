@REQ-FAX-101 @fax @outbound
Feature: Outbound Fax Basic Dispatch Workflow

  Scenario: Standard outbound fax delivery to primary provider without audit record
    Given an outbound clinical document is ready for member "M-99201"
    And the primary care provider fax is "555-019-2831"
    When the automated fax engine dispatches the document
    Then the document is transmitted to "555-019-2831"
    And the document includes a HIPAA cover sheet with redacted SSN

  Scenario: Manual selection of alternate care team provider without cover sheet redaction
    Given a care coordinator is viewing member "M-99201" care team panel
    When the coordinator selects alternate provider Dr. Alex Vance and initiates fax dispatch
    Then the fax is queued for transmission to Dr. Alex Vance's fax destination