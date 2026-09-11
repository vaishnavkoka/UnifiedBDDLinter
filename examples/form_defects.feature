Feature: User authentication

  Scenario: Registered user signs in with valid credentials
     Given a registered user exists   
    When the user submits valid credentials
    Then the user reaches their dashboard.



  Scenario: Registered user signs in with valid credentials
    Given a registered user exists
    When the user submits valid credentials
    Then the user reaches their dashboard
