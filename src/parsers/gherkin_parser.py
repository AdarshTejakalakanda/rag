"""Gherkin .feature file parser with Canonical Text generation and Domain Term Preservation.

Conforms to Specifications §5, §6, §7:
- AST parsing (Feature, Background, Scenario, Scenario Outline, Given, When, Then, And, But, Data tables, Examples).
- Canonical text generation preserving domain terms (QOM, PA, MVN, EDI 278, Member 360, HICN, PBP, CDO, UI labels).
- Raw Gherkin preservation for mandatory citations and evidence.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import List, Dict, Any, Optional
import os

try:
    import xxhash
    def fast_hash(text: str) -> str:
        return xxhash.xxh64(text.encode("utf-8")).hexdigest()
except ImportError:
    import hashlib
    def fast_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class ScenarioChunk:
    """Represents an automated test scenario conforming to Specification §6."""
    scenario_id: str
    repository_id: str = "default"
    file_path: str = ""
    line_number: int = 1
    feature_name: str = ""
    feature_title: str = ""
    feature_description: str = ""
    scenario_name: str = ""
    scenario_type: str = "Scenario"  # 'Scenario' or 'Scenario Outline'
    tags: List[str] = field(default_factory=list)
    background: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    canonical_text: str = ""
    raw_gherkin: str = ""
    full_text: str = ""
    content_hash: str = ""
    last_modified: str = ""
    embedding_version: str = "v1.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.feature_title and not self.feature_name:
            self.feature_name = self.feature_title
        elif self.feature_name and not self.feature_title:
            self.feature_title = self.feature_name

        if self.full_text and not self.canonical_text:
            self.canonical_text = self.full_text
        elif self.canonical_text and not self.full_text:
            self.full_text = self.canonical_text

    @property
    def repo_id(self) -> str:
        return self.repository_id

    @property
    def background_steps(self) -> List[str]:
        return self.background

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "repository_id": self.repository_id,
            "file_path": self.file_path,
            "feature_name": self.feature_name,
            "scenario_name": self.scenario_name,
            "scenario_type": self.scenario_type,
            "tags": self.tags,
            "background": self.background,
            "steps": self.steps,
            "examples": self.examples,
            "canonical_text": self.canonical_text,
            "raw_gherkin": self.raw_gherkin,
            "content_hash": self.content_hash,
            "last_modified": self.last_modified,
            "embedding_version": self.embedding_version,
            "metadata": self.metadata,
        }


class GherkinParser:
    """Parser for Gherkin .feature files extracting structured scenarios with dual representations."""

    STEP_KEYWORDS = ("Given", "When", "Then", "And", "But", "*")

    @classmethod
    def generate_canonical_text(
        cls,
        feature_name: str,
        feature_desc: str,
        tags: List[str],
        background: List[str],
        scenario_name: str,
        steps: List[str],
        examples: List[str],
    ) -> str:
        """
        Generates canonical text for retrieval/embedding while strictly preserving
        domain terms (QOM, PA, MVN, EDI 278, Member 360, HICN, PBP, CDO, UI labels, exact validation messages).
        """
        parts = [f"Feature: {feature_name}"]
        if feature_desc:
            parts.append(feature_desc.strip())
        if tags:
            parts.append(f"Tags: {' '.join(tags)}")
        if background:
            parts.append("Background:\n" + "\n".join(f"  {s}" for s in background))
        parts.append(f"Scenario: {scenario_name}")
        if steps:
            parts.append("\n".join(f"  {s}" for s in steps))
        if examples:
            parts.append("Examples:\n" + "\n".join(f"  {e}" for e in examples))

        raw_canonical = "\n".join(p for p in parts if p.strip())
        # Clean extra trailing spaces while preserving exact domain terms and case
        cleaned_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_canonical.splitlines()]
        return "\n".join(cleaned_lines)

    @classmethod
    def parse_file(cls, file_path: str or Path, repo_id: str = "default") -> List[ScenarioChunk]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Feature file not found: {file_path}")

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        mtime_str = str(os.path.getmtime(path)) if path.exists() else ""
        return cls.parse_content(content, file_path=str(path), repo_id=repo_id, last_modified=mtime_str)

    @classmethod
    def parse_content(
        cls,
        content: str,
        file_path: str = "",
        repo_id: str = "default",
        last_modified: str = "",
    ) -> List[ScenarioChunk]:
        lines = content.splitlines()
        chunks: List[ScenarioChunk] = []

        feature_name = ""
        feature_desc_lines: List[str] = []
        feature_tags: List[str] = []
        background_steps: List[str] = []

        state = "INIT"
        pending_tags: List[str] = []
        pending_comments: List[str] = []
        current_scenario_tags: List[str] = []
        current_scenario_name = ""
        current_scenario_type = "Scenario"
        current_scenario_line = 1
        current_steps: List[str] = []
        current_examples: List[str] = []
        current_raw_lines: List[str] = []

        def flush_current_scenario():
            nonlocal current_scenario_name, current_steps, current_examples, current_scenario_tags
            nonlocal current_scenario_type, current_scenario_line, current_raw_lines

            if current_scenario_name or current_steps:
                combined_tags = list(dict.fromkeys(feature_tags + current_scenario_tags))
                feature_desc_str = "\n".join(feature_desc_lines).strip()

                canonical_text = cls.generate_canonical_text(
                    feature_name=feature_name,
                    feature_desc=feature_desc_str,
                    tags=combined_tags,
                    background=background_steps,
                    scenario_name=current_scenario_name or "Unnamed Scenario",
                    steps=current_steps,
                    examples=current_examples,
                )

                raw_gherkin = "\n".join(current_raw_lines).strip()
                content_hash = fast_hash(raw_gherkin + canonical_text)

                id_seed = f"{repo_id}#{file_path}#{feature_name}#{current_scenario_name}#{current_scenario_line}"
                scenario_id = fast_hash(id_seed)[:16]

                chunk = ScenarioChunk(
                    scenario_id=scenario_id,
                    repository_id=repo_id,
                    file_path=file_path,
                    line_number=current_scenario_line,
                    feature_name=feature_name,
                    scenario_name=current_scenario_name or "Unnamed Scenario",
                    scenario_type=current_scenario_type,
                    tags=combined_tags,
                    background=list(background_steps),
                    steps=list(current_steps),
                    examples=list(current_examples),
                    canonical_text=canonical_text,
                    raw_gherkin=raw_gherkin,
                    content_hash=content_hash,
                    last_modified=last_modified,
                    embedding_version="v1.0",
                    metadata={"total_steps": len(current_steps) + len(background_steps)}
                )
                chunks.append(chunk)

            # Reset
            current_scenario_name = ""
            current_scenario_type = "Scenario"
            current_scenario_line = 1
            current_steps = []
            current_examples = []
            current_scenario_tags = []
            current_raw_lines = []

        for line_idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()

            if not line:
                continue

            # Preserve comments attached to scenarios or features
            if line.startswith("#"):
                if state == "IN_SCENARIO":
                    current_raw_lines.append(raw_line)
                    current_steps.append(raw_line)
                else:
                    pending_comments.append(raw_line)
                continue

            # Tag lines
            if line.startswith("@"):
                tags = [t.strip() for t in line.split() if t.startswith("@")]
                if state in ("INIT", "FEATURE"):
                    feature_tags.extend(tags)
                else:
                    if current_scenario_name and current_steps:
                        flush_current_scenario()
                    pending_tags.extend(tags)
                current_raw_lines.append(raw_line)
                continue

            # Feature header
            if re.match(r"^Feature\s*:", line, re.IGNORECASE):
                feature_name = re.sub(r"^Feature\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                state = "IN_FEATURE_DESC"
                continue

            # Background header
            if re.match(r"^Background\s*:", line, re.IGNORECASE):
                flush_current_scenario()
                state = "IN_BACKGROUND"
                continue

            # Scenario or Scenario Outline header
            scenario_match = re.match(r"^(Scenario Outline|Scenario Template|Scenario)\s*:\s*(.*)$", line, re.IGNORECASE)
            if scenario_match:
                flush_current_scenario()
                stype_raw, sname = scenario_match.groups()
                current_scenario_type = "Scenario Outline" if "outline" in stype_raw.lower() or "template" in stype_raw.lower() else "Scenario"
                current_scenario_name = sname.strip()
                current_scenario_line = line_idx
                current_scenario_tags = list(pending_tags)
                pending_tags = []
                state = "IN_SCENARIO"
                if pending_comments:
                    current_raw_lines.extend(pending_comments)
                    current_steps.extend(pending_comments)
                    pending_comments = []
                current_raw_lines.append(raw_line)
                continue

            # Examples header
            if re.match(r"^(Examples|Scenarios)\s*:", line, re.IGNORECASE):
                state = "IN_EXAMPLES"
                current_raw_lines.append(raw_line)
                continue

            # Step line
            is_step = any(line.startswith(kw + " ") or line.startswith(kw + "\t") for kw in cls.STEP_KEYWORDS)
            if is_step:
                if state == "IN_BACKGROUND":
                    background_steps.append(line)
                elif state in ("IN_SCENARIO", "IN_EXAMPLES"):
                    current_steps.append(line)
                current_raw_lines.append(raw_line)
                continue

            # Data table row or Example row
            if line.startswith("|") and line.endswith("|"):
                if state == "IN_EXAMPLES":
                    current_examples.append(line)
                elif state in ("IN_SCENARIO", "IN_BACKGROUND"):
                    if state == "IN_BACKGROUND" and background_steps:
                        background_steps[-1] += f"\n    {line}"
                    elif current_steps:
                        current_steps[-1] += f"\n    {line}"
                current_raw_lines.append(raw_line)
                continue

            # Feature description lines
            if state == "IN_FEATURE_DESC":
                feature_desc_lines.append(line)
                continue

        flush_current_scenario()
        return chunks

    @classmethod
    def parse_directory(cls, dir_path: str or Path, repo_id: str = "default", recursive: bool = True) -> List[ScenarioChunk]:
        """Recursively parses all .feature files in any directory hierarchy under the repository."""
        directory = Path(dir_path)
        if not directory.exists():
            return []

        pattern = "**/*.feature" if recursive else "*.feature"
        all_chunks: List[ScenarioChunk] = []
        for feature_file in sorted(directory.glob(pattern)):
            if feature_file.is_file():
                try:
                    all_chunks.extend(cls.parse_file(feature_file, repo_id=repo_id))
                except Exception as e:
                    print(f"Warning: Failed parsing {feature_file}: {e}")
        return all_chunks


class UniversalFileParser:
    """Universal repository parser supporting .feature, .md, .txt, .json, .yaml, .csv, .docx, .pdf, etc."""

    SUPPORTED_EXTENSIONS = {
        ".feature", ".md", ".markdown", ".txt", ".text",
        ".json", ".yaml", ".yml", ".csv", ".tsv",
        ".pdf", ".docx", ".rst", ".xml", ".html"
    }

    IGNORED_EXTENSIONS = {
        ".pyc", ".db", ".sqlite", ".sqlite3", ".tmp", ".swp",
        ".lock", ".exe", ".dll", ".pyd", ".bin", ".tar", ".gz",
        ".zip", ".7z", ".rar", ".iso", ".png", ".jpg", ".jpeg",
        ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot"
    }

    @classmethod
    def is_indexable(cls, path: str or Path) -> bool:
        p = Path(path)
        if p.name.startswith(".") or "__pycache__" in p.parts:
            return False
        ext = p.suffix.lower()
        if ext in cls.IGNORED_EXTENSIONS:
            return False
        if ext in cls.SUPPORTED_EXTENSIONS or not ext:
            return True
        return True

    @classmethod
    def parse_file(cls, file_path: str or Path, repo_id: str = "default") -> List[ScenarioChunk]:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return []

        suffix = path.suffix.lower()
        if suffix == ".feature":
            return GherkinParser.parse_file(path, repo_id=repo_id)

        # Document loading for markdown, txt, json, yaml, csv, pdf, docx, etc.
        try:
            from src.parsers.document_loaders import DocumentLoaderFactory
            raw_text = DocumentLoaderFactory.load_file(path)
        except Exception:
            try:
                raw_text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        if not raw_text or not raw_text.strip():
            return []

        # If Markdown, split into header sections
        if suffix in (".md", ".markdown"):
            return cls._parse_markdown(path, raw_text, repo_id=repo_id)

        # For text, json, yaml, csv, pdf, docx, create semantic chunks
        return cls._chunk_document(path, raw_text, repo_id=repo_id)

    @classmethod
    def _parse_markdown(cls, path: Path, raw_text: str, repo_id: str) -> List[ScenarioChunk]:
        chunks = []
        lines = raw_text.splitlines()
        current_header = path.stem
        current_lines = []
        header_line = 1

        def flush():
            nonlocal current_header, current_lines, header_line
            if current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    canon = f"Document: {path.name}\nSection: {current_header}\n{body}"
                    cid = fast_hash(f"{repo_id}#{str(path.resolve())}#{current_header}#{header_line}")[:16]
                    chunks.append(ScenarioChunk(
                        scenario_id=cid,
                        repository_id=repo_id,
                        file_path=str(path.resolve()),
                        line_number=header_line,
                        feature_name=path.stem,
                        scenario_name=current_header,
                        scenario_type="Markdown Spec",
                        canonical_text=canon,
                        raw_gherkin=body,
                        full_text=canon,
                        content_hash=fast_hash(canon),
                    ))
            current_lines = []

        for l_idx, line in enumerate(lines, start=1):
            if re.match(r"^#{1,4}\s+(.+)$", line.strip()):
                flush()
                current_header = re.sub(r"^#{1,4}\s+", "", line.strip())
                header_line = l_idx
            else:
                current_lines.append(line)
        flush()

        if not chunks:
            cid = fast_hash(f"{repo_id}#{str(path.resolve())}#1")[:16]
            canon = f"Document: {path.name}\n{raw_text.strip()}"
            chunks.append(ScenarioChunk(
                scenario_id=cid,
                repository_id=repo_id,
                file_path=str(path.resolve()),
                line_number=1,
                feature_name=path.stem,
                scenario_name=path.stem,
                scenario_type="Markdown Spec",
                canonical_text=canon,
                raw_gherkin=raw_text.strip(),
                full_text=canon,
                content_hash=fast_hash(canon),
            ))
        return chunks

    @classmethod
    def _chunk_document(cls, path: Path, raw_text: str, repo_id: str) -> List[ScenarioChunk]:
        chunks = []
        paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [raw_text.strip()]

        current_block = []
        current_len = 0
        part_num = 1

        def flush_block():
            nonlocal current_block, current_len, part_num
            if current_block:
                block_text = "\n\n".join(current_block)
                sc_name = f"{path.name} (Part {part_num})" if part_num > 1 or len(paragraphs) > 1 else path.name
                canon = f"Document: {path.name}\n{block_text}"
                cid = fast_hash(f"{repo_id}#{str(path.resolve())}#{part_num}")[:16]
                chunks.append(ScenarioChunk(
                    scenario_id=cid,
                    repository_id=repo_id,
                    file_path=str(path.resolve()),
                    line_number=1,
                    feature_name=path.stem,
                    scenario_name=sc_name,
                    scenario_type="Document",
                    canonical_text=canon,
                    raw_gherkin=block_text,
                    full_text=canon,
                    content_hash=fast_hash(canon),
                ))
                part_num += 1
                current_block = []
                current_len = 0

        for p in paragraphs:
            if current_len + len(p) > 1200 and current_block:
                flush_block()
            current_block.append(p)
            current_len += len(p)
        flush_block()
        return chunks

    @classmethod
    def parse_directory(cls, dir_path: str or Path, repo_id: str = "default", recursive: bool = True) -> List[ScenarioChunk]:
        directory = Path(dir_path)
        if not directory.exists():
            return []

        all_chunks: List[ScenarioChunk] = []
        files = directory.rglob("*") if recursive else directory.glob("*")
        for f in sorted(files):
            if f.is_file() and cls.is_indexable(f):
                try:
                    all_chunks.extend(cls.parse_file(f, repo_id=repo_id))
                except Exception as e:
                    print(f"Warning: Failed parsing {f}: {e}")
        return all_chunks

