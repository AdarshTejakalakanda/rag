# [REQ-MAMP-101] Medication Adherence Monitoring and Pharmacy Outreach Trigger

## Overview
This business specification details the automated compliance monitoring for chronic disease medication regimes. The system analyzes pharmacy claims data to identify non-adherent members based on Proportion of Days Covered (PDC) calculations and triggers proactive pharmacy intervention tasks.

## Objectives
- Maintain CMS Star Ratings for medication adherence across targeted therapeutic classes (Statins, Diabetes, Hypertension).
- Proactively alert clinical outreach teams to gaps in member medication fill behavior.
- Track intervention outcome status and eliminate redundant outreach tasks.

## Scope
- Daily processing of pharmacy fill claim streams.
- PDC threshold calculation logic.
- Automated task creation for pharmacy outreach specialists.
- Documentation of member barriers and outreach disposition.
- Post-intervention suppression rules.

## Acceptance Criteria
1. The system must calculate Proportion of Days Covered (PDC) metrics nightly for targeted chronic medication classes.
2. Members falling below an 80% PDC threshold must be automatically flagged as non-adherent.
3. The system must generate an automated pharmacy outreach trigger task when a member is flagged as non-adherent.
4. Clinical users can document outreach attempts, member-reported barriers, and resolution statuses in the intervention log.
5. The platform must suppress new outreach triggers for 30 days once an outreach task is marked completed or in-progress.

## Out of Scope
- Real-time point-of-sale claim rejection overrides.
- Automated mail-order drug dispatch.
- Direct integration with retail pharmacy inventory management software.

## Glossary
- **Proportion of Days Covered (PDC)**: The proportion of days in a measurement period that a member has access to the medication based on prescription fill dates and days supply.
- **Chronic Medication Classes**: Targeted drug groups including oral anti-diabetics, RAS antagonists, and statins.
- **Outreach Trigger**: An automated clinical task assigned to a pharmacy technician or care manager to contact a non-adherent member.