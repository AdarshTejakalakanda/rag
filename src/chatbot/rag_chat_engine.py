"""Interactive RAG Chat Engine for repo-scoped test verification and QA."""

import math
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.retrieval.hybrid_retriever import HybridRetriever
from src.judge.llm_client import LLMClient
from src.storage.state_db import StateDatabase
from src.config import JudgeConfig


CHATBOT_SYSTEM_PROMPT = """You are an expert BDD & Gherkin Test Coverage Assistant.
You help engineers and product managers analyze requirement coverage against automated Gherkin (.feature) test suites in a specific repository.

Always structure your answer clearly with:
1. **Coverage Assessment & Match Percentage**:
   - Status: `Covered (100%)` | `Partially Covered (XX%)` | `Not Covered / Gap (0%)`
   - Explicitly include the estimated Coverage Match Percentage (e.g. `Coverage: Partially Covered (75%)` or `Coverage: Fully Covered (100%)`).
2. **Analysis & Grounded Evidence**:
   - Reference the retrieved Gherkin scenarios with Feature title, Scenario name, and File:Line citations.
   - Explain what specific acceptance criteria are verified.
3. **Identified Test Gaps & Concrete Gherkin Steps**:
   - If coverage is < 100%, provide ready-to-use Gherkin `Scenario:` examples to fill the missing gaps.
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

    def chat(
        self,
        user_message: str,
        repo_id: str = "default",
        chat_id: Optional[str] = None,
    ):
        """Processes a chat turn scoped to a repository and persists history in SQLite."""
        if not chat_id:
            chat_id = self.state_db.create_chat_session(
                repo_id=repo_id,
                title=f"Chat: {user_message[:40]}"
            )

        # 1. Retrieve Top 10 Scenarios from the selected repository
        candidates = self.retriever.retrieve(query=user_message, repo_id=repo_id)

        # Format candidates context with calibrated match percentages
        context_lines = []
        citations_data = []
        for idx, (sc, score, meta) in enumerate(candidates, start=1):
            try:
                score_val = float(score)
                # Sigmoid with calibration for ms-marco cross-encoder logits
                match_pct = round((1.0 / (1.0 + math.exp(-score_val))) * 100.0, 1)
            except Exception:
                match_pct = 50.0

            context_lines.append(
                f"[{idx}] Feature: {sc.feature_title}\n"
                f"    Scenario: {sc.scenario_name} ({Path(sc.file_path).name}:{sc.line_number})\n"
                f"    Relevance Match: {match_pct}%\n"
                f"    Steps:\n" + "\n".join(f"      {st}" for st in sc.steps[:5])
            )
            citations_data.append({
                "scenario_id": sc.scenario_id,
                "scenario_name": sc.scenario_name,
                "feature_title": sc.feature_title,
                "file_path": sc.file_path,
                "line_number": sc.line_number,
                "score": float(score),
                "match_percentage": match_pct,
                "repo_id": sc.repo_id,
            })

        context_str = "\n\n".join(context_lines) if context_lines else "No matching test scenarios found in repository."

        # Fetch recent chat history from SQLite
        history = self.state_db.get_chat_history(chat_id)
        history_snippet = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history[-4:])

        user_prompt = f"""Target Repository ID: {repo_id}

RECENT CONVERSATION:
{history_snippet}

RETRIEVED GHERKIN SCENARIOS FROM REPOSITORY (with quantitative relevance match):
{context_str}

USER QUESTION / REQUIREMENT:
{user_message}

Please provide a helpful, concise answer based on the retrieved scenarios above, including explicit Coverage Status & Match Percentage (e.g. 'Coverage: Partially Covered (75%)'):"""

        # Record user message in SQLite
        self.state_db.add_chat_message(chat_id=chat_id, role="user", content=user_message)

        # Check dense vector semantic cache (with cosine similarity search)
        candidate_ids = [sc.scenario_id for sc, _, _ in candidates]
        cached_hit = False
        query_embedding = None
        try:
            if hasattr(self.retriever, "embedder") and self.retriever.embedder:
                query_embedding = self.retriever.embedder.embed(user_message)
        except Exception:
            pass

        cached_data = self.state_db.get_cached_judgment(
            requirement_text=user_message,
            candidate_ids=candidate_ids,
            provider=self.llm_client.provider,
            repo_id=repo_id,
            requirement_embedding=query_embedding,
            similarity_threshold=0.88,
        )

        if cached_data and isinstance(cached_data, dict) and "reply" in cached_data:
            reply_text = cached_data["reply"]
            cached_hit = True
        else:
            # Generate response
            reply_text = self.llm_client.generate_text(
                system_prompt=CHATBOT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )

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

            # Store in dense vector semantic cache
            self.state_db.store_cached_judgment(
                requirement_text=user_message,
                candidate_ids=candidate_ids,
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

        if llm_match_pct is not None and citations_data:
            for idx, c in enumerate(citations_data):
                # If scenario is specifically cited/analyzed in the LLM response, reflect the LLM's grounded percentage
                if c["scenario_name"].lower() in reply_text.lower() or idx == 0:
                    c["match_percentage"] = llm_match_pct

        # Record assistant reply in SQLite
        self.state_db.add_chat_message(
            chat_id=chat_id,
            role="assistant",
            content=reply_text,
            citations=citations_data,
        )

        return {
            "chat_id": chat_id,
            "repo_id": repo_id,
            "reply": reply_text,
            "citations": citations_data,
            "cached": cached_hit,
            "raw_evaluation": None,
        }
