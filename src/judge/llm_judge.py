"""Batch LLM Judge executing ONE single call per requirement.

Per-scenario scores stay independent. Requirement-level score is the UNION of
acceptance criteria across files (connect-the-dots), without forcing 100/0.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Any, Optional, Set
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


def extract_indexed_criteria(requirement: RequirementChunk) -> Tuple[Dict[str, str], str, str]:
    """
    Extracts all acceptance criteria and business rules and assigns deterministic IDs:
    AC-1, AC-2, ... for Acceptance Criteria
    BR-1, BR-2, ... for Business Rules
    Returns:
      criterion_id_map: Dict[str, str] mapping ID -> text
      ac_formatted_string: e.g. "[AC-1] text\n[AC-2] text"
      br_formatted_string: e.g. "[BR-1] text"
    """
    id_map: Dict[str, str] = {}
    ac_lines = []
    br_lines = []

    # Process Acceptance Criteria
    ac_idx = 1
    for raw in requirement.acceptance_criteria or []:
        text = (raw or "").strip()
        if not text or text.lower() in ("none specified", "none", "n/a"):
            continue
        cleaned = re.sub(r"^\[(?:AC|BR|REQ)?\s*\d*\]\s*", "", text, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^[-*•\d.]+\s*", "", cleaned).strip()
        if not cleaned:
            continue
        cid = f"AC-{ac_idx}"
        id_map[cid] = cleaned
        ac_lines.append(f"[{cid}] {cleaned}")
        ac_idx += 1

    # Process Business Rules
    br_idx = 1
    for raw in requirement.business_rules or []:
        text = (raw or "").strip()
        if not text or text.lower() in ("none specified", "none", "n/a"):
            continue
        cleaned = re.sub(r"^\[(?:AC|BR|REQ)?\s*\d*\]\s*", "", text, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^[-*•\d.]+\s*", "", cleaned).strip()
        if not cleaned:
            continue
        cid = f"BR-{br_idx}"
        id_map[cid] = cleaned
        br_lines.append(f"[{cid}] {cleaned}")
        br_idx += 1

    # Fallback if neither AC nor BR are explicitly defined
    if not id_map:
        fallback_text = (requirement.description or requirement.title or "").strip()
        if fallback_text:
            cid = "AC-1"
            id_map[cid] = fallback_text
            ac_lines.append(f"[{cid}] {fallback_text}")

    ac_str = "\n".join(ac_lines) if ac_lines else "None specified"
    br_str = "\n".join(br_lines) if br_lines else "None specified"
    return id_map, ac_str, br_str


def canonical_requirement_criteria(requirement: RequirementChunk) -> List[str]:
    """Acceptance criteria plus business rules, skipping empty / placeholder rows."""
    id_map, _, _ = extract_indexed_criteria(requirement)
    return list(id_map.values())


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


def resolve_criterion_ids(raw_items: List[Any], id_map: Dict[str, str]) -> Set[str]:
    """
    Resolves raw LLM criterion references into canonical criterion IDs (AC-1, BR-1, etc.).
    Supports exact ID keys, bracketed tokens ([AC-1]), prefix strings (AC-1: ...), or text matching.
    """
    resolved = set()
    for item in raw_items or []:
        if not item:
            continue
        s = str(item).strip()

        # 1. Exact ID key match
        if s.upper() in id_map:
            resolved.add(s.upper())
            continue

        # 2. Regex token extraction for [AC-1] or AC-1 or BR-1
        matched_tokens = re.findall(r"\b(AC-\d+|BR-\d+|REQ-\d+)\b", s, re.IGNORECASE)
        for token in matched_tokens:
            token_upper = token.upper()
            if token_upper in id_map:
                resolved.add(token_upper)

        # 3. If no ID token found, fallback to similarity match against id_map values
        if not matched_tokens:
            mapped_text = map_to_canonical(s, list(id_map.values()))
            if mapped_text:
                for cid, text in id_map.items():
                    if text == mapped_text:
                        resolved.add(cid)
                        break

    return resolved


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
    criterion_id_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Deterministic union aggregator:
    - LLM provides per-scenario semantic evidence and criterion IDs.
    - Python aggregator owns set union, exact percentage arithmetic, 0% clamp,
      status classification (FULLY_COVERED, PARTIALLY_COVERED, NOT_COVERED), and citations.
    """
    if criterion_id_map is None:
        criterion_id_map, _, _ = extract_indexed_criteria(requirement)

    total_count = len(criterion_id_map)
    all_criterion_ids = list(criterion_id_map.keys())

    candidate_evals = [e for e in evaluations if isinstance(e, dict)]

    # Collect per-candidate covered criteria IDs
    cand_covered_map: Dict[str, Set[str]] = {}
    valid_candidate_count = 0

    for ev in candidate_evals:
        sc_id = ev.get("scenario_id") or ev.get("document_id") or "unknown"
        status = (ev.get("status") or "").strip().upper()

        # Check if candidate is NOT_RELEVANT
        if status in ("NOT_RELEVANT", "NOT_COVERED", "IRRELEVANT"):
            cand_covered_map[sc_id] = set()
            continue

        raw_covered = ev.get("covered_criteria") or []
        covered_ids = resolve_criterion_ids(raw_covered, criterion_id_map)

        # Guardrail: if status was marked FULLY/PARTIALLY but no valid criteria IDs resolved:
        if not covered_ids and int(ev.get("match_percentage", 0) or 0) <= 0:
            cand_covered_map[sc_id] = set()
            continue

        cand_covered_map[sc_id] = covered_ids
        if covered_ids:
            valid_candidate_count += 1

    # Mathematical Set Union across all valid candidates
    union_covered_ids: Set[str] = set()
    for c_ids in cand_covered_map.values():
        union_covered_ids.update(c_ids)

    # Deterministic 0% Clamp:
    # If all candidates are NOT_RELEVANT or union has 0 covered criteria -> strictly 0% NOT_COVERED
    if total_count == 0 or not union_covered_ids or valid_candidate_count == 0:
        union_pct = 0
        overall_classification = "NOT_COVERED"
        union_covered_texts = []
        missing_gap_texts = [criterion_id_map[cid] for cid in all_criterion_ids]
    else:
        covered_n = len(union_covered_ids)
        union_pct = int(round((covered_n / total_count) * 100))
        union_pct = max(0, min(100, union_pct))

        if union_pct >= 100:
            overall_classification = "FULLY_COVERED"
        elif union_pct > 0:
            overall_classification = "PARTIALLY_COVERED"
        else:
            overall_classification = "NOT_COVERED"

        # Deterministically order covered and missing lists
        union_covered_texts = [criterion_id_map[cid] for cid in all_criterion_ids if cid in union_covered_ids]
        missing_gap_texts = [criterion_id_map[cid] for cid in all_criterion_ids if cid not in union_covered_ids]

    # Build coverage map
    cand_by_id = {}
    if candidates:
        for sc, _, _ in candidates:
            cand_by_id[sc.scenario_id] = sc

    coverage_map: List[Dict[str, Any]] = []
    for ev in candidate_evals:
        sc_id = ev.get("scenario_id") or ev.get("document_id") or "unknown"
        c_ids = cand_covered_map.get(sc_id, set())
        if not c_ids:
            continue
        sc_obj = cand_by_id.get(sc_id)
        coverage_map.append({
            "scenario_id": sc_id,
            "scenario_name": sc_obj.scenario_name if sc_obj else ev.get("scenario_name", ""),
            "file_path": sc_obj.file_path if sc_obj else ev.get("file_path", ""),
            "covered_criteria": [criterion_id_map[cid] for cid in all_criterion_ids if cid in c_ids],
            "evidence": ev.get("evidence", []),
            "reasoning": ev.get("reasoning", ""),
        })

    narrative = (
        (overall_summary or {}).get("connecting_narrative")
        or (overall_summary or {}).get("reasoning_summary")
        or (overall_summary or {}).get("union_reasoning")
        or ""
    ).strip()

    return {
        "union_match_percentage": union_pct,
        "overall_classification": overall_classification,
        "covered_criteria": union_covered_texts,
        "missing_gaps": missing_gap_texts,
        "coverage_map": coverage_map,
        "connecting_narrative": narrative,
        "covered_count": len(union_covered_ids),
        "total_criteria": total_count,
        "cand_covered_map": cand_covered_map,
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
    """Judge verdict for a single business requirement with agentic retrieval telemetry."""
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
    retrieval_decision: str = "SUFFICIENT_EVIDENCE"  # "SUFFICIENT_EVIDENCE" | "INSUFFICIENT_EVIDENCE"
    retry_strategy: str = "NONE"  # "NONE" | "LEXICAL_HEAVY" | "DENSE_HEAVY"
    retry_reason: str = ""
    was_retried: bool = False
    llm_calls_count: int = 1

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
            "retrieval_decision": self.retrieval_decision,
            "retry_strategy": self.retry_strategy,
            "retry_reason": self.retry_reason,
            "was_retried": self.was_retried,
            "llm_calls_count": self.llm_calls_count,
        }


