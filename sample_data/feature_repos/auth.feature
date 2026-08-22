@auth @security
Feature: User Authentication and Login
  As a registered customer
  I want to log in securely to my account
  So that I can view my profile and orders

  Background:
    Given the authentication service is online
    And the user database is accessible

  @smoke @happy_path
  Scenario: Successful user login with valid credentials
    Given a registered user exists with email "alice@example.com" and password "SecurePass123!"
    When the user navigates to the login page
    And enters email "alice@example.com" and password "SecurePass123!"
    And clicks the "Login" button
    Then the user should be redirected to the account dashboard
    And a welcome greeting "Welcome, Alice!" should be displayed

  @negative @validation
  Scenario: Failed login with invalid password
    Given a registered user exists with email "alice@example.com" and password "SecurePass123!"
    When the user navigates to the login page
    And enters email "alice@example.com" and password "WrongPassword"
    And clicks the "Login" button
    Then an error message "Invalid credentials" should be displayed
    And the user should remain on the login page

  @security @rate_limiting
  Scenario: Account lockout after 5 consecutive failed attempts
    Given a registered user exists with email "bob@example.com"
    When the user fails login 5 times consecutively
    Then the user account should be locked temporarily for 15 minutes
    And an alert email should be sent to "bob@example.com"

  @mfa
  Scenario: Prompt for MFA OTP code on login
    Given a user "carol@example.com" has two-factor authentication enabled
    When the user enters valid username and password
    Then the system should display the "Enter 6-digit OTP" screen
    And an SMS with a 6-digit verification code should be dispatched
