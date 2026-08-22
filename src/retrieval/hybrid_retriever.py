"""Hybrid Retriever combining BM25, Lean Milvus Dense Search, SQLite Hydration, RRF, and Cross-Encoder."""

from typing import List, Tuple, Dict, Any, Optional
from src.parsers.gherkin_parser import ScenarioChunk
from src.parsers.requirement_parser import RequirementChunk
from src.index.bm25_index import BM25Index
from src.index.milvus_store import MilvusStore
from src.index.embedding_model import EmbeddingModel
from src.retrieval.rrf_fusion import RRFFusion
from src.retrieval.reranker import CrossEncoderReranker
from src.storage.state_db import StateDatabase
from src.config import RetrievalConfig


class HybridRetriever:
    """Orchestrates BM25 + Dense Search, hydrates full Gherkin from SQLite, fuses with RRF, and reranks with Cross-Encoder."""

    def __init__(
        self,
        bm25_index: BM25Index,
        milvus_store: MilvusStore,
        embedding_model: EmbeddingModel,
        reranker: CrossEncoderReranker,
        config: Optional[RetrievalConfig] = None,
        state_db: Optional[StateDatabase] = None,
    ):
        self.bm25 = bm25_index
        self.milvus = milvus_store
        self.embedder = embedding_model
        self.reranker = reranker
        self.config = config or RetrievalConfig()
        self.state_db = state_db

    def retrieve(
        self,
        query: str or RequirementChunk,
        repo_id: Optional[str] = None,
    ) -> List[Tuple[ScenarioChunk, float, Dict[str, Any]]]:
        """
        Executes hybrid retrieval:
        1. BM25 Lexical Search (Top 20)
        2. Milvus Dense Vector Search (Top 20) -> scenario_ids
        3. Hydrates full ScenarioChunk (raw Gherkin + canonical text) from SQLite by scenario_id
        4. Reciprocal Rank Fusion (RRF Top 20)
        5. Cross-Encoder Re-ranking (Top 10)
        """
        if isinstance(query, RequirementChunk):
            query_text = query.full_text or f"{query.title}\n{query.description}"
        else:
            query_text = str(query)

        # 1. BM25 Lexical Retrieval
        bm25_hits = self.bm25.search(query_text, top_k=self.config.bm25_top_k, repo_id=repo_id)

        # 2. Dense Vector Retrieval
        q_vec = self.embedder.encode_query(query_text)
        milvus_hits = self.milvus.search(q_vec, top_k=self.config.dense_top_k, repo_id=repo_id)

        # 3. Hydrate Milvus hits with full ScenarioChunk from SQLite
        dense_scenario_ids = [s_id for s_id, _, _ in milvus_hits]
        hydrated_map = {}
        if self.state_db:
            hydrated_map = self.state_db.get_scenarios_by_ids(dense_scenario_ids)

        dense_hits: List[Tuple[ScenarioChunk, float, int]] = []
        for rank, (s_id, score, meta) in enumerate(milvus_hits, start=1):
            if s_id in hydrated_map:
                sc = hydrated_map[s_id]
            else:
                # Construct fallback chunk from Milvus metadata
                sc = ScenarioChunk(
                    scenario_id=s_id,
                    repository_id=meta.get("repo_id", "default"),
                    file_path=meta.get("file_path", ""),
                    feature_name=meta.get("feature_name", ""),
                    scenario_name=meta.get("scenario_name", ""),
                    scenario_type=meta.get("scenario_type", "Scenario"),
                    content_hash=meta.get("content_hash", ""),
                    embedding_version=meta.get("embedding_version", "v1.0"),
                )
            dense_hits.append((sc, score, rank))

        # 4. Reciprocal Rank Fusion (RRF) -> Top 20 Candidates
        fused_candidates = RRFFusion.fuse(
            rankings=[bm25_hits, dense_hits],
            k=self.config.rrf_k,
            top_n=self.config.rrf_top_k,
        )

        if not fused_candidates:
            return []

        # 5. Cross-Encoder Re-ranking -> Top 10 Precision Scenarios
        reranked_top10 = self.reranker.rerank(
            query=query_text,
            candidates=fused_candidates,
            top_k=self.config.reranker_top_k,
        )

        return reranked_top10
