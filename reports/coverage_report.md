# 📊 Business Requirement to Automation Coverage Report

*Generated on: 2026-08-22 07:51:28*

## 📈 Executive Summary

| Metric | Count / Value | Percentage |
| :--- | :--- | :--- |
| **Total Business Requirements** | `1` | `100.0%` |
| **Scenarios in Repository** | `12` | `-` |
| **Average Test Match** | `90.0%` | `-` |
| 🟢 **Fully Covered** | `0` | `0.0%` |
| 🟡 **Partially Covered** | `1` | `100.0%` |
| 🔴 **Not Covered / Missing** | `0` | `0.0%` |

---

## 📋 Requirement Coverage & Grounded Evidence Details

### 🟡 Requirement: Feature: F1793489: Reach | Expand Manual User Member Alerts (Add 3 New Alerts)
Impacted Applications & Markets: QOM / Reach Portal | National CEQ & WellMed
Objective: Enable Reach/QOM outreach managers to manually create, display, edit, and retire member alerts directly within the member profile left panel to streamline outreach management *(⚡ Cached)*

**ID:** `REQ-001` | **Category:** `General` | **Document:** `sample_data\business_docs\Alerts.md:1`

**Overall Status:** **`PARTIALLY_COVERED`** (90% Match)

**Reasoning:** Candidate tests cover core operations for this requirement but miss specific boundary/negative paths.

**Evidence:**

1. **File:** `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini_test.feature`
   - **Feature:** [QOM0031] - Create and Display Manual Member Alerts in Left Panel
   - **Scenario:** Add new manual member alert and verify orange badge on Left Panel (Line 8)
   - **Match:** `90%`
   - **Status:** `PARTIALLY_COVERED`
   - **Reason:** This scenario directly addresses the creation and display of new manual member alerts for all three specified alert types ('Out of Country', 'Member request - Hold Outreach', 'Hospitalized'). It verifies key UI/UX specifications for the Left Panel (orange background, info icon, tooltip with start date and notes) and Right Panel ('Manage Member Alerts' drawer, mandatory start date, notes field, save action). However, it does not explicitly verify the 'optional end date' during creation, the 'cancel' action, or the 'toggle between User and System alerts' within the Right Panel drawer. It also does not cover editing, retiring, or the history drawer.
   - **Steps:**
     ```gherkin
    When User clicks on "Edit Alerts" icon in member left panel
    Then Verify "Manage Member Alerts" right panel drawer is displayed
    When User selects alert category "<AlertName>"
    And User enters start date "Today" and notes "<NoteText>"
    And User clicks on "Save" button in right panel
    Then User verifies alert "<AlertName>" is visible on the Left Panel with "orange" background
    And User hovers over alert info icon and verifies tooltip contains start date and notes "<NoteText>"
    Examples:
      | AlertName                       | NoteText                              |
      | Out of Country                  | Traveling outside US until next month |
      | Member request - Hold Outreach  | Member requested pause on phone calls |
      | Hospitalized                    | Admitted for inpatient observation    |
     ```

2. **File:** `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333851742.feature`
   - **Feature:** [QOM0032] - Edit and Retire Manual Member Alerts
   - **Scenario:** Modify active alert end date from Right Panel (Line 8)
   - **Match:** `75%`
   - **Status:** `PARTIALLY_COVERED`
   - **Reason:** This scenario covers the 'edit' and 'retire' aspects of the requirement. It verifies updating an optional end date and notes, and the subsequent removal of the alert from the Left Panel's active view, signifying retirement. It also explicitly validates the 'toggle between User and System alerts' in the 'Manage Member Alerts' panel. It uses one of the new alert types ('Hospitalized'). However, it doesn't cover alert creation, the full display specifications (info icon, tooltip), the other two new alert types, or the history drawer.
   - **Steps:**
     ```gherkin
    When User clicks on "Edit Alerts" icon in member left panel
    And User toggles to "User" alerts tab in "Manage Member Alerts" panel
    And User updates end date to "Today" and notes "Discharged from care" for alert "Hospitalized"
    And User clicks on "Save" button
    Then User verifies alert "Hospitalized" is removed from Left Panel active view
     ```

3. **File:** `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333842202.feature`
   - **Feature:** [QOM0026] - Member Demographics Right Panel and Alert State Consistency
   - **Scenario:** Validate persistent alert indicators while editing member demographic details (Line 8)
   - **Match:** `20%`
   - **Status:** `PARTIALLY_COVERED`
   - **Reason:** This scenario indirectly verifies a portion of the 'display' requirement. It confirms that an active manual alert (specifically 'Member request - Hold Outreach', one of the new types) remains visible on the Left Panel with an orange background even when a different right panel (Demographics) is open and being interacted with. It does not cover creating, editing, retiring, full display specifications (tooltip, info icon), or the history drawer. Its primary focus is on UI consistency rather than alert functionality itself.
   - **Steps:**
     ```gherkin
    Given Member "Donald Draper" has active manual alert "Member request - Hold Outreach"
    And Verify member left panel still displays alert "Member request - Hold Outreach" in "orange" background
    And Verify member summary details and active alerts remain synchronized on Left Panel
     ```

4. **File:** `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333851742.feature`
   - **Feature:** [QOM0032] - Edit and Retire Manual Member Alerts
   - **Scenario:** Inactivate alert and verify audit trail in History Drawer (Line 15)
   - **Match:** `70%`
   - **Status:** `PARTIALLY_COVERED`
   - **Reason:** This scenario directly addresses the 'History Drawer' UI/UX specification. It verifies that the 'History Drawer' opens, shows inactive alerts (specifically 'Hospitalized', one of the new types), and displays audit history (user and updated timestamp). This strongly supports the 'retire' and history aspects. It does not cover creation, active alert display, or editing, and only verifies the 'top row' rather than explicit ordering by inactivation timestamp.
   - **Steps:**
     ```gherkin
    When User clicks on "History" icon in "Manage Member Alerts" panel
    Then Verify "History Drawer" opens showing inactive alerts
    And User verifies the top row contains alert "Hospitalized"
    And Verify inactive alert displays last updated by "priority_roster_tester" with current timestamp
     ```

**Missing Coverage:**
- "Manage Member Alerts" drawer: 'cancel' action functionality is not explicitly tested.
- Left Panel tooltip: Verification that the tooltip includes the 'end date' when present, as specified in UI/UX.
- History Drawer: Verification that inactive/retired alerts are explicitly 'ordered by most recent inactivation timestamp'.
- Negative path testing for alert creation/editing (e.g., missing mandatory fields, invalid dates, exceeding character limits for notes).
- Verification that only active alerts are shown on the left panel (e.g., an expired alert is no longer visible).

**Citations:**
- `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini_test.feature` ➔ **[QOM0031] - Create and Display Manual Member Alerts in Left Panel** : *Add new manual member alert and verify orange badge on Left Panel* (Line 8)
- `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333851742.feature` ➔ **[QOM0032] - Edit and Retire Manual Member Alerts** : *Modify active alert end date from Right Panel* (Line 8)
- `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333842202.feature` ➔ **[QOM0026] - Member Demographics Right Panel and Alert State Consistency** : *Validate persistent alert indicators while editing member demographic details* (Line 8)
- `C:\Users\Adarsh Teja Kalakand\Desktop\rag\sample_data\feature_repos\gemini-code-1787333851742.feature` ➔ **[QOM0032] - Edit and Retire Manual Member Alerts** : *Inactivate alert and verify audit trail in History Drawer* (Line 15)

---
