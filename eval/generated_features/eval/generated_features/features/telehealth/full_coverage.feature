@REQ-TELE-101 @telehealth @provisioning @security
Feature: Automated Telehealth Virtual Room Provisioning

  # Covers AC-1, AC-2
  Scenario: System provisions room and generates secure tokens
    Given a telehealth appointment is confirmed for patient "P-8831" and provider "DR-402"
    When the automated provisioning service runs for appointment "APT-9901"
    Then a unique WebRTC virtual room is provisioned
    And secure single-use access links are generated for patient "P-8831" and provider "DR-402"

  # Covers AC-3
  Scenario: System dispatches 24 hour pre-appointment notifications
    Given appointment "APT-9901" is scheduled 24 hours in advance
    When the notification dispatcher triggers
    Then an SMS notification containing the secure room link is sent to patient "P-8831"
    And an Email notification containing the secure room link is sent to patient "P-8831"

  # Covers AC-4
  Scenario: Virtual room links expire post appointment window
    Given appointment "APT-9901" ended 61 minutes ago
    When patient "P-8831" attempts to access the virtual room link
    Then the system rejects access with an "Expired Room Session" message

  # Covers AC-5
  Scenario: System logs room creation and token generation events to audit log
    Given virtual room "ROOM-552" is successfully provisioned for appointment "APT-9901"
    When room creation and token generation complete
    Then an audit entry is recorded with event type "ROOM_PROVISIONED", room ID, and UTC timestamp