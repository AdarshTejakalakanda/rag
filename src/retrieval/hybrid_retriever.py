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

    def retrieve_with_pool(
        self,
        query: str or RequirementChunk,
        repo_id: Optional[str] = None,
    ) -> Tuple[List[Tuple[ScenarioChunk, float, Dict[str, Any]]], Dict[str, Any]]:
        """
        Executes initial hybrid retrieval with expanded Top 50 pool:
        1. BM25 Lexical Search (Top 50)
        2. Milvus Dense Vector Search (Top 50) -> scenario_ids
        3. Hydrates full ScenarioChunk (raw Gherkin + canonical text) from SQLite
        4. Caches Top 50 BM25 + Dense pool for zero-query controlled retry
        5. Balanced Reciprocal Rank Fusion (RRF Top 25)
        6. Cross-Encoder Re-ranking (Top 10)
        Returns: (top10_candidates, retrieval_pool)
        """
        if isinstance(query, RequirementChunk):
            query_text = query.full_text or f"{query.title}\n{query.description}"
        else:
            query_text = str(query)

        # Dynamic auto-sync: Reconcile BM25 and Milvus memory pools with SQLite
        if self.state_db and repo_id:
            self._sync_repo_if_needed(repo_id)

        # 1. BM25 Lexical Retrieval (Top 50)
        bm25_hits = self.bm25.search(query_text, top_k=self.config.bm25_top_k, repo_id=repo_id)

        # 2. Dense Vector Retrieval (Top 50)
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

        retrieval_pool = {
            "bm25_hits": bm25_hits,
            "dense_hits": dense_hits,
            "query_text": query_text,
            "repo_id": repo_id,
        }

        # 4. Standard Balanced Reciprocal Rank Fusion (RRF) -> Top 25 Candidates
        fused_candidates = RRFFusion.fuse(
            rankings=[bm25_hits, dense_hits],
            weights=[1.0, 1.0],
            k=self.config.rrf_k,
            top_n=self.config.rrf_top_k,
        )

        if not fused_candidates:
            return [], retrieval_pool

        # 5. Cross-Encoder Re-ranking -> Top 10 Precision Scenarios
        reranked_top10 = self.reranker.rerank(
            query=query_text,
            candidates=fused_candidates,
            top_k=self.config.reranker_top_k,
        )

        return reranked_top10, retrieval_pool

    def retry_with_strategy(
        self,
        retrieval_pool: Dict[str, Any],
        strategy: str = "LEXICAL_HEAVY",
    ) -> List[Tuple[ScenarioChunk, float, Dict[str, Any]]]:
        """
        Executes one controlled retry using Weighted RRF over the already retrieved Top 50 lists.
        Does NOT re-query Milvus or BM25, keeping execution extremely fast and lightweight.

        Strategies:
          • 'LEXICAL_HEAVY': weights=[2.0, 0.5] (prioritizes BM25 exact keyword matches)
          • 'DENSE_HEAVY':   weights=[0.5, 2.0] (prioritizes Milvus semantic vector matches)
          • 'NONE' / other:  weights=[1.0, 1.0] (balanced)
        """
        bm25_hits = retrieval_pool.get("bm25_hits", [])
        dense_hits = retrieval_pool.get("dense_hits", [])
        query_text = retrieval_pool.get("query_text", "")

        if not bm25_hits and not dense_hits:
            return []

        strat_upper = (strategy or "").upper()
        if strat_upper == "LEXICAL_HEAVY":
            weights = list(self.config.lexical_heavy_weights)
        elif strat_upper == "DENSE_HEAVY":
            weights = list(self.config.dense_heavy_weights)
        else:
            weights = list(self.config.balanced_weights)

        # Weighted RRF on existing Top 50 pools
        fused_candidates = RRFFusion.fuse(
            rankings=[bm25_hits, dense_hits],
            weights=weights,
            k=self.config.rrf_k,
            top_n=self.config.rrf_top_k,
        )

        if not fused_candidates:
            return []

        # Cross-Encoder Reranking -> Top 10
        reranked_top10 = self.reranker.rerank(
            query=query_text,
            candidates=fused_candidates,
            top_k=self.config.reranker_top_k,
        )

        return reranked_top10

    def _sync_repo_if_needed(self, repo_id: str) -> None:
        """
        Reconciles repository-scoped scenario IDs and content hashes against SQLite.
        Removes records absent from SQLite, rebuilds or upserts changed records, and
        clears stale index records when SQLite is empty while preserving unchanged records.
        """
        if not self.state_db or not repo_id:
            return

        sqlite_scenarios = self.state_db.get_all_scenarios(repo_id=repo_id)
        sqlite_map = {s.scenario_id: s for s in sqlite_scenarios}
        sqlite_ids = set(sqlite_map.keys())
        sqlite_hash_map = {s.scenario_id: s.content_hash for s in sqlite_scenarios}

        # 1. BM25 reconciliation: compare repository-scoped IDs and content hashes
        bm25_repo_scenarios = [s for s in self.bm25.scenarios if s.repository_id == repo_id]
        bm25_ids = {s.scenario_id for s in bm25_repo_scenarios}
        bm25_hash_map = {s.scenario_id: s.content_hash for s in bm25_repo_scenarios}

        bm25_needs_sync = (bm25_ids != sqlite_ids) or any(
            bm25_hash_map.get(s_id) != h for s_id, h in sqlite_hash_map.items()
        )
        if bm25_needs_sync:
            all_scenarios = self.state_db.get_all_scenarios()
            self.bm25.index_scenarios(all_scenarios)

        # 2. Milvus reconciliation: compare repository-scoped IDs and content hashes
        if hasattr(self.milvus, "_local_fallback_store"):
            milvus_records = {
                s_id: rec for s_id, rec in self.milvus._local_fallback_store.items()
                if rec.get("repo_id") == repo_id
            }
            milvus_ids = set(milvus_records.keys())
            milvus_hash_map = {s_id: rec.get("content_hash") for s_id, rec in milvus_records.items()}

            stale_milvus_ids = list(milvus_ids - sqlite_ids)
            if stale_milvus_ids:
                self.milvus.delete_by_ids(stale_milvus_ids)

            missing_or_changed = [
                s for s in sqlite_scenarios
                if s.scenario_id not in milvus_ids or milvus_hash_map.get(s.scenario_id) != s.content_hash
            ]
            if missing_or_changed:
                texts = [s.canonical_text for s in missing_or_changed]
                embeddings = self.embedder.encode(texts)
                self.milvus.upsert(missing_or_changed, embeddings)
        elif self.milvus.collection:
            # Standalone Milvus Collection connected
            try:
                existing = self.milvus.collection.query(
                    expr=f'repo_id == "{repo_id}"',
                    output_fields=["scenario_id", "content_hash"]
                )
                milvus_ids = {r["scenario_id"] for r in existing}
                milvus_hash_map = {r["scenario_id"]: r.get("content_hash") for r in existing}

                stale_milvus_ids = list(milvus_ids - sqlite_ids)
                if stale_milvus_ids:
                    self.milvus.delete_by_ids(stale_milvus_ids)

                missing_or_changed = [
                    s for s in sqlite_scenarios
                    if s.scenario_id not in milvus_ids or milvus_hash_map.get(s.scenario_id) != s.content_hash
                ]
                if missing_or_changed:
                    texts = [s.canonical_text for s in missing_or_changed]
                    embeddings = self.embedder.encode(texts)
                    self.milvus.upsert(missing_or_changed, embeddings)
            except Exception as e:
                print(f"[HybridRetriever] Notice: Milvus reconciliation ({e})")

    def retrieve(
        self,
        query: str or RequirementChunk,
        repo_id: Optional[str] = None,
    ) -> List[Tuple[ScenarioChunk, float, Dict[str, Any]]]:
        """Backward-compatible retrieval method returning top-10 candidates."""
        candidates, _ = self.retrieve_with_pool(query=query, repo_id=repo_id)
        return candidates
