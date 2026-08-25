"""Milvus Vector Store with lean scenario collection schema.

Conforms to Specifications:
- Lean Milvus schema storing vector + scenario metadata (no raw Gherkin text).
- Primary key: scenario_id.
- HNSW index with COSINE metric.
- Dynamic repo_id filtering.
- Resilient local fallback when native Milvus server is not installed.
"""

from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import numpy as np
import warnings

# Suppress harmless PyMilvus ORM deprecation warnings
warnings.filterwarnings("ignore", module="pymilvus.*")
warnings.filterwarnings("ignore", message=".*connections.connect.*")

from src.parsers.gherkin_parser import ScenarioChunk, fast_hash
from src.config import MilvusConfig


class MilvusStore:
    """Milvus Vector Database client storing lean scenario embeddings with HNSW indexing."""

    def __init__(
        self,
        config: Optional[MilvusConfig] = None,
        db_path: str or Path = "data/milvus_rag.db",
        dim: int = 384,
    ):
        self.config = config or MilvusConfig()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self.collection_name = self.config.collection_name
        self.collection = None
        self._local_fallback_store: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
        self._init_milvus()

    def _load_cache(self):
        cache_file = self.db_path.with_suffix(".vectors.json")
        if cache_file.exists():
            try:
                import json
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    v["vector"] = np.array(v["vector"], dtype=np.float32)
                    self._local_fallback_store[k] = v
            except Exception:
                pass

    def _save_cache(self):
        cache_file = self.db_path.with_suffix(".vectors.json")
        try:
            import json
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            serializable = {}
            for k, v in self._local_fallback_store.items():
                s = dict(v)
                s["vector"] = s["vector"].tolist() if hasattr(s["vector"], "tolist") else list(s["vector"])
                serializable[k] = s
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(serializable, f)
        except Exception:
            pass

    def _init_milvus(self):
        """Initializes Milvus connection and collection schema or falls back gracefully."""
        try:
            from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility

            # Connect
            if self.config.mode == "standalone":
                connections.connect(
                    alias="default",
                    host=self.config.host,
                    port=self.config.port,
                )
            else:
                connections.connect(
                    alias="default",
                    uri=str(self.db_path),
                )

            # Create Collection Schema if not existing
            if not utility.has_collection(self.collection_name):
                fields = [
                    FieldSchema(name="scenario_id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
                    FieldSchema(name="repo_id", dtype=DataType.VARCHAR, max_length=128),
                    FieldSchema(name="feature_file_id", dtype=DataType.VARCHAR, max_length=128),
                    FieldSchema(name="file_path", dtype=DataType.VARCHAR, max_length=2048),
                    FieldSchema(name="feature_name", dtype=DataType.VARCHAR, max_length=512),
                    FieldSchema(name="scenario_name", dtype=DataType.VARCHAR, max_length=512),
                    FieldSchema(name="scenario_type", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="content_hash", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="embedding_version", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
                ]

                schema = CollectionSchema(
                    fields=fields,
                    description="Gherkin scenario embeddings for requirement coverage retrieval",
                    enable_dynamic_field=False,
                )

                self.collection = Collection(
                    name=self.collection_name,
                    schema=schema,
                )

                # Create HNSW vector index
                index_params = {
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                    "params": {"M": 16, "efConstruction": 200},
                }
                self.collection.create_index(
                    field_name="embedding",
                    index_params=index_params,
                )
            else:
                self.collection = Collection(name=self.collection_name)

        except Exception:
            self.collection = None

    def upsert(self, scenarios: List[ScenarioChunk], embeddings: np.ndarray):
        """Inserts or updates scenario vectors and lean metadata in Milvus."""
        if not scenarios or len(embeddings) == 0:
            return

        scenario_ids = [s.scenario_id for s in scenarios]
        repo_ids = [s.repository_id for s in scenarios]
        feature_file_ids = [f"ff_{fast_hash(f'{s.repository_id}#{s.file_path}')[:12]}" for s in scenarios]
        file_paths = [s.file_path for s in scenarios]
        feature_names = [s.feature_name[:500] for s in scenarios]
        scenario_names = [s.scenario_name[:500] for s in scenarios]
        scenario_types = [s.scenario_type for s in scenarios]
        content_hashes = [s.content_hash for s in scenarios]
        embedding_versions = [s.embedding_version for s in scenarios]
        vectors = embeddings.tolist()

        if self.collection:
            try:
                # Delete existing by IDs
                expr = f'scenario_id in {json.dumps(scenario_ids)}'
                self.collection.delete(expr)
                
                data = [
                    scenario_ids,
                    repo_ids,
                    feature_file_ids,
                    file_paths,
                    feature_names,
                    scenario_names,
                    scenario_types,
                    content_hashes,
                    embedding_versions,
                    vectors,
                ]
                self.collection.insert(data)
                self.collection.flush()
                return
            except Exception as e:
                print(f"[Milvus] Warning: Insert error ({e}), updating local fallback store.")

        # Local fallback store
        for idx, sc in enumerate(scenarios):
            self._local_fallback_store[sc.scenario_id] = {
                "scenario_id": sc.scenario_id,
                "repo_id": sc.repository_id,
                "feature_file_id": feature_file_ids[idx],
                "file_path": sc.file_path,
                "feature_name": sc.feature_name,
                "scenario_name": sc.scenario_name,
                "scenario_type": sc.scenario_type,
                "content_hash": sc.content_hash,
                "embedding_version": sc.embedding_version,
                "vector": embeddings[idx],
            }
        self._save_cache()

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 20,
        repo_id: Optional[str] = None,
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Executes dense vector search returning lean candidate tuples:
        (scenario_id, cosine_score, metadata).
        Full scenario Gherkin content is hydrated from SQLite using scenario_id.
        """
        if self.collection:
            try:
                search_params = {"metric_type": "COSINE", "params": {"ef": 64}}
                expr = f'repo_id == "{repo_id}"' if repo_id else None

                results = self.collection.search(
                    data=[query_vector.tolist()],
                    anns_field="embedding",
                    param=search_params,
                    limit=top_k,
                    expr=expr,
                    output_fields=[
                        "scenario_id", "repo_id", "feature_file_id",
                        "file_path", "feature_name", "scenario_name",
                        "scenario_type", "content_hash", "embedding_version"
                    ],
                )

                hits_out: List[Tuple[str, float, Dict[str, Any]]] = []
                for hits in results:
                    for hit in hits:
                        meta = {
                            "repo_id": hit.entity.get("repo_id"),
                            "feature_file_id": hit.entity.get("feature_file_id"),
                            "file_path": hit.entity.get("file_path"),
                            "feature_name": hit.entity.get("feature_name"),
                            "scenario_name": hit.entity.get("scenario_name"),
                            "scenario_type": hit.entity.get("scenario_type"),
                            "content_hash": hit.entity.get("content_hash"),
                            "embedding_version": hit.entity.get("embedding_version"),
                        }
                        hits_out.append((hit.id, float(hit.distance), meta))
                return hits_out
            except Exception as e:
                print(f"[Milvus] Warning: Search error ({e}), searching local fallback store.")

        # Local fallback cosine similarity search
        if not self._local_fallback_store:
            return []

        q_vec = query_vector / (np.linalg.norm(query_vector) + 1e-9)
        scored = []

        for s_id, record in self._local_fallback_store.items():
            if repo_id and record.get("repo_id") != repo_id:
                continue

            r_vec = record["vector"] / (np.linalg.norm(record["vector"]) + 1e-9)
            sim = float(np.dot(q_vec, r_vec))
            meta = {
                "repo_id": record.get("repo_id"),
                "feature_file_id": record.get("feature_file_id"),
                "file_path": record.get("file_path"),
                "feature_name": record.get("feature_name"),
                "scenario_name": record.get("scenario_name"),
                "scenario_type": record.get("scenario_type"),
                "content_hash": record.get("content_hash"),
                "embedding_version": record.get("embedding_version"),
            }
            scored.append((s_id, sim, meta))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def delete_by_file(self, file_path: str) -> None:
        """Deletes all scenario embeddings matching a given file_path."""
        p_str = str(file_path)
        target_norm = str(Path(file_path).resolve()).replace("\\", "/")
        target_raw = str(file_path).replace("\\", "/")

        if self.collection:
            try:
                p_esc = p_str.replace("\\", "\\\\")
                p_norm_esc = target_norm.replace("\\", "\\\\")
                expr = f'file_path == "{p_esc}" or file_path == "{p_norm_esc}"'
                self.collection.delete(expr)
                self.collection.flush()
            except Exception:
                pass
        to_del = [
            s_id for s_id, rec in self._local_fallback_store.items()
            if str(Path(rec.get("file_path", "")).resolve()).replace("\\", "/") == target_norm
            or str(rec.get("file_path", "")).replace("\\", "/") == target_raw
        ]
        for s_id in to_del:
            del self._local_fallback_store[s_id]
        if to_del:
            self._save_cache()

    def clear_repo(self, repo_id: str) -> None:
        """Removes all embeddings for a specific repository."""
        if self.collection:
            try:
                expr = f'repo_id == "{repo_id}"'
                self.collection.delete(expr)
                self.collection.flush()
            except Exception:
                pass
        to_del = [s_id for s_id, rec in self._local_fallback_store.items() if rec.get("repo_id") == repo_id]
        for s_id in to_del:
            del self._local_fallback_store[s_id]
        if to_del:
            self._save_cache()


    def count(self, repo_id: Optional[str] = None) -> int:
        if self.collection:
            try:
                if repo_id:
                    return len(self.collection.query(expr=f'repo_id == "{repo_id}"', output_fields=["scenario_id"]))
                return self.collection.num_entities
            except Exception:
                pass
        if repo_id:
            return sum(1 for r in self._local_fallback_store.values() if r.get("repo_id") == repo_id)
        return len(self._local_fallback_store)
