Feature: File saved with a byte order mark

  Scenario: The BOM must not be read as indentation
    Given a file written by a Windows editor
    When the linter reads it
    Then no spurious indentation violation appears
