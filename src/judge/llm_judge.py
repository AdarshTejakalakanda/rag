"""Batch LLM Judge executing ONE single call per requirement.

Per-scenario scores stay independent. Requirement-level score is the UNION of
acceptance criteria across files (connect-the-dots), without forcing 100/0.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional
import re

from src.parsers.requirement_parser import RequirementChunk
from src.parsers.gherkin_parser import ScenarioChunk
from src.judge.llm_client import LLMClient
from src.judge.judge_prompts import (
    BATCH_JUDGE_SYSTEM_PROMPT,
    BATCH_JUDGE_USER_TEMPLATE,
    PROMPT_VERSION,
)
from src.storage.state_db import StateDatabase
from src.config import JudgeConfig


def normalize_criterion(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for criterion matching."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def canonical_requirement_criteria(requirement: RequirementChunk) -> List[str]:
    """Acceptance criteria plus business rules, skipping empty / placeholder rows."""
    items: List[str] = []
    seen = set()
    for raw in list(requirement.acceptance_criteria or []) + list(requirement.business_rules or []):
        text = (raw or "").strip()
        if not text or text.lower() in ("none specified", "none", "n/a"):
            continue
        key = normalize_criterion(text)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def map_to_canonical(stated: str, canonical: List[str]) -> Optional[str]:
    """
    Map LLM-stated criterion text back onto a canonical requirement criterion with
    strict token-overlap verification to eliminate loose false-positive mappings.
    """
    needle = normalize_criterion(stated)
    if not needle:
        return None

    needle_words = set(re.findall(r"\w+", needle.lower()))
    if not needle_words:
        return None

    best_match = None
    best_score = 0.0

    for item in canonical:
        hay = normalize_criterion(item)
        if not hay:
            continue
        hay_words = set(re.findall(r"\w+", hay.lower()))
        if not hay_words:
            continue

        # Exact match
        if needle == hay:
            return item

        # Substantial substring (at least 3 words and covers >= 50% length of the target criterion)
        if len(needle_words) >= 3 and (needle in hay or hay in needle):
            if len(needle) >= 0.5 * len(hay) or len(hay) >= 0.5 * len(needle):
                return item

        # Token overlap & Jaccard similarity
        intersection = needle_words.intersection(hay_words)
        if not intersection:
            continue
        overlap = len(intersection) / min(len(needle_words), len(hay_words))
        jaccard = len(intersection) / len(needle_words.union(hay_words))

        # Composite score
        score = max(overlap * 0.6 + jaccard * 0.4, jaccard)
        if score > best_score and score >= 0.65:
            best_score = score
            best_match = item

    return best_match


def classify_from_percentage(match_percentage: int) -> str:
    """True ratio classification: 100 fully, 1-99 partial, 0 not covered. No forcing."""
    if match_percentage >= 100:
        return "FULLY_COVERED"
    if match_percentage > 0:
        return "PARTIALLY_COVERED"
    return "NOT_COVERED"


def compute_union_coverage(
    requirement: RequirementChunk,
    evaluations: List[Dict[str, Any]],
    overall_summary: Optional[Dict[str, Any]] = None,
    candidates: Optional[List[Tuple[ScenarioChunk, float, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Union unique criteria evidenced by any candidate. Does not use max(individual %)."""
    overall_summary = overall_summary or {}
    canonical = canonical_requirement_criteria(requirement)

    stated_covered: List[str] = []
    for ev in evaluations:
        if not isinstance(ev, dict):
            continue
        if int(ev.get("match_percentage", 0) or 0) <= 0:
            continue
        for item in ev.get("covered_criteria") or []:
            if item:
                stated_covered.append(str(item))
    for item in overall_summary.get("covered_criteria") or []:
        if item:
            stated_covered.append(str(item))

    if canonical:
        union_covered: List[str] = []
        seen = set()
        for stated in stated_covered:
            mapped = map_to_canonical(stated, canonical)
            if mapped:
                key = normalize_criterion(mapped)
                if key not in seen:
                    seen.add(key)
                    union_covered.append(mapped)
        missing = [c for c in canonical if normalize_criterion(c) not in seen]
        total = len(canonical)
        covered_n = len(union_covered)
        union_pct = int(round((covered_n / total) * 100)) if total else 0
    else:
        covered_norm = {}
        for stated in stated_covered:
            key = normalize_criterion(stated)
            if key:
                covered_norm[key] = stated.strip()
        missing_norm = {}
        for ev in evaluations:
            if not isinstance(ev, dict):
                continue
            for item in ev.get("missing_gaps") or []:
                key = normalize_criterion(str(item))
                if key and key not in covered_norm:
                    missing_norm[key] = str(item).strip()
        for item in overall_summary.get("missing_gaps") or []:
            key = normalize_criterion(str(item))
            if key and key not in covered_norm:
                missing_norm[key] = str(item).strip()
        union_covered = list(covered_norm.values())
        missing = list(missing_norm.values())
        total = len(union_covered) + len(missing)
        covered_n = len(union_covered)
        if total:
            union_pct = int(round((covered_n / total) * 100))
        else:
            llm_union = overall_summary.get("union_match_percentage")
            union_pct = int(llm_union) if llm_union is not None else 0

    union_pct = max(0, min(100, union_pct))

    cand_by_id = {}
    if candidates:
        for sc, _, _ in candidates:
            cand_by_id[sc.scenario_id] = sc

    coverage_map: List[Dict[str, Any]] = []
    llm_map = overall_summary.get("coverage_map") or []
    if isinstance(llm_map, list) and llm_map:
        for row in llm_map:
            if not isinstance(row, dict):
                continue
            sid = row.get("scenario_id") or ""
            sc = cand_by_id.get(sid)
            covers = row.get("covers") or []
            if canonical:
                mapped_covers = []
                seen_c = set()
                for item in covers:
                    mapped = map_to_canonical(str(item), canonical)
                    if mapped and normalize_criterion(mapped) not in seen_c:
                        seen_c.add(normalize_criterion(mapped))
                        mapped_covers.append(mapped)
                covers = mapped_covers
            coverage_map.append({
                "scenario_id": sid,
                "file_path": row.get("file_path") or (sc.file_path if sc else ""),
                "scenario_name": sc.scenario_name if sc else row.get("scenario_name", ""),
                "covers": covers,
            })
    else:
        for ev in evaluations:
            if not isinstance(ev, dict):
                continue
            sid = ev.get("scenario_id") or ev.get("document_id") or ""
            sc = cand_by_id.get(sid)
            covers_raw = ev.get("covered_criteria") or []
            if canonical:
                covers = []
                seen_c = set()
                for item in covers_raw:
                    mapped = map_to_canonical(str(item), canonical)
                    if mapped and normalize_criterion(mapped) not in seen_c:
                        seen_c.add(normalize_criterion(mapped))
                        covers.append(mapped)
            else:
                covers = [str(x) for x in covers_raw if x]
            if not covers:
                continue
            coverage_map.append({
                "scenario_id": sid,
                "file_path": sc.file_path if sc else "",
                "scenario_name": sc.scenario_name if sc else "",
                "covers": covers,
            })

    narrative = (
        overall_summary.get("connecting_narrative")
        or overall_summary.get("union_reasoning")
        or ""
    ).strip()

    return {
        "union_match_percentage": union_pct,
        "overall_classification": classify_from_percentage(union_pct),
        "covered_criteria": union_covered,
        "missing_gaps": missing,
        "coverage_map": coverage_map,
        "connecting_narrative": narrative,
        "covered_count": covered_n,
        "total_criteria": total,
    }


