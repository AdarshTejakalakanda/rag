# [REQ-FAX-101] Outbound Fax Dispatch to Assigned Care Team Provider

## Overview
The Outbound Fax Dispatch engine facilitates secure, automated transmission of clinical care plans, transition of care summaries, and authorization requests directly to the verified fax numbers of assigned primary care providers (PCPs) within the care team panel.

## Objectives
- Automate clinical document routing to reduce care coordinator manual effort.
- Ensure full compliance with HIPAA privacy standards by dynamically appending redacted cover sheets.
- Maintain absolute auditability for all outbound fax transmission attempts, failures, and rerouting actions.

## Scope
This business requirement covers outbound fax generation, primary provider routing, automatic retry mechanisms, cover sheet generation, manual provider override options, and escalation queues for failed dispatches.

## Acceptance Criteria
1. System shall automatically route outbound clinical faxes to the assigned primary care provider's verified fax number.
2. If primary care provider fax delivery fails, system shall attempt retry up to 3 times at 5-minute intervals.
3. System shall generate a transmission audit record containing timestamp, recipient fax number, page count, and status.
4. If all retries fail, the outbound fax shall be routed to the central unassigned fax review queue.
5. Users can manually select an alternate care team provider from the member's care team panel prior to dispatch.
6. The outbound fax payload must include a HIPAA-compliant cover sheet with redacted member SSN and full NPI details.

## Out of Scope
- Inbound optical character recognition (OCR) processing of returned faxes.
- Integration with non-fax communication channels such as Direct Secure Messaging or SMS.

## Glossary
- **PCP**: Primary Care Provider assigned to a member chart.
- **Care Team Panel**: The multidisciplinary group of practitioners assigned to manage a member's care.
- **Unassigned Fax Review Queue**: A centralized portal workflow where dispatches with invalid numbers or complete delivery failure are triaged by operations.