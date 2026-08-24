@REQ-MAMP-101 @pharmacy @adherence @pdc @outreach
Feature: Medication Adherence Monitoring and Pharmacy Outreach Trigger

  # Covers AC-1, AC-2
  Scenario: Nightly job calculates PDC metrics and flags non-adherent members
    Given the batch job "PDC-Calculation-Engine" runs at midnight
    When pharmacy claim histories are evaluated for active statin prescriptions
    Then the system computes member "M-30044" has a PDC metric of 72%
    And member "M-30044" is flagged as non-adherent because the PDC is below the 80% threshold

  # Covers AC-3
  Scenario: System triggers pharmacy outreach task for non-adherent member
    Given member "M-30044" is flagged as non-adherent
    When the adherence monitor executes its post-calculation workflow
    Then an automated pharmacy outreach task "TASK-PHARM-881" is created in the pharmacy team queue

  # Covers AC-4, AC-5
  Scenario: Pharmacist documents outreach intervention and suppresses secondary triggers
    Given pharmacy outreach task "TASK-PHARM-881" is assigned to clinician "P-4011"
    When clinician "P-4011" logs outreach attempt "Completed", member barrier "Financial Copay", and resolution "Generic Substitution Approved"
    Then the intervention log updates with the recorded details and timestamp
    And the system suppresses any new adherence outreach triggers for member "M-30044" for 30 days