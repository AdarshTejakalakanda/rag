@REQ-PAAR-101 @appeal @prior_auth @compliance
Feature: Prior Authorization Denial Appeal Tracking and Resolution

  # Covers AC-1, AC-2
  Scenario: Care manager logs prior authorization denial details
    Given a prior authorization request "PA-99201" for member "M-88301" is denied by the payer
    When the care manager inputs denial code "DEN-404", denial date "2023-10-15", and clinical rationale "Lack of conservative therapy evidence"
    Then the system logs the denial record in the member PA history
    And the system presents an option to initiate a 1st-level or 2nd-level appeal with pre-populated context

  # Covers AC-3, AC-4
  Scenario: Care manager initiates a 1st-level appeal workflow
    Given an active prior authorization denial exists for member "M-88301"
    When the care manager initiates a "1st-level" appeal with filing deadline "2023-11-15"
    And the clinical staff attaches supporting document "Physical_Therapy_Notes.pdf" to the appeal file
    Then the platform schedules an automated alert for the care manager set for "2023-11-10"
    And the attached medical documentation is linked directly to the active appeal

  # Covers AC-5, AC-6
  Scenario: Care manager resolves appeal with outcome decision and notifies provider
    Given an active appeal file "AP-7001" is under review
    When the user records the outcome decision as "Overturned" with resolution timestamp "2023-10-20T14:30:00Z"
    Then the appeal status updates to "Resolved - Overturned"
    And an automated notification email is generated and sent to prescribing provider "Dr. Sarah Lin"