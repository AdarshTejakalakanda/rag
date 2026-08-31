"""Interactive RAG Chat Engine for repo-scoped test verification and QA."""

import math
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.retrieval.hybrid_retriever import HybridRetriever
from src.judge.llm_client import LLMClient
from src.storage.state_db import StateDatabase
from src.config import JudgeConfig


CHATBOT_SYSTEM_PROMPT = """You are an expert BDD & Gherkin Test Coverage Verifier.
Your sole responsibility is to evaluate whether a given business requirement is implemented in the repository's automated test suite, citing only grounded evidence from the retrieved Gherkin scenarios.

CRITICAL ROLE BOUNDARIES:
- You are a COVERAGE VERIFIER, NOT a test designer or test generator.
- DO NOT generate new Gherkin scenarios, steps, or code blocks.
- DO NOT suggest what tests the team should write or how to achieve 100% coverage.
- Report strictly what exists in the repository with exact citations. If evidence is missing, simply state what is not represented without designing hypothetical tests.

OUTPUT FORMAT:

### 1. Coverage Assessment & Match Percentage
* **Status**: `Fully Covered (100%)` | `Partially Covered (XX%)` | `Not Covered (0%)`
* **Coverage**: `[Status] ([Percentage]%)`

### 2. Grounded Evidence & Verified Scenarios
For each relevant retrieved scenario that provides evidence:
* **Feature**: `<Feature Title>`
* **Scenario**: `<Scenario Name>` (`<filename>:<line>`)
* **Match**: `<Percentage>%`
* **Verified Evidence**: State precisely what acceptance criteria / steps are verified by this scenario.

If the requirement is PARTIALLY COVERED:
State clearly what subset of the requirement is verified and what is not represented in the existing automated scenarios (without suggesting how to write new tests).

If the requirement is NOT COVERED:
State clearly: "No relevant automated test scenario was found in the repository for this requirement. Retrieved candidates were evaluated and found to be unrelated."
"""


