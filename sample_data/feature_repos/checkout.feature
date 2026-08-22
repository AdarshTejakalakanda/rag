@checkout @orders
Feature: Shopping Cart Checkout and Payments
  As an online shopper
  I want to checkout my cart items and pay securely
  So that I receive my ordered goods

  @smoke @payment
  Scenario: Complete checkout with valid Credit Card
    Given the user has 2 items in their shopping cart with total "$49.99"
    When the user proceeds to checkout
    And enters shipping address "123 Market St, San Francisco, CA"
    And provides valid credit card details
    And clicks "Confirm Order"
    Then the payment should be processed successfully
    And an order confirmation email should be dispatched
    And inventory stock for the items should decrease accordingly

  @coupon @discount
  Scenario: Apply valid promotional discount coupon
    Given the user cart subtotal is "$100.00"
    When the user enters promo code "SUMMER20"
    And clicks "Apply Coupon"
    Then the system validates the coupon expiration date
    And applies a 20% discount reducing subtotal to "$80.00"
    And displays discount line item "-$20.00"