def build_union_reasoning(
    union: Dict[str, Any],
    citations: List["ScenarioCitation"],
) -> str:
    """Requirement-level reasoning: individual alignment plus how files connect."""
    parts: List[str] = []
    union_pct = union["union_match_percentage"]
    covered_n = union["covered_count"]
    total = union["total_criteria"]
    if total:
        parts.append(
            f"Union coverage is {union_pct}% ({covered_n} of {total} criteria evidenced across retrieved files)."
        )
    else:
        parts.append(f"Union coverage is {union_pct}%.")

    if union.get("connecting_narrative"):
        parts.append(union["connecting_narrative"])

    map_bits = []
    for row in union.get("coverage_map") or []:
        covers = row.get("covers") or []
        if not covers:
            continue
        label = row.get("file_path") or row.get("scenario_name") or row.get("scenario_id")
        map_bits.append(f"{label} covers: {'; '.join(covers)}")
    if map_bits and not union.get("connecting_narrative"):
        parts.append("Connecting files: " + " | ".join(map_bits))

    relevant = [c for c in citations if c.match_percentage > 0]
    if relevant:
        aligned = ", ".join(
            f"{c.scenario_name} ({c.match_percentage}% individually in {c.file_path})"
            for c in relevant
        )
        parts.append(f"Individual file alignment: {aligned}.")

    if union.get("missing_gaps"):
        parts.append("Still missing after union: " + "; ".join(union["missing_gaps"]) + ".")

    return " ".join(parts)