class LLMJudge:
    """Evaluates requirement against candidate scenarios with Agentic Retrieval-Sufficiency & Controlled Retry."""

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
        retrieval_pool: Optional[Dict[str, Any]] = None,
        retriever: Optional[Any] = None,
    ) -> RequirementJudgeVerdict:
        """
        Evaluates candidate scenarios against requirement. If retrieval_pool and retriever
        are provided, transparently performs Agentic Retrieval Sufficiency check & Controlled Retry.
        """
        if retrieval_pool is not None and retriever is not None:
            return self.judge_requirement_agentic(
                requirement=requirement,
                candidates=candidates,
                retrieval_pool=retrieval_pool,
                retriever=retriever,
                repo_id=repo_id,
                bypass_cache=bypass_cache,
            )

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
                retrieval_decision="INSUFFICIENT_EVIDENCE",
                retry_strategy="NONE",
                retry_reason="No test scenarios found in repository.",
                was_retried=False,
                llm_calls_count=0,
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
        criterion_id_map, ac_str, rules_str = extract_indexed_criteria(requirement)

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

        return self._build_verdict_from_response(requirement, candidates, response_dict, criterion_id_map)

    def judge_requirement_agentic(
        self,
        requirement: RequirementChunk,
        candidates: List[Tuple[ScenarioChunk, float, Dict[str, Any]]],
        retrieval_pool: Optional[Dict[str, Any]] = None,
        retriever: Optional[Any] = None,
        repo_id: str = "default",
        bypass_cache: bool = False,
    ) -> RequirementJudgeVerdict:
        """
        Executes Agentic Retrieval-Sufficiency check and One Controlled Retry:
        • Call 1: Evaluates initial Top 10 candidates.
        • If LLM determines evidence is INSUFFICIENT with LEXICAL_HEAVY or DENSE_HEAVY:
            -> Executes ONE controlled retry using Weighted RRF on cached Top 50 pool -> Cross-Encoder -> New Top 10.
            -> If new candidates are surfaced: Call 2 evaluates the new candidate set for final coverage.
        • Total LLM calls: exactly 1 (normal) or 2 (retry).
        """
        # Step 1: Execute Call 1 (Coverage & Retrieval Sufficiency)
        verdict = self.judge_requirement(
            requirement=requirement,
            candidates=candidates,
            repo_id=repo_id,
            bypass_cache=bypass_cache,
            retrieval_pool=None,
            retriever=None,
        )

        # Step 2: Branch on Retrieval Sufficiency Decision
        if (
            verdict.retrieval_decision == "INSUFFICIENT_EVIDENCE"
            and verdict.retry_strategy in ("LEXICAL_HEAVY", "DENSE_HEAVY")
            and retrieval_pool is not None
            and retriever is not None
            and not verdict.cached
        ):
            strategy = verdict.retry_strategy
            reason = verdict.retry_reason
            print(f"[Agentic RAG] Sufficiency check failed for [{requirement.req_id}]. Triggering controlled retry: '{strategy}'. Reason: {reason}")

            # Re-fuse already retrieved Top 50 lists with Weighted RRF (Zero extra DB queries)
            retry_candidates = retriever.retry_with_strategy(retrieval_pool, strategy=strategy)

            initial_ids = {sc.scenario_id for sc, _, _ in candidates}
            retry_ids = {sc.scenario_id for sc, _, _ in retry_candidates}

            if retry_candidates and retry_ids != initial_ids:
                new_count = len(retry_ids - initial_ids)
                print(f"[Agentic RAG] Weighted RRF surfaced {new_count} new candidate(s). Executing Call 2 (Final Judge)...")
                retry_verdict = self.judge_requirement(
                    requirement=requirement,
                    candidates=retry_candidates,
                    repo_id=repo_id,
                    bypass_cache=True,
                    retrieval_pool=None,
                    retriever=None,
                )
                retry_verdict.was_retried = True
                retry_verdict.retry_strategy = strategy
                retry_verdict.retry_reason = reason
                retry_verdict.llm_calls_count = 2
                return retry_verdict

        return verdict

    def _build_verdict_from_response(
        self,
        requirement: RequirementChunk,
        candidates: List[Tuple[ScenarioChunk, float, Dict[str, Any]]],
        data: Dict[str, Any],
        criterion_id_map: Optional[Dict[str, str]] = None,
    ) -> RequirementJudgeVerdict:
        if criterion_id_map is None:
            criterion_id_map, _, _ = extract_indexed_criteria(requirement)

        evals = data.get("evaluations", [])
        overall_summary = data.get("overall_summary", {}) or {}
        rs_raw = data.get("retrieval_sufficiency")
        rs_data = rs_raw if isinstance(rs_raw, dict) else {}

        retrieval_decision = str(rs_data.get("decision") or "SUFFICIENT_EVIDENCE").strip().upper()
        if retrieval_decision not in ("SUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"):
            retrieval_decision = "SUFFICIENT_EVIDENCE"

        retry_strategy = str(rs_data.get("retry_strategy") or "NONE").strip().upper()
        if retry_strategy not in ("NONE", "LEXICAL_HEAVY", "DENSE_HEAVY"):
            retry_strategy = "NONE"

        retry_reason = str(rs_data.get("reason") or "").strip()

        eval_map = {(e.get("scenario_id") or e.get("document_id")): e for e in evals if isinstance(e, dict)}

        union = compute_union_coverage(
            requirement=requirement,
            evaluations=evals if isinstance(evals, list) else [],
            overall_summary=overall_summary,
            candidates=candidates,
            criterion_id_map=criterion_id_map,
        )

        cand_covered_map = union.get("cand_covered_map", {})
        total_crit = union.get("total_criteria", 0)

        citations: List[ScenarioCitation] = []
        max_candidate_match = 0
        best_candidate_obj = None

        for sc, ce_score, meta in candidates:
            e_data = eval_map.get(sc.scenario_id, {})
            c_covered_ids = cand_covered_map.get(sc.scenario_id, set())

            if total_crit > 0 and c_covered_ids:
                c_match = int(round((len(c_covered_ids) / total_crit) * 100))
                c_match = max(0, min(100, c_match))
            else:
                c_match = 0

            if c_match >= 100:
                c_status = "FULLY_COVERED"
            elif c_match > 0:
                c_status = "PARTIALLY_COVERED"
            else:
                c_status = "NOT_RELEVANT"

            c_reason = e_data.get("reasoning", "")
            c_covered = [criterion_id_map[cid] for cid in criterion_id_map if cid in c_covered_ids]

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

        overall_match = union["union_match_percentage"]
        overall_classification = union["overall_classification"]
        if overall_match == 0:
            best_candidate_obj = None

        suggested_intents = overall_summary.get(
            "suggested_test_intents",
            overall_summary.get("suggested_tests", []),
        )
        missing = union["missing_gaps"]
        covered = union["covered_criteria"]

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
            retrieval_decision=retrieval_decision,
            retry_strategy=retry_strategy,
            retry_reason=retry_reason,
            was_retried=False,
            llm_calls_count=1,
        )
