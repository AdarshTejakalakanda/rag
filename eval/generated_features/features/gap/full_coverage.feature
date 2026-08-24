@REQ-GAP-101 @care_gap @telephonic @outreach
Feature: Care Gap Telephonic Outreach and Task Management

  # Covers AC-1
  Scenario: Identify open care gaps and assign telephonic outreach task
    Given an open HbA1c screening gap is identified for patient "PAT-1092"
    When the clinical rules engine processes the gap criteria
    Then a telephonic outreach task is queued for the assigned care manager

  # Covers AC-2, AC-3
  Scenario: Log telephonic outreach attempt details
    Given a care manager completes a telephonic outreach call for patient "PAT-1092"
    When they log call outcome "Completed", duration "12 minutes", and timestamp
    Then the outreach history drawer records the call details
    And the care gap status updates to "Closed-Pending-Verification"

  # Covers AC-2, AC-4
  Scenario: Reassign task after 3 failed outreach attempts
    Given patient "PAT-8812" has 2 prior logged unsuccessful telephonic attempts
    When the care manager logs a 3rd unsuccessful call outcome
    Then the telephonic call attempt details are recorded
    And the outreach task is automatically reassigned to a community care worker

  # Covers AC-5
  Scenario: Schedule follow-up outreach call and generate calendar reminder
    Given a care manager schedules a follow-up outreach call for patient "PAT-1092" in 3 days
    When the follow-up schedule is saved
    Then a reminder task is generated on the assigned worker calendar exactly 24 hours prior to the call