@dataclass
class ScenarioCitation:
    """Individual scenario evidence citation conforming to Specification §19."""
    scenario_id: str
    scenario_name: str
    feature_title: str
    file_path: str
    line_number: int
    cross_encoder_score: float
    rrf_score: float = 0.0
    role: str = "IRRELEVANT"
    verifies: str = ""
    evidence_steps: str = ""
    match_percentage: int = 0
    covered_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "feature_title": self.feature_title,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "cross_encoder_score": round(self.cross_encoder_score, 3),
            "rrf_score": round(self.rrf_score, 4),
            "role": self.role,
            "verifies": self.verifies,
            "evidence_steps": self.evidence_steps,
            "match_percentage": self.match_percentage,
            "covered_criteria": self.covered_criteria,
        }


@dataclass
class RequirementJudgeVerdict:
    """Judge verdict for a single business requirement."""
    req_id: str
    title: str
    category: str
    source_file: str
    line_number: int
    match_percentage: int  # union of criteria across files, 0-100, not forced
    overall_classification: str  # 'FULLY_COVERED', 'PARTIALLY_COVERED', 'NOT_COVERED'
    reasoning: str
    primary_citation: Optional[Dict[str, Any]]
    citations: List[ScenarioCitation]
    covered_criteria: List[str] = field(default_factory=list)
    missing_gaps: List[str] = field(default_factory=list)
    suggested_tests: List[str] = field(default_factory=list)
    coverage_map: List[Dict[str, Any]] = field(default_factory=list)
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "req_id": self.req_id,
            "title": self.title,
            "category": self.category,
            "source_file": self.source_file,
            "line_number": self.line_number,
            "match_percentage": self.match_percentage,
            "union_match_percentage": self.match_percentage,
            "overall_classification": self.overall_classification,
            "reasoning": self.reasoning,
            "primary_citation": self.primary_citation,
            "citations": [c.to_dict() for c in self.citations],
            "covered_criteria": self.covered_criteria,
            "missing_gaps": self.missing_gaps,
            "suggested_tests": self.suggested_tests,
            "coverage_map": self.coverage_map,
            "cached": self.cached,
        }


