Feature: Shopping Cart Checkout   

    Scenario: test the checkout thing properly   
        Given the user is on the checkout page
        When the user clicks the pay button
        When the user clicks confirm
        Then it should work correctly.



    Scenario: test the checkout thing properly
        Given a user with foo in the cart
        When the user clicks the pay button
        Then the page should not fail
