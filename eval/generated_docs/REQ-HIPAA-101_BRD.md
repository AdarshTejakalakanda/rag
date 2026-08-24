# [REQ-HIPAA-101] HIPAA Identity Verification Failure Session Lockout

## Overview
To protect Protected Health Information (PHI) against unauthorized access and brute-force identity theft attempt, the platform mandates strict member identity verification prior to granting clinical chart visibility. Consecutive failed verification attempts trigger security logging and automated user session lockout.

## Objectives
- Prevent unauthorized access to member PHI through multi-factor identifier validation.
- Enforce automatic 15-minute session lockout upon repeated verification failures.
- Ensure complete security alert logging and administrative override capabilities for locked user accounts.

## Scope
Covers user-facing identifier verification screens, failed attempt counting logic, automated session lockout timer, security alert generation, and administrator lockout overrides.

## Acceptance Criteria
1. System shall require successful verification of 3 member identifiers (DOB, MRN, Zip Code) prior to granting access to PHI.
2. System shall log a security alert after 3 consecutive identity verification failures for a single user session.
3. Upon 3 consecutive identity verification failures, system shall immediately lock the user session for 15 minutes.
4. Locked user sessions shall display a countdown timer indicating remaining lock duration.
5. System administrators can manually unlock locked sessions prior to timer expiration via the Security Admin Portal.

## Out of Scope
- Password resetting or multi-factor authentication (MFA) SMS passcode generation.
- Role-based access control (RBAC) permission changes outside of session locking.

## Glossary
- **PHI**: Protected Health Information as governed by HIPAA rules.
- **MRN**: Medical Record Number.
- **Session Lockout**: Temporary suspension of active user session state preventing any portal interaction.