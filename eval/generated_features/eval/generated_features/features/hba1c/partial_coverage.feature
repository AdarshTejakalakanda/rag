@REQ-HBA1C-101 @hba1c @caregap
Feature: Partial HbA1c Lab Ingestion Workflow

  Scenario: Process low HbA1c result to close care gap without CRM sync
    Given an incoming lab feed with LOINC 