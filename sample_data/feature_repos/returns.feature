Feature: Product Return and Refund Management
  Scenario: Return undamaged product within 30 days for full refund
    Given customer purchased an item 15 days ago
    And customer provides a valid purchase receipt
    When customer initiates a product return request
    Then a full refund is processed to the original payment method within 3-5 business days