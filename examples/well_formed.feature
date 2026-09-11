Feature: Well Formed

Scenario: Customer adds an item to an empty cart
  Given the customer has an empty cart
  When the customer adds a book to the cart
  Then the cart contains one book
