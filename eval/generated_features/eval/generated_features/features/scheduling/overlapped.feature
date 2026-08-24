@REQ-SCHED-101 @scheduling @admin @working_hours
Feature: Provider Working Hours and Recurring Appointment Management

  Scenario: Clinic administrator configures provider working hours and holiday schedule
    Given clinic admin "ADM-10" opens provider "DR-402" schedule settings
    When the admin adds a holiday block for "2025-07-04"
    Then no patient appointments can be booked with "DR-402" on that date

  Scenario: System processes automated recurring appointment creation for chronic care
    Given member "P-5011" is enrolled in a 12-week Diabetes Management program
    When the automated recurring scheduler runs
    Then recurring weekly slots are reserved for member "P-5011"