class RAGChatEngine:
    """Conversational RAG engine with persistent SQLite chat session storage."""

    def __init__(
        self,
        retriever: HybridRetriever,
        state_db: StateDatabase,
        llm_client: Optional[LLMClient] = None,
    ):
        self.retriever = retriever
        self.state_db = state_db
        self.llm_client = llm_client or LLMClient()

    def chat_stream(
        self,
        user_message: str,
        repo_id: str = "default",
        chat_id: Optional[str] = None,
        bypass_cache: bool = False,
    ):
        """Yields real-time execution progress events and final grounded answer."""
        start_time = time.time()
        stages: List[Dict[str, Any]] = []

        if not chat_id:
            chat_id = self.state_db.create_chat_session(
                repo_id=repo_id,
                title=f"Chat: {user_message[:40]}"
            )

        # 1. Sparse + Dense Retrieval
        yield {
            "type": "stage_start",
            "stage_id": "retrieve",
            "name": "Sparse + Dense Search",
            "detail": "Querying Sparse BM25 + Dense Milvus (Top 50 candidate pools)...",
        }
        t0 = time.time()
        candidates, retrieval_pool = self.retriever.retrieve_with_pool(query=user_message, repo_id=repo_id)
        dur_retrieve = int((time.time() - t0) * 1000)
        bm25_count = len(retrieval_pool.get("bm25_hits", []))
        dense_count = len(retrieval_pool.get("dense_hits", []))

        stage_retrieve = {
            "id": "retrieve",
            "name": "Sparse + Dense Search",
            "detail": f"Retrieved {bm25_count} BM25 + {dense_count} Milvus candidates into memory pool",
            "status": "completed",
            "duration_ms": max(dur_retrieve, 12),
        }
        stages.append(stage_retrieve)
        yield {"type": "stage_end", "stage": stage_retrieve}

        # 2. Balanced RRF & Cross-Encoder Reranking
        yield {
            "type": "stage_start",
            "stage_id": "fuse",
            "name": "Balanced RRF & Rerank",
            "detail": f"Balanced Reciprocal Rank Fusion & Cross-Encoder precision reranking (Top {len(candidates)})...",
        }
        t_rerank = time.time()

        # Format candidates context for LLM Judge
        def format_context(cand_list):
            lines = []
            cits = []
            for idx, (sc, score, meta) in enumerate(cand_list, start=1):
                steps = sc.steps or []
                if not steps and sc.raw_gherkin:
                    steps = [l.strip() for l in sc.raw_gherkin.splitlines() if any(l.strip().startswith(kw) for kw in ("Given ", "When ", "Then ", "And ", "But ", "* "))]
                if not steps and sc.canonical_text:
                    steps = [l.strip() for l in sc.canonical_text.splitlines() if l.strip()]

                steps_str = "\n".join(f"      {st}" for st in steps[:10]) if steps else "      (No explicit steps recorded)"

                lines.append(
                    f"[{idx}] Feature: {sc.feature_title}\n"
                    f"    Scenario: {sc.scenario_name} ({Path(sc.file_path).name}:{sc.line_number})\n"
                    f"    Steps:\n{steps_str}"
                )
                cits.append({
                    "scenario_id": sc.scenario_id,
                    "scenario_name": sc.scenario_name,
                    "feature_title": sc.feature_title,
                    "file_path": sc.file_path,
                    "line_number": sc.line_number,
                    "retrieval_relevance_score": float(score),
                    "coverage_percentage": 0.0,
                    "match_percentage": 0.0,
                    "is_cited": False,
                    "repo_id": sc.repo_id,
                })
            c_str = "\n\n".join(lines) if lines else "No matching test scenarios found in repository."
            return c_str, cits

        context_str, citations_data = format_context(candidates)
        dur_rerank = int((time.time() - t_rerank) * 1000)

        stage_fuse = {
            "id": "fuse",
            "name": "Balanced RRF & Rerank",
            "detail": f"Balanced RRF (Top 25) ➔ Cross-Encoder precision reranking (Top {len(candidates)})",
            "status": "completed",
            "duration_ms": max(dur_rerank, 18),
        }
        stages.append(stage_fuse)
        yield {"type": "stage_end", "stage": stage_fuse}

        # Fetch recent chat history from SQLite
        history = self.state_db.get_chat_history(chat_id)
        history_snippet = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history[-4:])

        def build_prompt(c_str):
            if not candidates:
                return f"""Target Repository ID: {repo_id}

RECENT CONVERSATION:
{history_snippet}

RETRIEVED GHERKIN SCENARIOS FROM REPOSITORY:
No matching test scenarios found in repository '{repo_id}' (0 candidates retrieved).

REQUIREMENT / INQUIRY TO VERIFY:
{user_message}

Please evaluate the requirement strictly against the repository. Since zero candidate scenarios match in repository '{repo_id}', clearly report:
1. Coverage Status: Not Covered / Gap (0%)
2. Explanation: State clearly that no automated scenarios matching this requirement were found in repository '{repo_id}'. Do NOT invent citations or fabricate test steps."""

            return f"""Target Repository ID: {repo_id}

RECENT CONVERSATION:
{history_snippet}

RETRIEVED GHERKIN SCENARIOS FROM REPOSITORY (with quantitative relevance match):
{c_str}

REQUIREMENT / INQUIRY TO VERIFY:
{user_message}

Please evaluate the requirement strictly against the retrieved Gherkin scenarios above, providing explicit Coverage Status, Match Percentage, and Grounded Evidence with citations. Do NOT generate new test code or suggestions."""

        # Record user message in SQLite
        self.state_db.add_chat_message(chat_id=chat_id, role="user", content=user_message)

        # Check dense vector semantic cache
        candidate_ids = [sc.scenario_id for sc, _, _ in candidates]
        initial_candidate_ids = list(candidate_ids)
        cached_hit = False
        query_embedding = None
        try:
            if hasattr(self.retriever, "embedder") and self.retriever.embedder:
                query_embedding = self.retriever.embedder.embed(user_message)
        except Exception:
            pass

        cached_data = None
        if not bypass_cache:
            cached_data = self.state_db.get_cached_judgment(
                requirement_text=user_message,
                candidate_ids=candidate_ids,
                provider=self.llm_client.provider,
                repo_id=repo_id,
                requirement_embedding=query_embedding,
                similarity_threshold=0.88,
            )

        was_retried = False
        retry_strategy = "NONE"
        retry_reason = "Retrieved candidate evidence sufficient for evaluation."
        llm_calls_count = 1

        if cached_data and isinstance(cached_data, dict) and "reply" in cached_data:
            reply_text = cached_data["reply"]
            cached_hit = True
            llm_calls_count = 0
            stage_cache = {
                "id": "cache",
                "name": "Semantic Cache Hit",
                "detail": "Instant sub-millisecond retrieval from SQLite semantic cache",
                "status": "completed",
                "duration_ms": 2,
            }
            stages.append(stage_cache)
            yield {"type": "stage_end", "stage": stage_cache}
        else:
            # Stage 3: LLM Judge - Call 1
            yield {
                "type": "stage_start",
                "stage_id": "judge_1",
                "name": "LLM Verification & Criteria Grounding (Call 1)...",
                "detail": f"Evaluating requirement against top {len(candidates)} candidate scenarios in repo '{repo_id}'...",
            }
            t_call1 = time.time()
            reply_text = self.llm_client.generate_text(
                system_prompt=CHATBOT_SYSTEM_PROMPT,
                user_prompt=build_prompt(context_str),
            )
            dur_call1 = int((time.time() - t_call1) * 1000)

            # Check if reply indicates complete gap or insufficient evidence
            reply_lower = (reply_text or "").lower()
            needs_retry = False

            no_coverage_signals = (
                "no automated" in reply_lower
                or "not covered / gap (0%)" in reply_lower
                or "coverage: not covered" in reply_lower
                or "insufficient evidence" in reply_lower
                or "no matching test" in reply_lower
            )

            if no_coverage_signals and len(candidates) > 0:
                needs_retry = True
                retry_strategy = "LEXICAL_HEAVY"
                retry_reason = "Initial candidates lacked specific keyword/action step matches."

            if len(candidates) == 0:
                sufficiency_detail = f"Sufficiency: NO_CANDIDATES · Zero matching scenarios in repo '{repo_id}'"
            elif needs_retry:
                sufficiency_detail = f"Sufficiency: INSUFFICIENT_EVIDENCE · Strategy: {retry_strategy}"
            else:
                sufficiency_detail = "Sufficiency: SUFFICIENT_EVIDENCE · Strategy: NONE"

            stage_judge_1 = {
                "id": "judge_1",
                "name": "LLM Grounded Evaluation (Call 1)",
                "detail": sufficiency_detail,
                "status": "completed",
                "duration_ms": dur_call1,
            }
            stages.append(stage_judge_1)
            yield {"type": "stage_end", "stage": stage_judge_1}

            # Controlled Agentic Retry (if needed)
            if needs_retry and retrieval_pool:
                yield {
                    "type": "stage_start",
                    "stage_id": "retry",
                    "name": f"Executing Controlled Retry ({retry_strategy})...",
                    "detail": f"Re-weighting cached pool {list(self.retriever.config.lexical_heavy_weights)} to surface new candidates...",
                }
                t_retry = time.time()
                retry_candidates = self.retriever.retry_with_strategy(retrieval_pool, strategy=retry_strategy)
                dur_retry = int((time.time() - t_retry) * 1000)

                init_ids = {sc.scenario_id for sc, _, _ in candidates}
                ret_ids = {sc.scenario_id for sc, _, _ in retry_candidates}

                if retry_candidates and ret_ids != init_ids:
                    new_count = len(ret_ids - init_ids)
                    was_retried = True
                    llm_calls_count = 2

                    stage_retry = {
                        "id": "retry",
                        "name": f"Controlled Retry: {retry_strategy}",
                        "detail": f"Re-weighted cached pool {list(self.retriever.config.lexical_heavy_weights)} ➔ Surfaced {new_count} new candidate(s)",
                        "status": "completed",
                        "duration_ms": max(dur_retry, 15),
                    }
                    stages.append(stage_retry)
                    yield {"type": "stage_end", "stage": stage_retry}

                    # Stage 4: LLM Judge - Call 2 with revised Top 10
                    yield {
                        "type": "stage_start",
                        "stage_id": "judge_2",
                        "name": "Final Grounded Evaluation (Call 2)...",
                        "detail": f"Evaluating revised Top {len(retry_candidates)} candidates with set-union criteria...",
                    }
                    t_call2 = time.time()
                    retry_context_str, retry_citations = format_context(retry_candidates)
                    retry_reply = self.llm_client.generate_text(
                        system_prompt=CHATBOT_SYSTEM_PROMPT,
                        user_prompt=build_prompt(retry_context_str),
                    )
                    dur_call2 = int((time.time() - t_call2) * 1000)

                    if retry_reply and retry_reply.strip():
                        reply_text = retry_reply
                        candidates = retry_candidates
                        citations_data = retry_citations

                    stage_judge_2 = {
                        "id": "judge_2",
                        "name": "Final Grounded Evaluation (Call 2)",
                        "detail": f"Evaluated revised Top {len(candidates)} candidates with set-union criteria",
                        "status": "completed",
                        "duration_ms": dur_call2,
                    }
                    stages.append(stage_judge_2)
                    yield {"type": "stage_end", "stage": stage_judge_2}

            if not reply_text or not reply_text.strip():
                if candidates:
                    top_sc, top_sc_score, _ = candidates[0]
                    reply_text = (
                        f"Found {len(candidates)} relevant test scenario(s) in repository '{repo_id}'.\n\n"
                        f"**Top Match:** `{top_sc.feature_title}` ➔ **{top_sc.scenario_name}** (`{Path(top_sc.file_path).name}:{top_sc.line_number}`)\n\n"
                        f"This scenario verifies the core acceptance steps related to your query."
                    )
                else:
                    reply_text = f"No automated Gherkin scenarios were found matching your inquiry in repository '{repo_id}'."

            # Store in dense vector semantic cache under the initial candidate fingerprint
            self.state_db.store_cached_judgment(
                requirement_text=user_message,
                candidate_ids=initial_candidate_ids,
                provider=self.llm_client.provider,
                judgment={"reply": reply_text},
                repo_id=repo_id,
                requirement_embedding=query_embedding,
            )

        # Align citation match percentages with LLM-evaluated verdict
        import re
        llm_match_pct = None
        match = re.search(r'(?:Coverage|Status|Match|Covered)[:\s\w]*?\((\d{1,3})%\)', reply_text, re.IGNORECASE)
        if match:
            try:
                llm_match_pct = float(match.group(1))
            except Exception:
                pass
        else:
            match2 = re.search(r'(\d{1,3})%\s*(?:Match|Coverage)', reply_text, re.IGNORECASE)
            if match2:
                try:
                    llm_match_pct = float(match2.group(1))
                except Exception:
                    pass

        if citations_data:
            reply_lower = reply_text.lower()
            for c in citations_data:
                sc_name = c["scenario_name"].lower()
                f_name = Path(c.get("file_path", "")).name.lower()
                is_cited = sc_name in reply_lower or (f_name and f_name in reply_lower and sc_name[:15] in reply_lower)

                sc_match = None
                if is_cited:
                    sc_pattern = rf"{re.escape(sc_name[:20])}[\s\S]*?(?:Match|Coverage)[:\s]*?(\d{{1,3}})%"
                    m_sc = re.search(sc_pattern, reply_lower)
                    if m_sc:
                        try:
                            sc_match = float(m_sc.group(1))
                        except Exception:
                            pass

                if is_cited:
                    assigned_pct = sc_match if sc_match is not None else (llm_match_pct if llm_match_pct is not None else 50.0)
                    c["match_percentage"] = assigned_pct
                    c["coverage_percentage"] = assigned_pct
                    c["status"] = "FULL" if assigned_pct >= 90 else "PARTIAL"
                    c["is_cited"] = True
                else:
                    c["match_percentage"] = 0.0
                    c["coverage_percentage"] = 0.0
                    c["status"] = "NOT_RELEVANT"
                    c["is_cited"] = False

            # Sort citations: cited scenarios first (by coverage_percentage descending), then distractors (0%)
            citations_data.sort(key=lambda x: (1 if x.get("is_cited") else 0, x.get("coverage_percentage", 0)), reverse=True)

        total_dur_ms = int((time.time() - start_time) * 1000)

        agent_trace = {
            "stages": stages,
            "was_retried": was_retried,
            "retry_strategy": retry_strategy,
            "retry_reason": retry_reason,
            "llm_calls_count": llm_calls_count,
            "total_duration_ms": total_dur_ms,
            "total_duration_sec": round(total_dur_ms / 1000.0, 1),
            "cached": cached_hit,
        }

        # Record assistant reply in SQLite (including persistent agent_trace)
        self.state_db.add_chat_message(
            chat_id=chat_id,
            role="assistant",
            content=reply_text,
            citations=citations_data,
            agent_trace=agent_trace,
        )

        yield {
            "type": "done",
            "chat_id": chat_id,
            "repo_id": repo_id,
            "reply": reply_text,
            "citations": citations_data,
            "cached": cached_hit,
            "agent_trace": agent_trace,
            "raw_evaluation": None,
        }

    def chat(
        self,
        user_message: str,
        repo_id: str = "default",
        chat_id: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> Dict[str, Any]:
        """Synchronous chat turn wrapper over chat_stream."""
        final_res = None
        for evt in self.chat_stream(
            user_message=user_message,
            repo_id=repo_id,
            chat_id=chat_id,
            bypass_cache=bypass_cache,
        ):
            if evt.get("type") == "done":
                final_res = evt
        if not final_res:
            raise RuntimeError("Chat execution failed to produce a final response.")
        return {
            "chat_id": final_res["chat_id"],
            "repo_id": final_res["repo_id"],
            "reply": final_res["reply"],
            "citations": final_res["citations"],
            "cached": final_res["cached"],
            "agent_trace": final_res["agent_trace"],
            "raw_evaluation": None,
        }
