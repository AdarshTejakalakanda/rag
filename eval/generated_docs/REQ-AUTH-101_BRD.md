# [REQ-AUTH-101] Automated Referral Authorization and Payer Approval Workflow

## Overview
The Automated Referral Authorization and Payer Approval Workflow module streamlines the prior authorization process for specialty care referrals within the Reach healthcare platform. By integrating directly with payer electronic data interchange (EDI) endpoints, the platform reduces administrative burden and manual data entry errors.

## Objectives
- Automate submission of HIPAA EDI 278 prior authorization transactions.
- Enable real-time status tracking for pending authorizations.
- Direct rejected or flagged authorization requests to clinical coordinators for manual intervention.
- Ensure patient transparency through automated multi-channel notifications.

## Scope
- Integration with payer EDI 278 transactions.
- Internal task queuing and workflow routing for prior authorization coordinators.
- Automated SMS communication triggered by status change events.
- Retries for transient network/EDI connection timeouts.

## Acceptance Criteria
1. System automatically submits prior authorization requests to electronic payer EDI endpoints using 278 transactions.
2. Pending requests must reflect an updated status of Payer Pending within 60 seconds of submission.
3. Received payer approvals must automatically attach the authorization reference number to the referral record.
4. Rejected requests require routing to the prior auth coordinator task queue within 5 minutes.
5. Patient notifications are triggered via SMS upon approval or denial of referral authorization.
6. System must support automatic authorization retry up to 3 times for transient EDI timeout errors.

## Out of Scope
- Direct integration with non-standard legacy fax gateways.
- Real-time phone verification with payer call centers.

## Glossary
- **EDI 278**: Health Care Services Review - Request for Review and Response standard transaction set.
- **Prior Authorization**: Requirement by a health plan for pre-approval of health services.