# [REQ-SCHED-101] Patient Self-Rescheduling Rules with Dynamic Cancellation Windows

## Overview
The Patient Portal scheduling engine allows patients to modify existing appointment times self-service while enforcing specialty-based dynamic cancellation cutoff windows, fee warnings, slot release procedures, and usage limits.

## Objectives
- Empower patients to self-reschedule appointments without calling administrative staff.
- Protect clinic provider utilization using specialty-based dynamic cancellation windows.
- Instantly recycle cancelled booking slots into the open scheduling pool.
- Prevent system abuse by enforcing per-appointment reschedule limits.

## Scope
Covers patient self-service portal rescheduling workflows, specialty window validation rules, late reschedule penalty warnings, real-time slot releases, self-reschedule frequency limits, and confirmation messaging.

## Acceptance Criteria
1. Patients can self-reschedule appointments via the portal up to a dynamic cancellation window defined by specialty (e.g. 24 hours for General Care, 48 hours for Specialty Care).
2. Self-rescheduling within the restricted window triggers a late cancellation warning and requires penalty fee acknowledgement.
3. Upon successful self-rescheduling, the original calendar slot is immediately released to the available booking pool.
4. System must enforce a maximum limit of 3 self-reschedules per appointment instance before requiring staff assistance.
5. Rescheduling confirmation notifications must be delivered via preferred communication channel (SMS/Email).

## Out of Scope
- Payment gateway processing for late cancellation fee collection.
- Transportation service re-booking integrations.

## Glossary
- Dynamic cancellation window: Time threshold before an appointment during which rescheduling rules change based on clinic specialty.
- Booking pool: Available appointment time slots published for patient booking.