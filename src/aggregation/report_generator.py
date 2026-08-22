"""Report generator producing Markdown, JSON, and HTML reports conforming to Specification §30."""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from src.aggregation.aggregator import GlobalCoverageReport


class ReportGenerator:
    """Generates structured coverage reports in Markdown, JSON, and interactive HTML format."""

    def __init__(self, output_dir: str or Path = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, report: GlobalCoverageReport, base_name: str = "coverage_report") -> Dict[str, str]:
        json_path = self.output_dir / f"{base_name}.json"
        md_path = self.output_dir / f"{base_name}.md"
        html_path = self.output_dir / f"{base_name}.html"

        self.generate_json(report, json_path)
        self.generate_markdown(report, md_path)
        self.generate_html(report, html_path)

        return {
            "json": str(json_path),
            "markdown": str(md_path),
            "html": str(html_path),
        }

    def generate_json(self, report: GlobalCoverageReport, output_file: Path) -> None:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

    def generate_markdown(self, report: GlobalCoverageReport, output_file: Path) -> None:
        """Generates Markdown report conforming to Section 30."""
        lines = [
            "# 📊 Business Requirement to Automation Coverage Report",
            "",
            f"*Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
            "",
            "## 📈 Executive Summary",
            "",
            "| Metric | Count / Value | Percentage |",
            "| :--- | :--- | :--- |",
            f"| **Total Business Requirements** | `{report.total_requirements}` | `100.0%` |",
            f"| **Scenarios in Repository** | `{report.total_feature_scenarios}` | `-` |",
            f"| **Average Test Match** | `{report.average_match_pct:.1f}%` | `-` |",
            f"| 🟢 **Fully Covered** | `{report.covered_count}` | `{report.coverage_rate:.1f}%` |",
            f"| 🟡 **Partially Covered** | `{report.partial_count}` | `{report.partial_rate:.1f}%` |",
            f"| 🔴 **Not Covered / Missing** | `{report.uncovered_count}` | `{report.uncovered_rate:.1f}%` |",
            "",
            "---",
            "",
            "## 📋 Requirement Coverage & Grounded Evidence Details",
            "",
        ]

        for v in report.verdicts:
            status_icon = "🟢" if v.overall_classification == "FULLY_COVERED" else ("🟡" if v.overall_classification == "PARTIALLY_COVERED" else "🔴")
            cached_tag = " *(⚡ Cached)*" if v.cached else ""

            lines.extend([
                f"### {status_icon} Requirement: {v.title}{cached_tag}",
                "",
                f"**ID:** `{v.req_id}` | **Category:** `{v.category}` | **Document:** `{v.source_file}:{v.line_number}`",
                "",
                f"**Overall Status:** **`{v.overall_classification}`** ({v.match_percentage}% union coverage across files)",
                "",
                f"**Reasoning:** {v.reasoning}",
                "",
            ])

            if v.coverage_map:
                lines.append("**How tests connect (union):**")
                for row in v.coverage_map:
                    covers = row.get("covers") or []
                    if not covers:
                        continue
                    loc = row.get("file_path") or row.get("scenario_id")
                    name = row.get("scenario_name") or ""
                    label = f"`{loc}`" + (f" — {name}" if name else "")
                    lines.append(f"- {label}: " + "; ".join(covers))
                lines.append("")

            if v.covered_criteria:
                lines.append("**Criteria covered (union):**")
                for c in v.covered_criteria:
                    lines.append(f"- {c}")
                lines.append("")

            # Evidence Section conforming to Section 30
            relevant_citations = [c for c in v.citations if c.role in ("FULLY_COVERED", "PARTIALLY_COVERED") or c.match_percentage > 0]

            if relevant_citations:
                lines.append("**Evidence:**")
                lines.append("")
                for idx, c in enumerate(relevant_citations, start=1):
                    lines.extend([
                        f"{idx}. **File:** `{c.file_path}`",
                        f"   - **Feature:** {c.feature_title}",
                        f"   - **Scenario:** {c.scenario_name} (Line {c.line_number})",
                        f"   - **Individual alignment:** `{c.match_percentage}%`",
                        f"   - **Status:** `{c.role}`",
                        f"   - **Reason:** {c.verifies}",
                    ])
                    if c.evidence_steps:
                        lines.extend([
                            f"   - **Steps:**",
                            f"     ```gherkin",
                            f"{c.evidence_steps}",
                            f"     ```",
                        ])
                    lines.append("")
            else:
                lines.extend([
                    "**Evidence:**",
                    "*No supporting automation evidence found in the repository.*",
                    "",
                ])

            # Missing coverage & gaps
            if v.missing_gaps:
                lines.append("**Missing Coverage:**")
                for g in v.missing_gaps:
                    lines.append(f"- {g}")
                lines.append("")

            # Citations list
            if relevant_citations:
                lines.append("**Citations:**")
                for c in relevant_citations:
                    lines.append(f"- `{c.file_path}` ➔ **{c.feature_title}** : *{c.scenario_name}* (Line {c.line_number})")
                lines.append("")
            else:
                lines.extend([
                    "**Citation:**",
                    "No supporting automation evidence found.",
                    "",
                ])

            lines.append("---")
            lines.append("")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def generate_html(self, report: GlobalCoverageReport, output_file: Path) -> None:
        """Generates self-contained interactive HTML dashboard."""
        json_data = json.dumps(report.to_dict())
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Coverage Report Dashboard</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Inter', sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ color: #38bdf8; font-size: 24px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 24px 0; }}
    .card {{ background: #1e293b; padding: 20px; border-radius: 10px; border: 1px solid #334155; text-align: center; }}
    .card .val {{ font-size: 28px; font-weight: 800; margin-top: 6px; }}
    .req-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 20px; margin-bottom: 16px; }}
    .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700; }}
    .badge-covered {{ background: rgba(34, 197, 94, 0.2); color: #22c55e; }}
    .badge-partial {{ background: rgba(234, 179, 8, 0.2); color: #eab308; }}
    .badge-uncovered {{ background: rgba(239, 68, 68, 0.2); color: #ef4444; }}
    pre {{ background: #0f172a; padding: 12px; border-radius: 6px; font-family: 'Fira Code', monospace; font-size: 12.5px; overflow-x: auto; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>📊 Requirement Test Coverage & Verification Report</h1>
    <div class="cards">
      <div class="card"><div>Total Requirements</div><div class="val">{report.total_requirements}</div></div>
      <div class="card"><div>Average Match</div><div class="val" style="color:#38bdf8;">{report.average_match_pct:.1f}%</div></div>
      <div class="card"><div>Fully Covered</div><div class="val" style="color:#22c55e;">{report.covered_count}</div></div>
      <div class="card"><div>Gaps / Missing</div><div class="val" style="color:#ef4444;">{report.uncovered_count}</div></div>
    </div>
    <div id="content"></div>
  </div>
  <script>
    const data = {json_data};
    const c = document.getElementById('content');
    data.requirements.forEach(r => {{
      const bClass = r.overall_classification === 'FULLY_COVERED' ? 'badge-covered' : (r.overall_classification === 'PARTIALLY_COVERED' ? 'badge-partial' : 'badge-uncovered');
      const box = document.createElement('div');
      box.className = 'req-box';
      box.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h2 style="font-size:17px; margin:0;">[${{r.req_id}}] ${{r.title}}</h2>
          <span class="badge ${{bClass}}">${{r.overall_classification}} (${{r.match_percentage}}% union)</span>
        </div>
        <p style="margin-top:10px; color:#94a3b8; font-size:14px;">${{r.reasoning}}</p>
        ${{(r.coverage_map || []).filter(m => (m.covers || []).length).map(m => `<div style="font-size:13px; color:#cbd5e1; margin-top:6px;">🔗 ${{m.file_path || m.scenario_id}}: ${{(m.covers || []).join('; ')}}</div>`).join('')}}
        ${{r.primary_citation ? `<div style="font-size:13px; color:#38bdf8; font-family:'Fira Code', monospace; margin-top:8px;">🎯 Strongest individual file: ${{r.primary_citation.file_path}} : ${{r.primary_citation.scenario_name}}</div>` : ''}}
      `;
      c.appendChild(box);
    }});
  </script>
</body>
</html>
"""
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html)