class LLMJudge:
    """Evaluates requirement against Top 10 candidate scenarios in ONE LLM call."""

    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        config: Optional[JudgeConfig] = None,
        client: Optional[LLMClient] = None,
        state_db: Optional[StateDatabase] = None,
    ):
        self.config = config or JudgeConfig()
        self.client = client or LLMClient(config=self.config)
        self.state_db = state_db

    def judge_requirement(
        self,
        requirement: RequirementChunk,
        candidates: List[Tuple[ScenarioChunk, float, Dict[str, Any]]],
        repo_id: str = "default",
        bypass_cache: bool = False,
    ) -> RequirementJudgeVerdict:
        """
        Executes ONE LLM call evaluating all candidates independently, then unions
        criteria across files. Checks multi-factor semantic cache in SQLite first unless bypass_cache=True.
        """
        candidate_ids = [sc.scenario_id for sc, _, _ in candidates]

        if not bypass_cache and self.state_db:
            cached_data = self.state_db.get_cached_judgment(
                requirement_text=requirement.full_text,
                candidate_ids=candidate_ids,
                provider=self.client.provider,
                repo_id=repo_id,
                prompt_version=self.prompt_version,
            )
            if cached_data:
                verdict = self._build_verdict_from_response(requirement, candidates, cached_data)
                verdict.cached = True
                return verdict

        if not candidates:
            return RequirementJudgeVerdict(
                req_id=requirement.req_id,
                title=requirement.title,
                category=requirement.category,
                source_file=requirement.source_file,
                line_number=requirement.line_number,
                match_percentage=0,
                overall_classification="NOT_COVERED",
                reasoning="No automated test scenarios were retrieved from the repository.",
                primary_citation=None,
                citations=[],
                covered_criteria=[],
                missing_gaps=canonical_requirement_criteria(requirement) or [
                    "No test scenarios found in repository"
                ],
                suggested_tests=[f"Create automated test scenario for {requirement.title}"],
                coverage_map=[],
                cached=False,
            )

        scenarios_text_blocks = []
        for idx, (sc, ce_score, meta) in enumerate(candidates, start=1):
            start_line = sc.line_number
            raw_lines = sc.raw_gherkin.splitlines() if sc.raw_gherkin else []
            end_line = sc.line_number + max(len(raw_lines) - 1, 0)
            sc_text = (
                f"--- [Candidate #{idx}] Scenario ID: {sc.scenario_id} ---\n"
                f"Feature Name: {sc.feature_name}\n"
                f"Scenario Name: {sc.scenario_name}\n"
                f"File Path: {sc.file_path}\n"
                f"Location: Lines {start_line}-{end_line}\n\n"
                f"Canonical Steps Representation:\n{sc.canonical_text}\n\n"
                f"Raw Gherkin Evidence:\n{sc.raw_gherkin}\n"
            )
            scenarios_text_blocks.append(sc_text)

        scenarios_formatted = "\n".join(scenarios_text_blocks)
        ac_str = "\n".join(f"- {a}" for a in requirement.acceptance_criteria) if requirement.acceptance_criteria else "None specified"
        rules_str = "\n".join(f"- {r}" for r in requirement.business_rules) if requirement.business_rules else "None specified"

        user_prompt = BATCH_JUDGE_USER_TEMPLATE.format(
            req_id=requirement.req_id,
            req_title=requirement.title,
            category=requirement.category,
            source_file=requirement.source_file,
            line_number=requirement.line_number,
            description=requirement.description or requirement.title,
            acceptance_criteria=ac_str,
            business_rules=rules_str,
            candidates_count=len(candidates),
            scenarios_formatted=scenarios_formatted,
        )

        response_dict = self.client.generate_json(
            system_prompt=BATCH_JUDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        if self.state_db:
            self.state_db.store_cached_judgment(
                requirement_text=requirement.full_text,
                candidate_ids=candidate_ids,
                provider=self.client.provider,
                judgment=response_dict,
                repo_id=repo_id,
                prompt_version=self.prompt_version,
            )

        return self._build_verdict_from_response(requirement, candidates, response_dict)

    def _build_verdict_from_response(
        self,
        requirement: RequirementChunk,
        candidates: List[Tuple[ScenarioChunk, float, Dict[str, Any]]],
        data: Dict[str, Any],
    ) -> RequirementJudgeVerdict:
        evals = data.get("evaluations", [])
        overall_summary = data.get("overall_summary", {}) or {}

        eval_map = {(e.get("scenario_id") or e.get("document_id")): e for e in evals if isinstance(e, dict)}

        citations: List[ScenarioCitation] = []
        max_candidate_match = 0
        best_candidate_obj = None

        for sc, ce_score, meta in candidates:
            e_data = eval_map.get(sc.scenario_id, {})
            c_match = int(e_data.get("match_percentage", 0) or 0)
            c_match = max(0, min(100, c_match))
            c_status = e_data.get("status") or classify_from_percentage(c_match)
            if c_status == "NOT_COVERED":
                c_status = "NOT_RELEVANT"
            c_reason = e_data.get("reasoning", "")
            c_covered = [str(x) for x in (e_data.get("covered_criteria") or []) if x]

            raw_evidence = e_data.get("evidence", [])
            if isinstance(raw_evidence, list):
                c_evidence = "\n".join(f"- {ev}" for ev in raw_evidence if ev)
            else:
                c_evidence = str(raw_evidence)

            if c_match > max_candidate_match:
                max_candidate_match = c_match
                best_candidate_obj = {
                    "scenario_id": sc.scenario_id,
                    "scenario_name": sc.scenario_name,
                    "feature_name": sc.feature_name,
                    "file_path": sc.file_path,
                    "line_number": sc.line_number,
                    "cross_encoder_score": ce_score,
                    "individual_match_percentage": c_match,
                }

            citations.append(ScenarioCitation(
                scenario_id=sc.scenario_id,
                scenario_name=sc.scenario_name,
                feature_title=sc.feature_name,
                file_path=sc.file_path,
                line_number=sc.line_number,
                cross_encoder_score=float(ce_score),
                rrf_score=float(meta.get("rrf_score", 0.0)),
                role=c_status,
                verifies=c_reason,
                evidence_steps=c_evidence,
                match_percentage=c_match,
                covered_criteria=c_covered,
            ))

        union = compute_union_coverage(
            requirement=requirement,
            evaluations=evals if isinstance(evals, list) else [],
            overall_summary=overall_summary,
            candidates=candidates,
        )

        overall_match = union["union_match_percentage"]
        overall_classification = union["overall_classification"]
        if overall_match == 0:
            best_candidate_obj = None

        suggested_intents = overall_summary.get(
            "suggested_test_intents",
            overall_summary.get("suggested_tests", []),
        )
        missing = union["missing_gaps"] or overall_summary.get("missing_gaps", [])
        covered = union["covered_criteria"] or overall_summary.get("covered_criteria", [])

        return RequirementJudgeVerdict(
            req_id=requirement.req_id,
            title=requirement.title,
            category=requirement.category,
            source_file=requirement.source_file,
            line_number=requirement.line_number,
            match_percentage=overall_match,
            overall_classification=overall_classification,
            reasoning=build_union_reasoning(union, citations),
            primary_citation=best_candidate_obj,
            citations=citations,
            covered_criteria=covered,
            missing_gaps=missing,
            suggested_tests=suggested_intents,
            coverage_map=union["coverage_map"],
            cached=False,
        )
