@inventory
Feature: Warehouse Inventory Stock Management
  As a warehouse operator
  I want stock quantities updated upon orders
  So that out-of-stock products cannot be oversold

  Scenario: Inventory decrement on shipment
    Given product "SKU-9901" has stock level 50
    When an order for 2 units of "SKU-9901" is confirmed
    Then the stock level of "SKU-9901" should be updated to 48
