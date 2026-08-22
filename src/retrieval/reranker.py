"""Cross-Encoder Reranker for fine-grained relevance scoring."""

from typing import List, Tuple, Dict, Any, Union, Optional
import numpy as np
from src.parsers.gherkin_parser import ScenarioChunk


class CrossEncoderReranker:
    """Reranks candidate scenarios using a Cross-Encoder model."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: str = "cpu",
        top_k: int = 10,
    ):
        self.model_name = model_name
        self.device = device
        self.top_k = top_k
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name, device=self.device)
            except Exception as e:
                print(f"Warning: Could not load CrossEncoder '{self.model_name}': {e}. Using token-overlap fallback scoring.")
                self._model = "FALLBACK"

    def rerank(
        self,
        query: str,
        candidates: List[Tuple[ScenarioChunk, float, Dict[str, Any]]],
        top_n: int = 10,
        top_k: Optional[int] = None,
    ) -> List[Tuple[ScenarioChunk, float, Dict[str, Any]]]:
        """
        Reranks a list of candidate scenarios against the query text.

        Args:
            query: Requirement text or query
            candidates: List of (ScenarioChunk, rrf_score, rrf_details) from RRF
            top_n: Number of top candidates to return (default 10)
            top_k: Optional alias for top_n

        Returns:
            List of (ScenarioChunk, rerank_score, full_metadata) sorted descending.
        """
        limit = top_k if top_k is not None else top_n
        if not candidates:
            return []

        self._load_model()
        
        # Build sub-query candidates for long requirement descriptions to avoid passage dilution
        sub_queries = [query]
        words = query.split()
        if len(words) > 25:
            import re
            lines = [l.strip() for l in query.splitlines() if len(l.strip().split()) >= 3]
            sub_queries.extend(lines[:8])
            sentences = [s.strip() for s in re.split(r"[.!?\n]+", query) if len(s.strip().split()) >= 4]
            sub_queries.extend(sentences[:8])

        pairs = []
        pair_mapping = []
        for idx, c in enumerate(candidates):
            sc_text = c[0].full_text
            for sq in sub_queries:
                pairs.append([sq, sc_text])
                pair_mapping.append(idx)

        if self._model != "FALLBACK":
            try:
                raw_scores = self._model.predict(pairs, show_progress_bar=False)
                max_scores = {idx: -999.0 for idx in range(len(candidates))}
                for s_val, idx in zip(raw_scores, pair_mapping):
                    if float(s_val) > max_scores[idx]:
                        max_scores[idx] = float(s_val)
                scores = [max_scores[idx] for idx in range(len(candidates))]
            except Exception as e:
                print(f"Warning: Cross-encoder prediction failed ({e}). Falling back.")
                scores = self._fallback_score(query, [c[0] for c in candidates])
        else:
            scores = self._fallback_score(query, [c[0] for c in candidates])

        # Combine with RRF information
        scored_candidates = []
        for idx, (scenario, rrf_score, rrf_meta) in enumerate(candidates):
            ce_score = float(scores[idx])
            meta = dict(rrf_meta)
            meta["rrf_score"] = rrf_score
            meta["cross_encoder_score"] = ce_score
            scored_candidates.append((scenario, ce_score, meta))

        # Sort descending by cross-encoder score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        return scored_candidates[:limit]

    def _fallback_score(self, query: str, scenarios: List[ScenarioChunk]) -> List[float]:
        """Heuristic lexical overlap scoring if cross-encoder model is unavailable."""
        q_tokens = set(query.lower().split())
        scores = []
        for s in scenarios:
            s_tokens = set(s.full_text.lower().split())
            overlap = len(q_tokens.intersection(s_tokens))
            score = overlap / (len(q_tokens) or 1.0)
            scores.append(score)
        return scores
