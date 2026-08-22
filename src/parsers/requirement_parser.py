"""Business requirement parser and functional decomposition engine.

Conforms to Specifications §4 & §18:
- Parses multi-format documents (Markdown, TXT, PDF, DOCX).
- Decomposes documents into atomic, meaningful functional requirements.
- Preserves business meaning, acceptance criteria, business rules, and tables without blind sentence splitting.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import List, Dict, Any, Optional
from src.parsers.document_loaders import DocumentLoaderFactory


@dataclass
class RequirementChunk:
    """Atomic functional requirement representation."""
    req_id: str
    title: str
    description: str
    acceptance_criteria: List[str] = field(default_factory=list)
    business_rules: List[str] = field(default_factory=list)
    category: str = "General"
    source_file: str = ""
    line_number: int = 1
    full_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "req_id": self.req_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "business_rules": self.business_rules,
            "category": self.category,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "full_text": self.full_text,
            "metadata": self.metadata,
        }


class RequirementParser:
    """Extracts atomic functional requirements from business documents."""

    REQ_HEADER_REGEX = re.compile(
        r"^(?:#{1,4}\s+)?(?:REQ(?:UIREMENT)?|BRD|AC|F(?:EAT)?|US|PA|QOM)?[-_\s]*([A-Z0-9]+[-_][0-9]+|\b\d+\b)[.:\s-]*(.*)$",
        re.IGNORECASE
    )

    @classmethod
    def parse_file(cls, file_path: str or Path) -> List[RequirementChunk]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {file_path}")

        raw_text = DocumentLoaderFactory.load_file(path)
        return cls.parse_content(raw_text, source_file=str(path))

    @classmethod
    def parse_markdown_or_text(cls, content: str, source_file: str = "") -> List[RequirementChunk]:
        """Alias for parse_content."""
        return cls.parse_content(content, source_file=source_file)

    @classmethod
    def parse_content(cls, content: str, source_file: str = "") -> List[RequirementChunk]:
        lines = content.splitlines()
        chunks: List[RequirementChunk] = []

        current_category = "General"
        current_req_id = ""
        current_title = ""
        current_desc_lines: List[str] = []
        current_ac: List[str] = []
        current_rules: List[str] = []
        current_line_num = 1
        in_ac_section = False
        in_rules_section = False

        def flush_current():
            nonlocal current_req_id, current_title, current_desc_lines, current_ac, current_rules, current_line_num
            if current_req_id or current_title or current_desc_lines:
                desc = "\n".join(current_desc_lines).strip()
                full_parts = []
                if current_title:
                    full_parts.append(f"Requirement: {current_title}")
                if desc:
                    full_parts.append(f"Description: {desc}")
                if current_ac:
                    full_parts.append("Acceptance Criteria:\n" + "\n".join(f"- {a}" for a in current_ac))
                if current_rules:
                    full_parts.append("Business Rules:\n" + "\n".join(f"- {r}" for r in current_rules))

                full_text = "\n\n".join(full_parts)
                rid = current_req_id or f"REQ-{len(chunks) + 1:03d}"
                rtitle = current_title or (desc.split(".")[0] if desc else rid)

                chunks.append(RequirementChunk(
                    req_id=rid,
                    title=rtitle,
                    description=desc,
                    acceptance_criteria=list(current_ac),
                    business_rules=list(current_rules),
                    category=current_category,
                    source_file=source_file,
                    line_number=current_line_num,
                    full_text=full_text,
                ))

            current_req_id = ""
            current_title = ""
            current_desc_lines = []
            current_ac = []
            current_rules = []

        for line_idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()

            if not line:
                continue

            # Category / Document Title: # Feature / Specification Header
            if line.startswith("# ") and not cls.REQ_HEADER_REGEX.match(line):
                flush_current()
                current_category = line.lstrip("#").strip()
                continue

            # Check if line matches a requirement header
            req_match = cls.REQ_HEADER_REGEX.match(line)
            if req_match and (line.startswith("#") or line.lower().startswith("req") or "requirement" in line.lower() or line.startswith("##")):
                flush_current()
                raw_id, raw_title = req_match.groups()
                current_req_id = raw_id.strip() if "-" in raw_id or "_" in raw_id else f"REQ-{raw_id.strip()}"
                current_title = raw_title.strip() or line
                current_line_num = line_idx
                in_ac_section = False
                in_rules_section = False
                continue

            # Section flags
            lower_line = line.lower()
            if any(k in lower_line for k in ("acceptance criteria", "criteria:", "verification steps:")):
                in_ac_section = True
                in_rules_section = False
                continue
            elif any(k in lower_line for k in ("business rules", "rules:", "validation rules:")):
                in_rules_section = True
                in_ac_section = False
                continue
            elif line.startswith("## ") or line.startswith("### "):
                in_ac_section = False
                in_rules_section = False

            # Collect bullet items
            if line.startswith(("-", "*", "•", "+")) or re.match(r"^\d+\.\s+", line):
                item_text = re.sub(r"^[-*•+\d.]+\s*", "", line).strip()
                if in_ac_section:
                    current_ac.append(item_text)
                elif in_rules_section:
                    current_rules.append(item_text)
                else:
                    current_desc_lines.append(item_text)
                continue

            # Regular prose lines
            current_desc_lines.append(line)

        flush_current()

        # If document had no structured headers, extract meaningful functional sentences/paragraphs
        if not chunks and content.strip():
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            for idx, p in enumerate(paragraphs, start=1):
                first_line = p.splitlines()[0][:80]
                chunks.append(RequirementChunk(
                    req_id=f"REQ-{idx:03d}",
                    title=first_line,
                    description=p,
                    acceptance_criteria=[],
                    business_rules=[],
                    category=current_category,
                    source_file=source_file,
                    line_number=1,
                    full_text=p,
                ))

        return chunks

    @classmethod
    def parse_directory(cls, dir_path: str or Path) -> List[RequirementChunk]:
        """Parses all supported requirement documents in a directory."""
        directory = Path(dir_path)
        if not directory.exists():
            return []

        all_reqs: List[RequirementChunk] = []
        for file_path in sorted(directory.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in (".md", ".markdown", ".txt", ".pdf", ".docx"):
                try:
                    all_reqs.extend(cls.parse_file(file_path))
                except Exception as e:
                    print(f"Warning: Failed parsing document {file_path}: {e}")
        return all_reqs
