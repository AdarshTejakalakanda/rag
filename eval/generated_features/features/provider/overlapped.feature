@REQ-PROV-101 @provider @demographics
Feature: Provider Directory Demographics Search

  Scenario: Provider updates office address in directory portal
    Given provider user is logged into the provider portal
    When they submit a new office suite address for location "Site-A"
    Then the directory displays the updated street address
    And no CAQH sync or license verification is executed

  Scenario: Provider NPI number lookup in public directory
    Given a public user accesses the provider directory page
    When they search by NPI number "NPI-199203910"
    Then the public directory displays the practicing location details