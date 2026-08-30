"""SQLite-backed Relational State Database & Versioned Semantic Cache.

Conforms to Specifications:
- Normalized relational schema (repositories, documents, feature_files, scenarios,
  analysis_sessions, requirements, evaluations, chat_sessions, chat_messages).
- Integer corpus_version increment on change.
- 7-factor reproducible semantic cache key.
- Deterministic hydration of scenario Gherkin content by scenario_id.
"""

import sqlite3
import json
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from src.parsers.gherkin_parser import ScenarioChunk, fast_hash
from src.parsers.requirement_parser import RequirementChunk


class StateDatabase:
    """SQLite-backed local source of truth for metadata, relational index state, and sessions."""

    def __init__(self, db_path: str or Path = "data/rag_state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Creates the relational database schema and indexes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Repositories Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    repo_id TEXT PRIMARY KEY,
                    repo_name TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    branch TEXT DEFAULT 'main',
                    corpus_version INTEGER NOT NULL DEFAULT 1,
                    scenario_count INTEGER DEFAULT 0,
                    index_status TEXT DEFAULT 'READY',
                    current_indexing_file TEXT DEFAULT '',
                    indexing_progress_pct INTEGER DEFAULT 100,
                    last_indexed_at TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            # 1b. Multi-Folder Indexing Paths Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repo_folders (
                    folder_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    folder_path TEXT NOT NULL,
                    scenario_count INTEGER NOT NULL DEFAULT 0,
                    last_indexed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (repo_id) REFERENCES repositories(repo_id) ON DELETE CASCADE,
                    UNIQUE(repo_id, folder_path)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_repo_folders ON repo_folders(repo_id);")

            # 2. Documents Table (Business Requirement Documents)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL UNIQUE,
                    file_name TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # 3. Feature Files Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feature_files (
                    feature_file_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    scenario_count INTEGER NOT NULL DEFAULT 0,
                    last_modified TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY (repo_id) REFERENCES repositories(repo_id) ON DELETE CASCADE,
                    UNIQUE(repo_id, file_path)
                );
            """)

            # 4. Scenarios Table (Scenario-level indexing metadata & content)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    feature_file_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    line_number INTEGER NOT NULL DEFAULT 1,
                    feature_name TEXT,
                    scenario_name TEXT NOT NULL,
                    scenario_type TEXT NOT NULL,
                    tags_json TEXT,
                    canonical_text TEXT,
                    raw_gherkin TEXT,
                    content_hash TEXT NOT NULL,
                    milvus_id TEXT NOT NULL UNIQUE,
                    indexed_at TEXT NOT NULL,
                    FOREIGN KEY (feature_file_id) REFERENCES feature_files(feature_file_id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_repo ON scenarios(repo_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_file ON scenarios(feature_file_id);")

            # 5. Analysis Sessions Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS analysis_sessions (
                    analysis_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    corpus_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    session_name TEXT,
                    total_requirements INTEGER DEFAULT 0,
                    covered_count INTEGER DEFAULT 0,
                    partial_count INTEGER DEFAULT 0,
                    uncovered_count INTEGER DEFAULT 0,
                    average_match_pct REAL DEFAULT 0.0,
                    report_paths_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (repo_id) REFERENCES repositories(repo_id),
                    FOREIGN KEY (document_id) REFERENCES documents(document_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_repo ON analysis_sessions(repo_id);")

            # 6. Extracted Business Requirements Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS requirements (
                    requirement_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    requirement_text TEXT NOT NULL,
                    requirement_index INTEGER NOT NULL,
                    title TEXT,
                    category TEXT,
                    source_file TEXT,
                    line_number INTEGER DEFAULT 1,
                    acceptance_criteria_json TEXT,
                    business_rules_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (analysis_id) REFERENCES analysis_sessions(analysis_id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_requirements_analysis ON requirements(analysis_id);")

            # 7. Final / Evidence-Level Evaluations Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    requirement_id TEXT NOT NULL,
                    scenario_id TEXT,
                    status TEXT NOT NULL,
                    match_percentage REAL,
                    reasoning TEXT NOT NULL,
                    evidence_json TEXT,
                    citation_json TEXT,
                    covered_criteria_json TEXT,
                    missing_gaps_json TEXT,
                    suggested_tests_json TEXT,
                    model_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (requirement_id) REFERENCES requirements(requirement_id) ON DELETE CASCADE,
                    FOREIGN KEY (scenario_id) REFERENCES scenarios(scenario_id) ON DELETE SET NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_evaluations_req ON evaluations(requirement_id);")

            # 8. Chat Sessions Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    chat_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    analysis_id TEXT,
                    title TEXT,
                    corpus_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (repo_id) REFERENCES repositories(repo_id),
                    FOREIGN KEY (analysis_id) REFERENCES analysis_sessions(analysis_id)
                );
            """)

            # 9. Chat Messages Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citations_json TEXT,
                    agent_trace_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (chat_id) REFERENCES chat_sessions(chat_id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages ON chat_messages(chat_id);")

            # 10. Semantic Cache Table (7-factor reproducible cache)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS semantic_cache (
                    cache_key TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    corpus_version INTEGER NOT NULL,
                    requirement_hash TEXT NOT NULL,
                    candidates_hash TEXT NOT NULL,
                    retrieval_version TEXT NOT NULL,
                    reranker_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    judgment_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_composite ON semantic_cache(repo_id, corpus_version);")

            # Auto-migrations for existing tables
            migrations = [
                ("chat_messages", "message_id", "TEXT"),
                ("chat_messages", "citations_json", "TEXT"),
                ("chat_messages", "agent_trace_json", "TEXT"),
                ("chat_sessions", "analysis_id", "TEXT"),
                ("chat_sessions", "corpus_version", "INTEGER DEFAULT 1"),
                ("repositories", "corpus_version", "INTEGER DEFAULT 1"),
                ("analysis_sessions", "corpus_version", "INTEGER DEFAULT 1"),
                ("analysis_sessions", "session_name", "TEXT"),
                ("analysis_sessions", "report_paths_json", "TEXT"),
                ("requirements", "title", "TEXT"),
                ("requirements", "category", "TEXT"),
                ("requirements", "source_file", "TEXT"),
                ("requirements", "line_number", "INTEGER DEFAULT 1"),
                ("requirements", "acceptance_criteria_json", "TEXT"),
                ("requirements", "business_rules_json", "TEXT"),
                ("evaluations", "covered_criteria_json", "TEXT"),
                ("evaluations", "missing_gaps_json", "TEXT"),
                ("evaluations", "suggested_tests_json", "TEXT"),
                ("repositories", "index_status", "TEXT DEFAULT 'READY'"),
                ("repositories", "current_indexing_file", "TEXT DEFAULT ''"),
                ("repositories", "indexing_progress_pct", "INTEGER DEFAULT 100"),
                ("semantic_cache", "embedding_blob", "BLOB"),
                ("semantic_cache", "requirement_text", "TEXT"),
            ]
            for tbl, col, ctype in migrations:
                try:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ctype};")
                except Exception:
                    pass

            # Backward-compatible view for old session listings
            cursor.execute("CREATE VIEW IF NOT EXISTS sessions AS SELECT analysis_id AS session_id, session_name, status, repo_id, total_requirements, covered_count, partial_count, uncovered_count, average_match_pct, report_paths_json, created_at AS started_at, completed_at FROM analysis_sessions;")

            conn.commit()

    # ==================== Repositories ====================

    def register_repo(
        self,
        repo_name: str,
        repo_path: str or Path,
        repo_id: Optional[str] = None,
        branch: str = "main",
    ) -> dict:
        p = str(Path(repo_path).resolve())
        r_id = repo_id or f"repo_{fast_hash(p)[:8]}"
        clean_name = (repo_name or "").strip()
        if not clean_name:
            clean_name = Path(p).name.replace("_", " ").title()
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO repositories (repo_id, repo_name, repo_path, branch, corpus_version, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_name = excluded.repo_name,
                    repo_path = excluded.repo_path,
                    branch = excluded.branch
            """, (r_id, clean_name, p, branch, now))
            conn.commit()
        return self.get_repo(r_id) or {"repo_id": r_id, "repo_name": clean_name, "repo_path": p}

    def get_repo(self, repo_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM repositories WHERE repo_id = ?", (repo_id,)).fetchone()
            return dict(row) if row else None

    def get_repo_by_path(self, repo_path: str) -> Optional[dict]:
        p = str(Path(repo_path).resolve())
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM repositories WHERE repo_path = ?", (p,)).fetchone()
            return dict(row) if row else None

    def list_repos(self) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.cursor().execute(
                "SELECT * FROM repositories WHERE repo_name IS NOT NULL AND TRIM(repo_name) != '' ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def increment_corpus_version(self, repo_id: str) -> int:
        """Increments integer corpus_version for the repository when features change."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE repositories SET corpus_version = corpus_version + 1, last_indexed_at = ? WHERE repo_id = ?",
                           (datetime.now().isoformat(), repo_id))
            conn.commit()
            row = cursor.execute("SELECT corpus_version FROM repositories WHERE repo_id = ?", (repo_id,)).fetchone()
            return int(row["corpus_version"]) if row else 1

    def get_corpus_version(self, repo_id: str) -> int:
        repo = self.get_repo(repo_id)
        return int(repo["corpus_version"]) if repo and "corpus_version" in repo.keys() else 1

    def update_repo_corpus_version(self, repo_id: str) -> str:
        """Helper returning corpus version string for backward compatibility."""
        ver_int = self.get_corpus_version(repo_id)
        return f"v{ver_int}"

    def set_repo_indexing_status(
        self,
        repo_id: str,
        status: str,
        current_file: Optional[str] = None,
        progress_pct: int = 100
    ):
        """Updates repository index status (READY | INDEXING | ERROR) and active indexing filename."""
        with self._get_connection() as conn:
            conn.cursor().execute("""
                UPDATE repositories
                SET index_status = ?,
                    current_indexing_file = ?,
                    indexing_progress_pct = ?
                WHERE repo_id = ?
            """, (status, current_file or "", int(progress_pct), repo_id))
            conn.commit()

    def get_repo_indexing_status(self, repo_id: str) -> dict:
        """Gets index status, progress percentage, and current active file for repo."""
        repo = self.get_repo(repo_id)
        if not repo:
            return {
                "repo_id": repo_id,
                "index_status": "READY",
                "current_indexing_file": "",
                "indexing_progress_pct": 100,
                "scenario_count": 0,
                "corpus_version": 1,
            }
        return {
            "repo_id": repo["repo_id"],
            "repo_name": repo.get("repo_name", repo_id),
            "index_status": repo.get("index_status", "READY") or "READY",
            "current_indexing_file": repo.get("current_indexing_file", "") or "",
            "indexing_progress_pct": int(repo.get("indexing_progress_pct", 100) or 100),
            "scenario_count": int(repo.get("scenario_count", 0) or 0),
            "corpus_version": int(repo.get("corpus_version", 1) or 1),
            "last_indexed_at": repo.get("last_indexed_at"),
        }

    def delete_repo(self, repo_id: str):
        with self._get_connection() as conn:
            conn.cursor().execute("DELETE FROM repositories WHERE repo_id = ?", (repo_id,))
            conn.cursor().execute("DELETE FROM repo_folders WHERE repo_id = ?", (repo_id,))
            conn.cursor().execute("DELETE FROM scenarios WHERE repo_id = ?", (repo_id,))
            conn.cursor().execute("DELETE FROM feature_files WHERE repo_id = ?", (repo_id,))
            conn.cursor().execute("DELETE FROM semantic_cache WHERE repo_id = ?", (repo_id,))
            conn.commit()

    # ==================== Repo Folders (Multi-Folder Indexing) ====================

    def add_repo_folder(self, repo_id: str, folder_path: str or Path, scenario_count: int = 0) -> dict:
        p = str(Path(folder_path).resolve())
        folder_id = f"fld_{fast_hash(f'{repo_id}_{p}')[:10]}"
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO repo_folders (folder_id, repo_id, folder_path, scenario_count, last_indexed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, folder_path) DO UPDATE SET
                    scenario_count = excluded.scenario_count,
                    last_indexed_at = excluded.last_indexed_at
            """, (folder_id, repo_id, p, scenario_count, now, now))
            conn.commit()
        return {"folder_id": folder_id, "repo_id": repo_id, "folder_path": p, "scenario_count": scenario_count, "last_indexed_at": now}

    def list_repo_folders(self, repo_id: Optional[str] = None) -> List[dict]:
        with self._get_connection() as conn:
            if repo_id:
                rows = conn.cursor().execute(
                    "SELECT * FROM repo_folders WHERE repo_id = ? ORDER BY created_at ASC", (repo_id,)
                ).fetchall()
            else:
                rows = conn.cursor().execute("SELECT * FROM repo_folders ORDER BY created_at ASC").fetchall()
            return [dict(r) for r in rows]

    def delete_repo_folder(self, folder_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM repo_folders WHERE folder_id = ?", (folder_id,)).fetchone()
            if row:
                f_data = dict(row)
                conn.cursor().execute("DELETE FROM repo_folders WHERE folder_id = ?", (folder_id,))
                conn.commit()
                return f_data
            return None

    def update_folder_scenario_count(self, folder_id: str, count: int):
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE repo_folders SET scenario_count = ?, last_indexed_at = ? WHERE folder_id = ?",
                (count, now, folder_id)
            )
            conn.commit()

    def register_document(self, file_path: str or Path) -> dict:
        p = str(Path(file_path).resolve())
        fname = Path(file_path).name
        if Path(file_path).is_file():
            fhash = self.compute_file_hash(Path(file_path))
        else:
            fhash = fast_hash(p)
        doc_id = f"doc_{fast_hash(p)[:10]}"
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO documents (document_id, file_path, file_name, file_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    file_name = excluded.file_name
            """, (doc_id, p, fname, fhash, now))
            conn.commit()
        return {"document_id": doc_id, "file_path": p, "file_name": fname, "file_hash": fhash}

    def get_document(self, document_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            return dict(row) if row else None

    # ==================== Feature Files ====================

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        with open(file_path, "rb") as f:
            return fast_hash(f.read().decode("utf-8", errors="replace"))

    def get_feature_file(self, repo_id: str, file_path: str) -> Optional[dict]:
        p = str(Path(file_path).resolve())
        with self._get_connection() as conn:
            row = conn.cursor().execute(
                "SELECT * FROM feature_files WHERE repo_id = ? AND file_path = ?", (repo_id, p)
            ).fetchone()
            return dict(row) if row else None

    def get_file_record(self, file_path: str) -> Optional[dict]:
        """Backward compatible helper."""
        p = str(Path(file_path).resolve())
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM feature_files WHERE file_path = ?", (p,)).fetchone()
            return dict(row) if row else None

    def update_feature_file(
        self,
        repo_id: str,
        file_path: str or Path,
        file_hash: str,
        scenario_count: int,
        last_modified: Optional[str] = None,
    ) -> str:
        p = str(Path(file_path).resolve())
        file_id = f"ff_{fast_hash(f'{repo_id}#{p}')[:12]}"
        now = datetime.now().isoformat()
        mod_time = str(last_modified) if last_modified else now

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO feature_files (feature_file_id, repo_id, file_path, file_hash, scenario_count, last_modified, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, file_path) DO UPDATE SET
                    file_hash = excluded.file_hash,
                    scenario_count = excluded.scenario_count,
                    last_modified = excluded.last_modified,
                    indexed_at = excluded.indexed_at
            """, (file_id, repo_id, p, file_hash, scenario_count, mod_time, now))
            conn.commit()
        return file_id

    def update_file_record(self, file_path: str, file_hash: str, modified_time: float, scenario_count: int, repo_id: str = "default"):
        """Backward compatible helper."""
        self.update_feature_file(repo_id, file_path, file_hash, scenario_count, str(modified_time))

    def delete_feature_file(self, repo_id: str, file_path: str):
        p = str(Path(file_path).resolve())
        with self._get_connection() as conn:
            conn.cursor().execute("DELETE FROM feature_files WHERE repo_id = ? AND file_path = ?", (repo_id, p))
            conn.cursor().execute("DELETE FROM scenarios WHERE repo_id = ? AND file_path = ?", (repo_id, p))
            conn.commit()

    def delete_file_record(self, file_path: str):
        p = str(Path(file_path).resolve())
        with self._get_connection() as conn:
            conn.cursor().execute("DELETE FROM feature_files WHERE file_path = ?", (p,))
            conn.cursor().execute("DELETE FROM scenarios WHERE file_path = ?", (p,))
            conn.commit()

    # ==================== Scenarios (Storage & Hydration) ====================

    def save_scenarios(self, scenarios: List[ScenarioChunk], repo_id: Optional[str] = None):
        if not scenarios:
            return
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for s in scenarios:
                r_id = repo_id or s.repository_id or "default"
                c_hash = s.content_hash or fast_hash(s.canonical_text + s.raw_gherkin)
                ff_id = f"ff_{fast_hash(f'{r_id}#{s.file_path}')[:12]}"
                milvus_id = s.scenario_id  # Stable 1-to-1 match

                # Ensure feature_files parent record exists
                cursor.execute("""
                    INSERT INTO feature_files (feature_file_id, repo_id, file_path, file_hash, scenario_count, last_modified, indexed_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(repo_id, file_path) DO UPDATE SET
                        indexed_at = excluded.indexed_at
                """, (ff_id, s.repository_id, s.file_path, c_hash, now, now))

                cursor.execute("""
                    INSERT INTO scenarios (
                        scenario_id, feature_file_id, repo_id, file_path, line_number,
                        feature_name, scenario_name, scenario_type, tags_json,
                        canonical_text, raw_gherkin, content_hash, milvus_id, indexed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scenario_id) DO UPDATE SET
                        feature_file_id = excluded.feature_file_id,
                        repo_id = excluded.repo_id,
                        file_path = excluded.file_path,
                        line_number = excluded.line_number,
                        feature_name = excluded.feature_name,
                        scenario_name = excluded.scenario_name,
                        scenario_type = excluded.scenario_type,
                        tags_json = excluded.tags_json,
                        canonical_text = excluded.canonical_text,
                        raw_gherkin = excluded.raw_gherkin,
                        content_hash = excluded.content_hash,
                        milvus_id = excluded.milvus_id,
                        indexed_at = excluded.indexed_at
                """, (
                    s.scenario_id, ff_id, s.repository_id, s.file_path, s.line_number,
                    s.feature_name, s.scenario_name, s.scenario_type, json.dumps(s.tags),
                    s.canonical_text, s.raw_gherkin, c_hash, milvus_id, now
                ))

            # Update scenario count in repository
            cursor.execute("""
                UPDATE repositories SET
                    scenario_count = (SELECT COUNT(*) FROM scenarios WHERE repo_id = repositories.repo_id),
                    last_indexed_at = ?
                WHERE repo_id = ?
            """, (now, scenarios[0].repository_id))
            conn.commit()

    def get_scenario(self, scenario_id: str) -> Optional[dict]:
        """Retrieves raw scenario metadata dictionary from SQLite."""
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["tags"] = json.loads(d["tags_json"]) if d.get("tags_json") else []
            except Exception:
                d["tags"] = []
            return d

    def get_scenario_by_id(self, scenario_id: str) -> Optional[ScenarioChunk]:
        """Hydrates a single full ScenarioChunk with exact raw Gherkin evidence from SQLite."""
        with self._get_connection() as conn:
            row = conn.cursor().execute("SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)).fetchone()
            if not row:
                return None
            return self._row_to_scenario(row)

    def get_scenarios_by_ids(self, scenario_ids: List[str]) -> Dict[str, ScenarioChunk]:
        """Bulk hydrates full ScenarioChunk objects with raw Gherkin from SQLite."""
        if not scenario_ids:
            return {}
        placeholders = ",".join("?" for _ in scenario_ids)
        with self._get_connection() as conn:
            rows = conn.cursor().execute(
                f"SELECT * FROM scenarios WHERE scenario_id IN ({placeholders})", scenario_ids
            ).fetchall()
            return {r["scenario_id"]: self._row_to_scenario(r) for r in rows}

    def get_all_scenarios(self, repo_id: Optional[str] = None) -> List[ScenarioChunk]:
        with self._get_connection() as conn:
            if repo_id:
                rows = conn.cursor().execute(
                    "SELECT * FROM scenarios WHERE repo_id = ? ORDER BY file_path, line_number", (repo_id,)
                ).fetchall()
            else:
                rows = conn.cursor().execute(
                    "SELECT * FROM scenarios ORDER BY file_path, line_number"
                ).fetchall()
            return [self._row_to_scenario(r) for r in rows]

    def get_repo_scenarios(self, repo_id: str) -> List[ScenarioChunk]:
        """Convenience alias for get_all_scenarios(repo_id)."""
        return self.get_all_scenarios(repo_id=repo_id)

    def _row_to_scenario(self, r: sqlite3.Row) -> ScenarioChunk:
        feat = r["feature_name"] or ""
        return ScenarioChunk(
            scenario_id=r["scenario_id"],
            repository_id=r["repo_id"],
            file_path=r["file_path"],
            line_number=r["line_number"] if "line_number" in r.keys() else 1,
            feature_name=feat,
            feature_title=feat,
            scenario_name=r["scenario_name"],
            scenario_type=r["scenario_type"],
            tags=json.loads(r["tags_json"] or "[]"),
            canonical_text=r["canonical_text"] or "",
            raw_gherkin=r["raw_gherkin"] or "",
            content_hash=r["content_hash"],
        )

    # ==================== Analysis Sessions & Requirements ====================

    def create_analysis_session(
        self,
        repo_id: str,
        document_id: str,
        corpus_version: Optional[int] = None,
        session_name: Optional[str] = None,
    ) -> str:
        analysis_id = f"analysis_{uuid.uuid4().hex[:12]}"
        c_ver = corpus_version if corpus_version is not None else self.get_corpus_version(repo_id)
        now = datetime.now().isoformat()
        s_name = session_name or f"Analysis Run {now[:19]}"

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO analysis_sessions (
                    analysis_id, repo_id, document_id, corpus_version, status, session_name, created_at
                )
                VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?, ?)
            """, (analysis_id, repo_id, document_id, c_ver, s_name, now))
            conn.commit()
        return analysis_id

    def create_session(
        self,
        session_name: Optional[str] = None,
        repo_id: str = "default",
        docs_path: str = "",
        features_path: str = "",
        config: Optional[dict] = None,
    ) -> str:
        """Backward compatible session creation."""
        doc_record = self.register_document(docs_path) if docs_path and Path(docs_path).exists() else {"document_id": f"doc_{uuid.uuid4().hex[:8]}"}
        c_ver = self.get_corpus_version(repo_id)
        return self.create_analysis_session(
            repo_id=repo_id,
            document_id=doc_record["document_id"],
            corpus_version=c_ver,
            session_name=session_name,
        )

    def save_requirements(self, analysis_id: str, requirements: List[RequirementChunk]):
        if not requirements:
            return
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for idx, req in enumerate(requirements, start=1):
                cursor.execute("""
                    INSERT INTO requirements (
                        requirement_id, analysis_id, requirement_text, requirement_index,
                        title, category, source_file, line_number,
                        acceptance_criteria_json, business_rules_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(requirement_id) DO UPDATE SET
                        requirement_text = excluded.requirement_text,
                        title = excluded.title,
                        category = excluded.category,
                        acceptance_criteria_json = excluded.acceptance_criteria_json,
                        business_rules_json = excluded.business_rules_json
                """, (
                    req.req_id, analysis_id, req.full_text or req.description, idx,
                    req.title, req.category, req.source_file, req.line_number,
                    json.dumps(req.acceptance_criteria), json.dumps(req.business_rules), now
                ))
            conn.commit()

    def save_evaluations(
        self,
        analysis_id: str,
        verdict: Any,
        model_version: str = "default",
        prompt_version: str = "v1.0",
    ):
        v_dict = verdict.to_dict() if hasattr(verdict, "to_dict") else verdict
        now = datetime.now().isoformat()
        eval_id = f"eval_{uuid.uuid4().hex[:12]}"
        req_id = v_dict.get("req_id", "")
        primary = v_dict.get("primary_citation")
        scenario_id = primary.get("scenario_id") if primary else None

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO evaluations (
                    evaluation_id, requirement_id, scenario_id, status, match_percentage,
                    reasoning, evidence_json, citation_json, covered_criteria_json,
                    missing_gaps_json, suggested_tests_json, model_version, prompt_version, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                eval_id, req_id, scenario_id,
                v_dict.get("overall_classification", "NOT_COVERED"),
                v_dict.get("match_percentage", 0.0),
                v_dict.get("reasoning", ""),
                json.dumps(v_dict.get("citations", [])),
                json.dumps(primary),
                json.dumps(v_dict.get("covered_criteria", [])),
                json.dumps(v_dict.get("missing_gaps", [])),
                json.dumps(v_dict.get("suggested_tests", [])),
                model_version, prompt_version, now
            ))
            conn.commit()

    def record_evaluation(self, session_id: str, verdict: Any, repo_id: str = "default"):
        """Backward compatible helper."""
        self.save_evaluations(analysis_id=session_id, verdict=verdict)

    def complete_analysis_session(
        self,
        analysis_id: str,
        global_report: Any,
        report_files: Optional[dict] = None,
        status: str = "COMPLETED"
    ):
        rep_dict = global_report.to_dict() if hasattr(global_report, "to_dict") else global_report
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.cursor().execute("""
                UPDATE analysis_sessions SET
                    status = ?,
                    total_requirements = ?,
                    covered_count = ?,
                    partial_count = ?,
                    uncovered_count = ?,
                    average_match_pct = ?,
                    report_paths_json = ?,
                    completed_at = ?
                WHERE analysis_id = ?
            """, (
                status,
                rep_dict.get("total_requirements", 0),
                rep_dict.get("covered_count", 0),
                rep_dict.get("partial_count", 0),
                rep_dict.get("uncovered_count", 0),
                rep_dict.get("average_match_pct", 0.0),
                json.dumps(report_files or {}),
                now,
                analysis_id,
            ))
            conn.commit()

    def complete_session(self, session_id: str, global_report: Any, report_files: Optional[dict] = None, status: str = "COMPLETED"):
        self.complete_analysis_session(session_id, global_report, report_files, status)

    def list_analysis_sessions(self, limit: int = 20, repo_id: Optional[str] = None) -> List[dict]:
        with self._get_connection() as conn:
            if repo_id:
                rows = conn.cursor().execute(
                    "SELECT * FROM analysis_sessions WHERE repo_id = ? ORDER BY created_at DESC LIMIT ?", (repo_id, limit)
                ).fetchall()
            else:
                rows = conn.cursor().execute(
                    "SELECT * FROM analysis_sessions ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def list_sessions(self, limit: int = 20, repo_id: Optional[str] = None) -> List[dict]:
        sessions = self.list_analysis_sessions(limit=limit, repo_id=repo_id)
        # Adapt keys for backward compatibility
        for s in sessions:
            s["session_id"] = s["analysis_id"]
            s["started_at"] = s["created_at"]
        return sessions

    def get_analysis_session(self, analysis_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            s_row = conn.cursor().execute(
                "SELECT * FROM analysis_sessions WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            if not s_row:
                return None
            session = dict(s_row)
            session["session_id"] = session["analysis_id"]
            session["started_at"] = session["created_at"]

            req_rows = conn.cursor().execute(
                "SELECT * FROM requirements WHERE analysis_id = ? ORDER BY requirement_index", (analysis_id,)
            ).fetchall()
            session["requirements"] = [dict(r) for r in req_rows]

            eval_rows = conn.cursor().execute(
                """SELECT e.*, r.title AS req_title, r.category, r.source_file, r.line_number
                   FROM evaluations e
                   JOIN requirements r ON e.requirement_id = r.requirement_id
                   WHERE r.analysis_id = ? ORDER BY r.requirement_index""", (analysis_id,)
            ).fetchall()
            session["evaluations"] = [dict(r) for r in eval_rows]
            return session

    def get_session(self, session_id: str) -> Optional[dict]:
        return self.get_analysis_session(session_id)

    # ==================== Chat Sessions & Messages ====================

    def create_chat_session(
        self,
        repo_id: str,
        title: Optional[str] = None,
        analysis_id: Optional[str] = None,
    ) -> str:
        if not self.get_repo(repo_id):
            self.register_repo(repo_name=repo_id, repo_path=f"data/repos/{repo_id}", repo_id=repo_id)

        chat_id = f"chat_{uuid.uuid4().hex[:12]}"
        chat_title = title or f"Chat with {repo_id}"
        c_ver = self.get_corpus_version(repo_id)
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO chat_sessions (chat_id, repo_id, analysis_id, title, corpus_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (chat_id, repo_id, analysis_id, chat_title, c_ver, now, now))
            conn.commit()
        return chat_id

    def list_chat_sessions(self, repo_id: Optional[str] = None) -> List[dict]:
        with self._get_connection() as conn:
            if repo_id:
                rows = conn.cursor().execute(
                    "SELECT * FROM chat_sessions WHERE repo_id = ? ORDER BY updated_at DESC", (repo_id,)
                ).fetchall()
            else:
                rows = conn.cursor().execute("SELECT * FROM chat_sessions ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_chat_history(self, chat_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.cursor().execute(
                "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["citations"] = json.loads(d.get("citations_json") or "[]")
                trace_raw = d.get("agent_trace_json")
                d["agent_trace"] = json.loads(trace_raw) if trace_raw else None
                out.append(d)
            return out

    def add_chat_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        citations: Optional[List[dict]] = None,
        agent_trace: Optional[dict] = None,
    ):
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        trace_json = json.dumps(agent_trace) if agent_trace else None
        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO chat_messages (message_id, chat_id, role, content, citations_json, agent_trace_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (msg_id, chat_id, role, content, json.dumps(citations or []), trace_json, now))
            conn.cursor().execute("UPDATE chat_sessions SET updated_at = ? WHERE chat_id = ?", (now, chat_id))
            conn.commit()

    # ==================== Vector Semantic Cache ====================

    @staticmethod
    def generate_cache_key(
        repo_id: str,
        corpus_version: int,
        requirement_text: str,
        candidate_ids: List[str],
        retrieval_version: str = "v1.0",
        reranker_version: str = "v1.0",
        prompt_version: str = "v1.0",
        model_version: str = "default",
    ) -> Tuple[str, str, str]:
        req_hash = fast_hash(requirement_text.strip())
        cand_str = ":".join(sorted(candidate_ids))
        cand_hash = fast_hash(cand_str)
        composite = f"{repo_id}_{corpus_version}_{req_hash}_{cand_hash}_{retrieval_version}_{reranker_version}_{prompt_version}_{model_version}"
        cache_key = fast_hash(composite)
        return cache_key, req_hash, cand_hash

    def get_cached_judgment(
        self,
        requirement_text: str,
        candidate_ids: List[str],
        provider: str,
        repo_id: str = "default",
        corpus_version: Optional[int or str] = None,
        retrieval_version: str = "v1.0",
        reranker_version: str = "v1.0",
        prompt_version: str = "v1.0",
        model_version: Optional[str] = None,
        embedding_version: Optional[str] = None,
        requirement_embedding: Optional[Any] = None,
        similarity_threshold: float = 0.88,
    ) -> Optional[dict]:
        """Retrieves cached judgment via O(1) hash or dense vector cosine similarity."""
        c_ver = int(str(corpus_version).lstrip("v")) if corpus_version is not None else self.get_corpus_version(repo_id)
        m_ver = model_version or provider
        cache_key, req_hash, cand_hash = self.generate_cache_key(
            repo_id=repo_id,
            corpus_version=c_ver,
            requirement_text=requirement_text,
            candidate_ids=candidate_ids,
            retrieval_version=retrieval_version,
            reranker_version=reranker_version,
            prompt_version=prompt_version,
            model_version=m_ver,
        )
        with self._get_connection() as conn:
            # 1. Exact hash lookup (Fast path)
            row = conn.cursor().execute(
                "SELECT judgment_json FROM semantic_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            if row:
                try:
                    res = json.loads(row["judgment_json"])
                    if isinstance(res, dict):
                        res["_cache_similarity"] = 1.0
                    return res
                except Exception:
                    pass

            # 2. Vector Semantic Similarity Lookup (Dense Embedding Cosine Match)
            if requirement_embedding is not None:
                import numpy as np
                q_vec = np.array(requirement_embedding, dtype=np.float32).flatten()
                q_norm = np.linalg.norm(q_vec)
                if q_norm > 1e-6:
                    rows = conn.cursor().execute("""
                        SELECT judgment_json, embedding_blob, candidates_hash
                        FROM semantic_cache
                        WHERE repo_id = ? AND corpus_version = ? AND embedding_blob IS NOT NULL
                    """, (repo_id, c_ver)).fetchall()

                    best_sim = 0.0
                    best_judgment = None

                    for r in rows:
                        try:
                            # Require candidate scenarios to match or overlap closely
                            if r["candidates_hash"] != cand_hash and cand_hash:
                                continue
                            c_blob = r["embedding_blob"]
                            if not c_blob:
                                continue
                            c_vec = np.frombuffer(c_blob, dtype=np.float32)
                            c_norm = np.linalg.norm(c_vec)
                            if c_norm < 1e-6:
                                continue
                            cos_sim = float(np.dot(q_vec, c_vec) / (q_norm * c_norm))
                            if cos_sim > best_sim:
                                best_sim = cos_sim
                                best_judgment = json.loads(r["judgment_json"])
                        except Exception:
                            continue

                    if best_sim >= similarity_threshold and best_judgment is not None:
                        if isinstance(best_judgment, dict):
                            best_judgment["_cache_similarity"] = round(best_sim, 3)
                        return best_judgment

        return None

    def store_cached_judgment(
        self,
        requirement_text: str,
        candidate_ids: List[str],
        provider: str,
        judgment: dict,
        repo_id: str = "default",
        corpus_version: Optional[int or str] = None,
        retrieval_version: str = "v1.0",
        reranker_version: str = "v1.0",
        prompt_version: str = "v1.0",
        model_version: Optional[str] = None,
        embedding_version: Optional[str] = None,
        requirement_embedding: Optional[Any] = None,
    ):
        """Stores judgment in multi-factor semantic cache with dense embedding vector."""
        c_ver = int(str(corpus_version).lstrip("v")) if corpus_version is not None else self.get_corpus_version(repo_id)
        m_ver = model_version or provider
        cache_key, req_hash, cand_hash = self.generate_cache_key(
            repo_id=repo_id,
            corpus_version=c_ver,
            requirement_text=requirement_text,
            candidate_ids=candidate_ids,
            retrieval_version=retrieval_version,
            reranker_version=reranker_version,
            prompt_version=prompt_version,
            model_version=m_ver,
        )
        now = datetime.now().isoformat()
        
        emb_bytes = None
        if requirement_embedding is not None:
            try:
                import numpy as np
                emb_bytes = np.array(requirement_embedding, dtype=np.float32).flatten().tobytes()
            except Exception:
                emb_bytes = None

        with self._get_connection() as conn:
            conn.cursor().execute("""
                INSERT INTO semantic_cache (
                    cache_key, repo_id, corpus_version, requirement_hash, candidates_hash,
                    retrieval_version, reranker_version, prompt_version, model_version, judgment_json,
                    embedding_blob, requirement_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    judgment_json = excluded.judgment_json,
                    embedding_blob = excluded.embedding_blob,
                    requirement_text = excluded.requirement_text,
                    created_at = excluded.created_at
            """, (
                cache_key, repo_id, c_ver, req_hash, cand_hash,
                retrieval_version, reranker_version, prompt_version, m_ver,
                json.dumps(judgment), emb_bytes, requirement_text[:1000], now
            ))
            conn.commit()

    def clear_all_sessions(self):
        """Drops all evaluation runs, requirements, evaluations, chat sessions, and semantic cache from SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages;")
            cursor.execute("DELETE FROM chat_sessions;")
            cursor.execute("DELETE FROM evaluations;")
            cursor.execute("DELETE FROM requirements;")
            cursor.execute("DELETE FROM analysis_sessions;")
            cursor.execute("DELETE FROM semantic_cache;")
            conn.commit()

    def clear_chat_sessions(self, repo_id: Optional[str] = None):
        """Clears chat history, sessions, and semantic cache from SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute("""
                    DELETE FROM chat_messages 
                    WHERE chat_id IN (SELECT chat_id FROM chat_sessions WHERE repo_id = ?)
                """, (repo_id,))
                cursor.execute("DELETE FROM chat_sessions WHERE repo_id = ?", (repo_id,))
                cursor.execute("DELETE FROM semantic_cache WHERE repo_id = ?", (repo_id,))
            else:
                cursor.execute("DELETE FROM chat_messages;")
                cursor.execute("DELETE FROM chat_sessions;")
                cursor.execute("DELETE FROM semantic_cache;")
            conn.commit()

    def clear_semantic_cache(self, repo_id: Optional[str] = None):
        """Clears semantic judgment cache from SQLite for a specific repo or all repos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if repo_id:
                cursor.execute("DELETE FROM semantic_cache WHERE repo_id = ?", (repo_id,))
            else:
                cursor.execute("DELETE FROM semantic_cache;")
            conn.commit()

    def delete_chat_session(self, chat_id: str) -> bool:
        """Deletes a specific chat session, its messages, and associated semantic cache entries."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Invalidate semantic cache entries associated with this session's user questions
            rows = cursor.execute("SELECT content FROM chat_messages WHERE chat_id = ? AND role = 'user';", (chat_id,)).fetchall()
            for r in rows:
                query_text = r["content"] if isinstance(r, sqlite3.Row) else r[0]
                if query_text:
                    req_hash = fast_hash(query_text.strip())
                    cursor.execute("DELETE FROM semantic_cache WHERE requirement_hash = ? OR requirement_text = ?;", (req_hash, query_text))

            # 2. Delete messages and session
            cursor.execute("DELETE FROM chat_messages WHERE chat_id = ?;", (chat_id,))
            cursor.execute("DELETE FROM chat_sessions WHERE chat_id = ?;", (chat_id,))
            conn.commit()
            return True

    def delete_analysis_session(self, session_id: str) -> bool:
        """Deletes a specific evaluation analysis session, requirements, and evaluations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM evaluations 
                WHERE requirement_id IN (SELECT requirement_id FROM requirements WHERE analysis_id = ?);
            """, (session_id,))
            cursor.execute("DELETE FROM requirements WHERE analysis_id = ?;", (session_id,))
            cursor.execute("DELETE FROM analysis_sessions WHERE analysis_id = ?;", (session_id,))
            conn.commit()
            return True
