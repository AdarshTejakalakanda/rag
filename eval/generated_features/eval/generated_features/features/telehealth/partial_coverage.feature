@REQ-TELE-101 @telehealth @provisioning
Feature: Partial Telehealth Virtual Room Creation

  Scenario: Basic room provisioning on confirmation
    Given a telehealth appointment "APT-9901" is confirmed
    When the room service executes
    Then a WebRTC room is created for appointment "APT-9901"

  Scenario: Basic token generation for confirmed session
    Given WebRTC room "ROOM-552" exists
    When access tokens are created
    Then single-use links are assigned to patient and provider