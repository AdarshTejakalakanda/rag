@REQ-MAMP-101 @pharmacy @adherence
Feature: Partial Medication Adherence Workflow

  Scenario: Nightly PDC calculation and non-adherence flagging
    Given the nightly adherence job evaluates active diabetes medications
    When member "M-30044" is calculated to have a PDC of 65%
    Then member "M-30044" is marked as non-adherent for dropping below 80% PDC

  Scenario: Manual outreach logging without trigger suppression
    Given a care coordinator contacts member "M-30044"
    When the coordinator logs outreach outcome "Spoke to Member" and barrier "Forgetfulness"
    Then the note is saved to the member intervention record