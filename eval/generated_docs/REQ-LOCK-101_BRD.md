# [REQ-LOCK-101] Real-time slot reservation locking to prevent double-booking

## Overview
This requirement specifies the implementation of real-time reservation locks on patient scheduling slots to eliminate concurrent booking race conditions and prevent double-booking across patient portals and staff scheduling interfaces.

## Objectives
- Implement atomic temporary locking mechanisms on appointment slots upon selection.
- Broadcast real-time slot state changes to active web clients.
- Provide smooth expiration dynamics and race condition mitigation.

## Scope
- Temporary slot locking for 10 minutes.
- Real-time updates via WebSocket.
- Automatic lock release upon timeout.
- First-come-first-served race condition resolution.
- Transition from temporary lock to permanent booking.

## Acceptance Criteria
1. System must place a temporary 10-minute reservation lock on an appointment time slot as soon as a patient begins booking.
2. Locked appointment slots must display as "Held" to other concurrent users in real time via WebSocket updates.
3. If the booking workflow is not completed within 10 minutes, the lock must automatically expire and return the slot to "Available" status.
4. Simultaneous attempts to select the same slot must grant the lock to the first request received by the server and display a conflict message to subsequent users.
5. Upon successful booking confirmation, the reservation lock must convert into a permanent booked appointment state.

## Out of Scope
- Overbooking authorization workflows for clinic management staff.
- Extended hold times beyond 10 minutes.

## Glossary
- Slot Lock: A transient mutex mechanism preventing multiple users from holding the same calendar interval simultaneously.
- WebSocket: Full-duplex communication channel for real-time UI state synchronization.