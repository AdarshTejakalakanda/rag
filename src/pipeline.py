"""Master RAG Requirement Coverage Pipeline with Normalized SQLite State & Lean Milvus Vector Store."""

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
import os

from src.config import AppConfig, load_config
from src.parsers.gherkin_parser import GherkinParser, UniversalFileParser, ScenarioChunk
from src.parsers.requirement_parser import RequirementParser, RequirementChunk
from src.parsers.document_loaders import DocumentLoaderFactory
from src.index.embedding_model import EmbeddingModel
from src.index.bm25_index import BM25Index
from src.index.milvus_store import MilvusStore
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.hybrid_retriever import HybridRetriever
from src.judge.llm_judge import LLMJudge
from src.judge.llm_client import LLMClient
from src.aggregation.aggregator import CoverageAggregator, GlobalCoverageReport, RequirementJudgeVerdict
from src.aggregation.report_generator import ReportGenerator
from src.storage.state_db import StateDatabase
from src.repos.repo_manager import RepositoryManager
from src.chatbot.rag_chat_engine import RAGChatEngine
from src.watcher.fs_watcher import FeatureRepositoryWatcher


class RAGCoveragePipeline:
    """Master orchestrator for Multi-Repo indexing, RAG evaluation, and interactive Chatbot."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.state_db = StateDatabase(db_path=self.config.paths.cache_dir / "rag_state.db")
        self.repo_manager = RepositoryManager(state_db=self.state_db)

        # Initialize submodules
        self.embedding_model = EmbeddingModel(
            model_name=self.config.models.embedding_model,
            device=self.config.models.embedding_device,
        )
        self.bm25_index = BM25Index(
            k1=self.config.bm25.k1,
            b=self.config.bm25.b,
        )
        self.milvus_store = MilvusStore(
            config=self.config.milvus,
            db_path=self.config.paths.milvus_db_path,
            dim=self.config.models.embedding_dimension,
        )
        self.reranker = CrossEncoderReranker(
            model_name=self.config.models.reranker_model,
            device=self.config.models.reranker_device,
            top_k=self.config.retrieval.reranker_top_k,
        )
        self.retriever = HybridRetriever(
            bm25_index=self.bm25_index,
            milvus_store=self.milvus_store,
            embedding_model=self.embedding_model,
            reranker=self.reranker,
            config=self.config.retrieval,
            state_db=self.state_db,
        )
        self.llm_client = LLMClient(config=self.config.judge)
        self.judge = LLMJudge(
            config=self.config.judge,
            client=self.llm_client,
            state_db=self.state_db,
        )
        self.chat_engine = RAGChatEngine(
            retriever=self.retriever,
            state_db=self.state_db,
            llm_client=self.llm_client,
        )
        self.report_generator = ReportGenerator(output_dir=self.config.paths.reports_dir)
        self.watcher: Optional[FeatureRepositoryWatcher] = None
        self._indexed_scenarios_count = 0

        # Pre-warm local neural models at startup so chat/retrieval is sub-second
        try:
            self.embedding_model._load_model()
            self.reranker._load_model()
        except Exception:
            pass

        # Auto-register repos from config if available
        if self.config.repositories:
            for repo_cfg in self.config.repositories:
                if Path(repo_cfg.path).exists():
                    self.repo_manager.add_repository(
                        repo_name=repo_cfg.name,
                        repo_path=repo_cfg.path,
                        repo_id=repo_cfg.id,
                        branch=repo_cfg.branch,
                    )

        # Initial synchronization of BM25 with all scenarios in state database
        self.sync_bm25_index()
        # Backfill any missing vector embeddings for all repositories
        self.ensure_all_embedded()

    def sync_bm25_index(self) -> int:
        """Loads all scenarios across all repositories into the BM25 index."""
        all_global = self.state_db.get_all_scenarios()
        self.bm25_index.index_scenarios(all_global)
        self._indexed_scenarios_count = len(all_global)
        return len(all_global)

    def ensure_all_embedded(self) -> int:
        """Ensures that all scenarios stored in SQLite across all repositories have embeddings in MilvusStore."""
        total_embedded = 0
        for repo in self.state_db.list_repos():
            rid = repo["repo_id"]
            scenarios = self.state_db.get_all_scenarios(repo_id=rid)
            if scenarios:
                m_count = self.milvus_store.count(repo_id=rid)
                if m_count < len(scenarios):
                    print(f"[Pipeline] Backfilling embeddings for repo '{rid}' ({m_count}/{len(scenarios)} embedded)...")
                    texts = [s.canonical_text for s in scenarios]
                    embeddings = self.embedding_model.encode(texts)
                    self.milvus_store.upsert(scenarios, embeddings)
                    total_embedded += len(scenarios)
        return total_embedded

    def index_features(
        self,
        feature_dir: Optional[str or Path] = None,
        repo_id: str = "default",
        repo_name: Optional[str] = None,
        force_reindex: bool = False,
    ) -> int:
        """Parses and incrementally indexes .feature files scoped to repo_id into SQLite, BM25, and Milvus."""
        target_dir = Path(feature_dir or self.config.paths.feature_repos_dir).resolve()
        r_name = repo_name or target_dir.name
        self.repo_manager.add_repository(repo_name=r_name, repo_path=target_dir, repo_id=repo_id)

        self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file="Scanning files...", progress_pct=10)

        print(f"[Pipeline] Scanning automation files in repository '{repo_id}' ({target_dir})")

        feature_files = [
            f for f in target_dir.rglob("*")
            if f.is_file() and UniversalFileParser.is_indexable(f)
        ] if target_dir.exists() else []

        if not feature_files:
            print(f"[Pipeline] Warning: No indexable test or specification files found in {target_dir}")
            self.state_db.set_repo_indexing_status(repo_id, "READY", current_file="", progress_pct=100)
            return 0

        files_to_update: List[Path] = []

        for fpath in feature_files:
            str_path = str(fpath.resolve())
            f_hash = StateDatabase.compute_file_hash(fpath)
            mtime = str(fpath.stat().st_mtime)
            rec = self.state_db.get_feature_file(repo_id, str_path)

            if force_reindex or not rec or rec["file_hash"] != f_hash:
                files_to_update.append(fpath)

        if files_to_update or force_reindex:
            print(f"[Pipeline] Indexing {len(files_to_update)} modified/new file(s) for repo '{repo_id}'...")
            for idx, fpath in enumerate(files_to_update, start=1):
                pct = int(10 + (idx / max(len(files_to_update), 1)) * 60)
                self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file=fpath.name, progress_pct=pct)
                scenarios = UniversalFileParser.parse_file(fpath, repo_id=repo_id)
                f_hash = StateDatabase.compute_file_hash(fpath)
                mtime = str(fpath.stat().st_mtime)
                self.state_db.update_feature_file(
                    repo_id=repo_id,
                    file_path=fpath,
                    file_hash=f_hash,
                    scenario_count=len(scenarios),
                    last_modified=mtime,
                )
                self.state_db.save_scenarios(scenarios, repo_id=repo_id)

            # Increment integer corpus version on feature changes
            c_ver_int = self.state_db.increment_corpus_version(repo_id)
            print(f"[Pipeline] Repository '{repo_id}' corpus version updated to: v{c_ver_int}")
        else:
            c_ver_int = self.state_db.get_corpus_version(repo_id)

        # Load all active scenarios from SQLite state
        all_scenarios = self.state_db.get_all_scenarios(repo_id=repo_id)
        if not all_scenarios:
            all_scenarios = GherkinParser.parse_directory(target_dir, repo_id=repo_id)
            self.state_db.save_scenarios(all_scenarios, repo_id=repo_id)

        # Check if vector embeddings already exist in Milvus
        milvus_count = self.milvus_store.count(repo_id=repo_id)
        needs_embedding = force_reindex or bool(files_to_update) or (milvus_count < len(all_scenarios))

        if needs_embedding:
            self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file="Generating embeddings...", progress_pct=80)
            print(f"[Pipeline] Generating dense embeddings for {len(all_scenarios)} scenario(s)...")
            texts = [s.canonical_text for s in all_scenarios]
            embeddings = self.embedding_model.encode(texts)
            self.milvus_store.upsert(all_scenarios, embeddings)
        else:
            print(f"[Pipeline] Repository '{repo_id}' up-to-date ({len(all_scenarios)} scenarios, corpus v{c_ver_int}).")

        self.sync_bm25_index()
        self.state_db.set_repo_indexing_status(repo_id, "READY", current_file="", progress_pct=100)
        return len(all_scenarios)

    def index_repo_folders(self, repo_id: str, force_reindex: bool = False) -> int:
        """Indexes all configured folders for a repository."""
        repo = self.repo_manager.get_repository(repo_id)
        if not repo:
            return 0
        folders = self.state_db.list_repo_folders(repo_id)
        if not folders and repo.get("repo_path"):
            return self.index_features(
                feature_dir=repo["repo_path"],
                repo_id=repo_id,
                repo_name=repo["repo_name"],
                force_reindex=force_reindex,
            )

        self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file="Scanning folders...", progress_pct=20)
        all_repo_scenarios = []
        for idx, f in enumerate(folders, start=1):
            f_path = Path(f["folder_path"])
            if f_path.exists() and f_path.is_dir():
                self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file=f_path.name, progress_pct=int(20 + (idx / len(folders)) * 50))
                scenarios = UniversalFileParser.parse_directory(f_path, repo_id=repo_id)
                self.state_db.update_folder_scenario_count(f["folder_id"], len(scenarios))
                self.state_db.save_scenarios(scenarios, repo_id=repo_id)
                all_repo_scenarios.extend(scenarios)

        if all_repo_scenarios:
            self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file="Generating embeddings...", progress_pct=85)
            texts = [s.canonical_text for s in all_repo_scenarios]
            embeddings = self.embedding_model.encode(texts)
            self.milvus_store.upsert(all_repo_scenarios, embeddings)
            self.state_db.increment_corpus_version(repo_id)

        self.sync_bm25_index()
        self.state_db.set_repo_indexing_status(repo_id, "READY", current_file="", progress_pct=100)
        return len(all_repo_scenarios)

    def analyze(
        self,
        document_path: Optional[str or Path] = None,
        repo_id: str = "default",
        report_base_name: str = "coverage_report",
        session_name: Optional[str] = None,
    ) -> Tuple[GlobalCoverageReport, Dict[str, str], str]:
        """
        Main analysis flow:
        1. Document -> Register in SQLite `documents` -> Functional Decomposition -> Save to `requirements`
        2. Hybrid Retrieval (BM25 + Dense -> RRF Top 20 -> Cross-Encoder Top 10)
        3. Semantic Cache -> ONE LLM Judge Call
        4. Save verdicts to `evaluations` linked to `requirements` & `scenarios`
        5. Deterministic Aggregation -> Section 30 Reports -> Complete `analysis_sessions`
        """
        # Resolve target repo
        repo = self.repo_manager.get_repository(repo_id)
        if repo and repo.get("repo_path") and Path(repo["repo_path"]).exists():
            feature_dir = Path(repo["repo_path"])
        else:
            feature_dir = Path(self.config.paths.feature_repos_dir)
            if not feature_dir.exists() and Path("sample_data/feature_repos").exists():
                feature_dir = Path("sample_data/feature_repos").resolve()

        if self._indexed_scenarios_count == 0 or not repo:
            self.index_features(feature_dir=feature_dir, repo_id=repo_id)

        target_doc = Path(document_path or self.config.paths.business_docs_dir)
        print(f"[Pipeline] Parsing & decomposing business requirements in: {target_doc}")

        # Register document in SQLite
        doc_record = self.state_db.register_document(target_doc)

        if target_doc.is_file():
            requirements = RequirementParser.parse_file(target_doc)
        else:
            requirements = RequirementParser.parse_directory(target_doc)

        if not requirements:
            print(f"[Pipeline] Warning: No functional requirements extracted from {target_doc}")

        # Create Persistent Analysis Session in SQLite
        corpus_ver = self.state_db.get_corpus_version(repo_id)
        analysis_id = self.state_db.create_analysis_session(
            repo_id=repo_id,
            document_id=doc_record["document_id"],
            corpus_version=corpus_ver,
            session_name=session_name or f"Analysis of {target_doc.name} against {repo_id}",
        )
        print(f"[Pipeline] Created Analysis Session: [cyan]{analysis_id}[/cyan] (Repo: {repo_id}, Corpus Ver: {corpus_ver})")

        # Save requirements in SQLite
        self.state_db.save_requirements(analysis_id=analysis_id, requirements=requirements)
        print(f"[Pipeline] Evaluating {len(requirements)} requirement(s) against repository '{repo_id}'...")

        verdicts: List[RequirementJudgeVerdict] = []

        for idx, req in enumerate(requirements, start=1):
            print(f"[{idx}/{len(requirements)}] Retrieving & judging coverage for [{req.req_id}] {req.title[:45]}...")
            
            # Hybrid Retrieval -> Top 50 BM25 + Top 50 Dense -> RRF -> Cross-Encoder (Top 10)
            top10_candidates, retrieval_pool = self.retriever.retrieve_with_pool(req, repo_id=repo_id)

            # Agentic Retrieval Sufficiency Check & ONE Controlled Retry (if needed)
            verdict = self.judge.judge_requirement(
                requirement=req,
                candidates=top10_candidates,
                repo_id=repo_id,
                retrieval_pool=retrieval_pool,
                retriever=self.retriever,
            )
            verdicts.append(verdict)

            # Record in SQLite `evaluations` table
            self.state_db.save_evaluations(
                analysis_id=analysis_id,
                verdict=verdict,
                model_version=self.llm_client.provider,
                prompt_version=self.judge.prompt_version,
            )

        # Global Aggregation
        print("[Pipeline] Aggregating deterministic coverage report...")
        global_report = CoverageAggregator.aggregate(
            verdicts=verdicts,
            total_scenarios_count=self.milvus_store.count(repo_id=repo_id),
        )

        # Report Generation (MD, JSON, HTML)
        print("[Pipeline] Generating Section 30 Markdown, JSON, and interactive HTML dashboard...")
        report_files = self.report_generator.generate_all(global_report, base_name=report_base_name)

        # Complete Analysis Session in SQLite
        self.state_db.complete_analysis_session(analysis_id, global_report, report_files)
        print(f"[Pipeline] Completed and persisted Analysis Session: [green]{analysis_id}[/green]")

        return global_report, report_files, analysis_id

    def evaluate(
        self,
        docs_dir: Optional[str or Path] = None,
        feature_dir: Optional[str or Path] = None,
        repo_id: Optional[str] = None,
        report_base_name: str = "coverage_report",
        session_name: Optional[str] = None,
    ) -> Tuple[GlobalCoverageReport, Dict[str, str], str]:
        """Alias for analyze()."""
        r_id = repo_id or "default"
        if feature_dir:
            self.index_features(feature_dir=feature_dir, repo_id=r_id)
        return self.analyze(
            document_path=docs_dir,
            repo_id=r_id,
            report_base_name=report_base_name,
            session_name=session_name,
        )

    def chat(self, message: str, repo_id: str = "default", chat_id: Optional[str] = None, bypass_cache: bool = False) -> Dict[str, Any]:
        """Conversational RAG QA with grounded scenario citations and index state guard."""
        idx_status = self.state_db.get_repo_indexing_status(repo_id)
        if idx_status.get("index_status") == "INDEXING":
            file_info = f" ({idx_status.get('current_indexing_file')})" if idx_status.get('current_indexing_file') else ""
            raise RuntimeError(
                f"Repository '{repo_id}' is currently indexing{file_info} [{idx_status.get('indexing_progress_pct', 0)}%]. "
                "Analysis will be available as soon as repository indexing completes."
            )
        return self.chat_engine.chat(user_message=message, repo_id=repo_id, chat_id=chat_id, bypass_cache=bypass_cache)

    def start_watcher(self, feature_dir: Optional[str or Path] = None, repo_id: str = "default", blocking: bool = False) -> FeatureRepositoryWatcher:
        """Starts real-time filesystem watcher for automatic incremental reindexing."""
        target_dir = Path(feature_dir or self.config.paths.feature_repos_dir)
        
        def on_reindex(path: str, count: int):
            p = Path(path)
            if p.exists():
                f_hash = StateDatabase.compute_file_hash(p)
                mtime = str(p.stat().st_mtime)
                self.state_db.update_feature_file(repo_id, path, f_hash, count, mtime)
            else:
                self.state_db.delete_feature_file(repo_id, path)
            self.state_db.increment_corpus_version(repo_id)

        self.watcher = FeatureRepositoryWatcher(
            target_dir=target_dir,
            on_change_callback=on_reindex,
        )
        self.watcher.start()
        if blocking:
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.watcher.stop()
        return self.watcher
