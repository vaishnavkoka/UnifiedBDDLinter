Feature: Windows line endings

  Scenario: CRLF must not become a violation on every line
    Given a file with CRLF endings
    When the linter reads it
    Then line endings alone produce no findings
