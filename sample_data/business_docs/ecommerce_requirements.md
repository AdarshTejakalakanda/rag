# E-Commerce Platform Business Requirements Specification

## REQ-001: User Login and Authentication
**Category:** Authentication & Security
**Description:**
Users must be able to securely authenticate to their account using valid email credentials and password.

**Acceptance Criteria:**
- Given a registered user with valid email and password, the system shall grant access to the account dashboard.
- If the user enters an incorrect password, display an error message 'Invalid credentials'.
- Lock the account temporarily after 5 consecutive failed login attempts.

---

## REQ-002: Multi-Factor Authentication (MFA)
**Category:** Authentication & Security
**Description:**
High-privilege or opted-in accounts must complete two-factor authentication via SMS or authenticator OTP before gaining access.

**Acceptance Criteria:**
- Prompt user for a 6-digit one-time password (OTP) after entering password.
- OTP expires after 5 minutes.
- Resend OTP button throttled to once every 60 seconds.

---

## REQ-003: Shopping Cart Checkout and Payment
**Category:** Order & Checkout
**Description:**
Customers can review items in their cart, select shipping options, and complete checkout using Credit Card or PayPal.

**Acceptance Criteria:**
- Cart total must accurately calculate item prices, shipping fees, and taxes.
- Successful payment triggers an order confirmation email and reduces inventory.
- Declined payments display friendly retry instructions without clearing cart items.

---

## REQ-004: Promotional Discount Coupons
**Category:** Promotions & Marketing
**Description:**
Customers can enter promo codes during checkout to receive percentage or fixed amount discounts.

**Acceptance Criteria:**
- Validate coupon expiration date and minimum order threshold.
- Apply discount to order subtotal and show discount line item.
- Prevent stacking invalid or single-use expired coupons.

---

## REQ-005: Automated Return and Refund Processing
**Category:** Customer Support & Returns
**Description:**
Customers can initiate self-service returns within 30 days of delivery, generating a prepaid return label and tracking refund status.

**Acceptance Criteria:**
- Verify return eligibility window (<= 30 days).
- Generate downloadable PDF return shipping label.
- Automatically issue refund to original payment method upon warehouse scan receipt.
