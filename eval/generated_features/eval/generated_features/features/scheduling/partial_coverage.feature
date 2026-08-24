@REQ-SCHED-101 @scheduling @reschedule
Feature: Basic Patient Rescheduling Rules

  Scenario: Patient reschedules standard appointment outside window
    Given patient "P-5011" has an upcoming appointment "APT-302"
    When the patient chooses a new time outside the cutoff window
    Then the appointment time is updated successfully

  Scenario: Immediate release of slot upon reschedule
    Given appointment "APT-302" is successfully rescheduled
    When the transaction commits
    Then the previous appointment slot becomes available in the booking pool