"""Retrieval Evaluation Module.

Computes Recall@5, Recall@10, Mean Reciprocal Rank (MRR), and NDCG@10
against gold standard ground-truth scenario datasets.
"""

import json
import math
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.retrieval.hybrid_retriever import HybridRetriever


class RetrievalEvaluator:
    """Evaluates retrieval quality against gold test cases."""

    def __init__(self, retriever: HybridRetriever, gold_dataset_path: Optional[str or Path] = None):
        self.retriever = retriever
        self.gold_path = Path(gold_dataset_path or Path(__file__).parent / "gold.json")

    def load_gold_dataset(self) -> List[Dict[str, Any]]:
        with open(self.gold_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _is_gold_hit(scenario_name: str, file_path: str, gold_names: List[str], gold_files: List[str]) -> bool:
        norm_name = scenario_name.strip().lower()
        norm_file = Path(file_path).name.strip().lower()
        
        name_match = any(g.strip().lower() in norm_name or norm_name in g.strip().lower() for g in gold_names)
        file_match = not gold_files or any(gf.strip().lower() in norm_file for gf in gold_files)
        return name_match and file_match

    def evaluate(self, repo_id: str = "default", top_k: int = 10) -> Dict[str, Any]:
        gold_data = self.load_gold_dataset()
        if not gold_data:
            return {
                "total_queries": 0,
                "recall_at_5": 0.0,
                "recall_at_10": 0.0,
                "mrr": 0.0,
                "ndcg_at_10": 0.0,
                "query_details": [],
            }

        recalls_5 = []
        recalls_10 = []
        reciprocal_ranks = []
        ndcgs_10 = []
        query_details = []

        for item in gold_data:
            q_id = item.get("requirement_id", "REQ")
            query = item["query"]
            gold_names = item.get("gold_scenario_names", [])
            gold_files = item.get("gold_files", [])

            # Run hybrid retrieval
            candidates = self.retriever.retrieve(query=query, repo_id=repo_id)

            hits_at_5 = 0
            hits_at_10 = 0
            first_rank = 0
            dcg_10 = 0.0

            for rank, (sc, score, _) in enumerate(candidates[:10], start=1):
                is_hit = self._is_gold_hit(sc.scenario_name, sc.file_path, gold_names, gold_files)
                if is_hit:
                    if first_rank == 0:
                        first_rank = rank
                    if rank <= 5:
                        hits_at_5 += 1
                    if rank <= 10:
                        hits_at_10 += 1
                    dcg_10 += 1.0 / math.log2(rank + 1.0)

            if len(gold_names) == 0:
                # True negative / distractor query: perfect if no false positive match
                r5 = 1.0 if hits_at_5 == 0 else 0.0
                r10 = 1.0 if hits_at_10 == 0 else 0.0
                rr = 1.0 if first_rank == 0 else 0.0
                ndcg_10 = 1.0 if dcg_10 == 0.0 else 0.0
            else:
                idcg_10 = sum(1.0 / math.log2(i + 1.0) for i in range(1, min(len(gold_names), 10) + 1)) or 1.0
                ndcg_10 = min(dcg_10 / idcg_10, 1.0)
                r5 = min(hits_at_5 / len(gold_names), 1.0)
                r10 = min(hits_at_10 / len(gold_names), 1.0)
                rr = 1.0 / first_rank if first_rank > 0 else 0.0

            recalls_5.append(r5)
            recalls_10.append(r10)
            reciprocal_ranks.append(rr)
            ndcgs_10.append(ndcg_10)

            query_details.append({
                "requirement_id": q_id,
                "query": query,
                "gold_scenarios": gold_names,
                "retrieved_top_3": [c[0].scenario_name for c in candidates[:3]],
                "first_hit_rank": first_rank,
                "recall_at_5": round(r5, 4),
                "recall_at_10": round(r10, 4),
                "mrr": round(rr, 4),
                "ndcg_at_10": round(ndcg_10, 4),
            })

        n = len(gold_data)
        return {
            "total_queries": n,
            "recall_at_5": round(sum(recalls_5) / n, 4),
            "recall_at_10": round(sum(recalls_10) / n, 4),
            "mrr": round(sum(reciprocal_ranks) / n, 4),
            "ndcg_at_10": round(sum(ndcgs_10) / n, 4),
            "query_details": query_details,
        }
