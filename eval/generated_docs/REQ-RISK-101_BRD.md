# [REQ-RISK-101] Member Risk Score Recalculation on Diagnosis Code Update

## Overview
This document specifies the operational and technical requirements for automatically triggering, calculating, and auditing Hierarchical Condition Category (HCC) risk score adjustments within the Reach Healthcare Platform whenever a member's diagnosis records are updated.

## Objectives
- Ensure real-time accuracy of member risk stratification profiles.
- Automate HCC risk score recalculations upon diagnosis code additions, updates, or deletions.
- Maintain complete audit histories for compliance and CMS reporting.
- Notify care teams when member risk profile tier transitions occur.

## Scope
- Automated event-driven triggers on ICD-10 diagnosis code modifications.
- Score calculation engine integration adhering to standard CMS-HCC risk adjustment models.
- Audit logging and timeline notification engine.
- Data validation and error handling for invalid or deprecated clinical coding.

## Acceptance Criteria
1. The system must automatically trigger a recalculation of a member's Hierarchical Condition Category (HCC) risk score whenever an ICD-10 diagnosis code is added, updated, or deleted in the member record.
2. Risk score recalculations must complete within 30 seconds of the diagnosis change submission.
3. If an updated diagnosis code increases the member's risk category, an automated risk stratification alert must be logged in the member timeline.
4. Historical risk scores and their associated calculation timestamps must be retained without overwriting existing records.
5. Invalid or deprecated ICD-10 codes must reject the recalculation request and flag an error in the ingestion queue.
6. Recalculation logs must record the triggering user or source API, previous score, updated score, and effective date.

## Out of Scope
- Manual score overrides by clinical staff.
- Retrospective risk adjustment analytics reporting for commercial payers.
- Adjustments based solely on prescription drug claims (RxHCC) outside of ICD-10 updates.

## Glossary
- **HCC**: Hierarchical Condition Category used by CMS for risk adjustment.
- **ICD-10**: International Classification of Diseases, 10th Revision.
- **CMS**: Centers for Medicare & Medicaid Services.