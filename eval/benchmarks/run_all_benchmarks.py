"""Master Benchmark Runner.

Runs Decoupled Retrieval & Judge Evaluations, captures immutable pipeline
configuration snapshots, and persists timestamped JSON and Markdown reports.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from src.pipeline import RAGCoveragePipeline
from src.config import AppConfig
from eval.retrieval.evaluate_retrieval import RetrievalEvaluator
from eval.judge.evaluate_judge import JudgeEvaluator


class BenchmarkRunner:
    """Orchestrates comprehensive decoupled benchmarks and saves configuration-fingerprinted reports."""

    def __init__(
        self,
        pipeline: Optional[RAGCoveragePipeline] = None,
        results_dir: Optional[str or Path] = None,
    ):
        self.pipeline = pipeline or RAGCoveragePipeline()
        self.results_dir = Path(results_dir or Path(__file__).parent.parent / "results")
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def get_config_snapshot(self, repo_id: str = "default", bypass_cache: bool = True) -> Dict[str, Any]:
        """Captures an exact snapshot of the active pipeline and model configurations."""
        corpus_ver = self.pipeline.state_db.get_corpus_version(repo_id)
        return {
            "embedding_model": getattr(self.pipeline.embedding_model, "model_name", "BAAI/bge-small-en-v1.5"),
            "reranker_model": getattr(self.pipeline.reranker, "model_name", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
            "llm_provider": getattr(self.pipeline.config.judge, "provider", "gemini"),
            "llm_model": getattr(self.pipeline.config.judge, "model_name", "gemini-2.5-flash"),
            "prompt_version": "judge-v2.0",
            "retrieval_version": "hybrid-bm25-dense-rrf-v1.0",
            "corpus_version": corpus_ver,
            "cache_bypassed": bypass_cache,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def run(
        self,
        target: str = "all",
        repo_id: str = "default",
        bypass_cache: bool = True,
    ) -> Dict[str, Any]:
        """Runs benchmarks for specified target ('retrieval', 'judge', 'all')."""
        print("\n=======================================================")
        print("⚡ LOCAL RAG BDD AUTOMATION AGENT - BENCHMARK HARNESS ⚡")
        print("=======================================================\n")

        config_snapshot = self.get_config_snapshot(repo_id=repo_id, bypass_cache=bypass_cache)
        print(f"📌 Pipeline Snapshot:")
        print(f"   • Embedding Model : {config_snapshot['embedding_model']}")
        print(f"   • Reranker Model  : {config_snapshot['reranker_model']}")
        print(f"   • LLM Judge Model : {config_snapshot['llm_model']} ({config_snapshot['llm_provider']})")
        print(f"   • Prompt Version  : {config_snapshot['prompt_version']}")
        print(f"   • Cache Bypassed  : {config_snapshot['cache_bypassed']}\n")

        results: Dict[str, Any] = {
            "timestamp": config_snapshot["timestamp"],
            "config_snapshot": config_snapshot,
            "target": target,
        }

        # 1. Retrieval Benchmark
        if target in ("retrieval", "all", "e2e"):
            # Ensure features are indexed for retrieval
            sc_count = len(self.pipeline.state_db.get_all_scenarios(repo_id=repo_id))
            if sc_count == 0:
                features_dir = Path("sample_data/feature_repos")
                if features_dir.exists():
                    print(f"📦 Indexing sample feature repository for retrieval evaluation...")
                    self.pipeline.index_features(feature_dir=features_dir, repo_id=repo_id)

            print("🔍 Running Retrieval Evaluation (Recall@5, Recall@10, MRR, NDCG)...")
            r_eval = RetrievalEvaluator(retriever=self.pipeline.retriever)
            r_metrics = r_eval.evaluate(repo_id=repo_id)
            results["retrieval_metrics"] = r_metrics
            print(f"   ✅ Recall@5  : {r_metrics['recall_at_5'] * 100:.1f}%")
            print(f"   ✅ Recall@10 : {r_metrics['recall_at_10'] * 100:.1f}%")
            print(f"   ✅ MRR       : {r_metrics['mrr']:.4f}")
            print(f"   ✅ NDCG@10   : {r_metrics['ndcg_at_10']:.4f}\n")

        # 2. Judge Benchmark
        if target in ("judge", "all", "e2e"):
            print("⚖️ Running LLM Judge Evaluation (Accuracy, Macro-F1, Confusion Matrix)...")
            j_eval = JudgeEvaluator(judge=self.pipeline.judge)
            j_metrics = j_eval.evaluate(bypass_cache=bypass_cache)
            results["judge_metrics"] = j_metrics
            print(f"   ✅ Accuracy  : {j_metrics['accuracy'] * 100:.1f}%")
            print(f"   ✅ Macro-F1  : {j_metrics['macro_f1']:.4f}")
            print(f"   ✅ MAE Match%: {j_metrics['mean_absolute_error_pct']:.1f}%\n")

        # Save Benchmark Reports
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.results_dir / f"{ts_str}_benchmark.json"
        md_path = self.results_dir / f"{ts_str}_benchmark.md"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        md_content = self._generate_markdown_report(results)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"💾 Benchmark JSON saved to: {json_path}")
        print(f"📄 Markdown Report saved to: {md_path}")
        print("=======================================================\n")
        return results

    def _generate_markdown_report(self, results: Dict[str, Any]) -> str:
        cfg = results["config_snapshot"]
        rm = results.get("retrieval_metrics", {})
        jm = results.get("judge_metrics", {})

        md = [
            f"# 📊 RAG Coverage Benchmark Report",
            f"**Execution Timestamp**: `{results['timestamp']}`\n",
            f"## ⚙️ Pipeline Configuration Snapshot",
            f"| Parameter | Value |",
            f"|---|---|",
            f"| **Embedding Model** | `{cfg['embedding_model']}` |",
            f"| **Reranker Model** | `{cfg['reranker_model']}` |",
            f"| **LLM Judge** | `{cfg['llm_model']} ({cfg['llm_provider']})` |",
            f"| **Prompt Version** | `{cfg['prompt_version']}` |",
            f"| **Retrieval Version** | `{cfg['retrieval_version']}` |",
            f"| **Corpus Version** | `v{cfg['corpus_version']}` |",
            f"| **Cache Bypassed** | `{cfg['cache_bypassed']}` |",
            "\n---\n",
        ]

        if rm:
            md.extend([
                "## 🔍 Retrieval Layer Metrics",
                f"- **Recall@5**: `{rm.get('recall_at_5', 0) * 100:.1f}%`",
                f"- **Recall@10**: `{rm.get('recall_at_10', 0) * 100:.1f}%`",
                f"- **MRR (Mean Reciprocal Rank)**: `{rm.get('mrr', 0):.4f}`",
                f"- **NDCG@10**: `{rm.get('ndcg_at_10', 0):.4f}`",
                f"- **Total Gold Queries Evaluated**: `{rm.get('total_queries', 0)}`",
                "\n",
            ])

        if jm:
            md.extend([
                "## ⚖️ LLM Judge Layer Metrics",
                f"- **Overall Accuracy**: `{jm.get('accuracy', 0) * 100:.1f}%`",
                f"- **Macro-F1 Score**: `{jm.get('macro_f1', 0):.4f}`",
                f"- **Mean Absolute Error (Match %)**: `{jm.get('mean_absolute_error_pct', 0):.1f}%`",
                "\n### Confusion Matrix",
                "```json",
                json.dumps(jm.get("confusion_matrix", {}), indent=2),
                "```\n",
            ])

        return "\n".join(md)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run RAG Evaluation Benchmarks")
    parser.add_argument("--target", choices=["retrieval", "judge", "all", "e2e"], default="all", help="Target benchmark layer")
    parser.add_argument("--repo-id", default="default", help="Repository ID to benchmark")
    parser.add_argument("--bypass-cache", action="store_true", default=True, help="Force bypass semantic cache")
    args = parser.parse_args()

    runner = BenchmarkRunner()
    runner.run(target=args.target, repo_id=args.repo_id, bypass_cache=args.bypass_cache)


if __name__ == "__main__":
    main()
