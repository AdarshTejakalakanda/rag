@REQ-TELE-101 @telehealth @bandwidth @metrics
Feature: Telehealth Session Quality and Bandwidth Management

  Scenario: Provider manually overrides virtual room bandwidth settings
    Given provider "DR-402" is in active session "ROOM-552"
    When the provider selects bandwidth mode "Low Bitrate Video"
    Then the WebRTC connection adjusts target video bitrate to 300kbps

  Scenario: System records telehealth video stream quality metrics
    Given an active video session in room "ROOM-552"
    When packet loss exceeds 5 percent
    Then the system logs a stream degradation telemetry alert