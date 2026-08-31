"""Filesystem Watcher for real-time Gherkin feature file indexing.

Monitors automation repository directories for .feature file modifications,
deletions, and additions, automatically synchronizing BM25, Milvus, and SQLite state.
"""

from pathlib import Path
import time
import threading
from typing import Callable, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent, FileMovedEvent
from src.parsers.gherkin_parser import GherkinParser, UniversalFileParser
from src.index.bm25_index import BM25Index
from src.index.milvus_store import MilvusStore
from src.index.embedding_model import EmbeddingModel
from src.storage.state_db import StateDatabase, fast_hash


class FeatureFileEventHandler(FileSystemEventHandler):
    """Event handler for all repository file modifications with robust trailing-edge debouncing."""

    def __init__(
        self,
        on_change_callback: Callable[[str, str], None],
        debounce_seconds: float = 1.0,
    ):
        super().__init__()
        self.on_change_callback = on_change_callback
        self.debounce_seconds = debounce_seconds
        self._timers: dict = {}
        self._pending_events: dict = {}
        self._lock = threading.Lock()

    def _schedule_event(self, file_path: str, event_type: str):
        if not UniversalFileParser.is_indexable(file_path):
            return

        with self._lock:
            # Cancel existing timer for this file if new write event arrives
            if file_path in self._timers:
                try:
                    self._timers[file_path].cancel()
                except Exception:
                    pass

            prev = self._pending_events.get(file_path)
            if prev == "created" and event_type == "modified":
                self._pending_events[file_path] = "created"
            else:
                self._pending_events[file_path] = event_type

            # Trailing-edge debounce timer: fires once file writes have settled
            timer = threading.Timer(
                self.debounce_seconds,
                self._dispatch_event,
                args=[file_path],
            )
            timer.daemon = True
            self._timers[file_path] = timer
            timer.start()

    def _dispatch_event(self, file_path: str):
        with self._lock:
            event_type = self._pending_events.pop(file_path, "modified")
            self._timers.pop(file_path, None)

        try:
            self.on_change_callback(file_path, event_type)
        except Exception as e:
            print(f"[Watcher] Callback exception for '{file_path}': {e}")

    def on_created(self, event):
        if event.is_directory:
            try:
                for p in Path(event.src_path).rglob("*"):
                    if p.is_file() and UniversalFileParser.is_indexable(p):
                        self._schedule_event(str(p), "created")
            except Exception:
                pass
        else:
            self._schedule_event(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule_event(event.src_path, "modified")

    def on_deleted(self, event):
        if not event.is_directory and UniversalFileParser.is_indexable(event.src_path):
            self._schedule_event(event.src_path, "deleted")

    def on_moved(self, event):
        if not event.is_directory:
            if UniversalFileParser.is_indexable(event.src_path):
                self._schedule_event(event.src_path, "deleted")
            if UniversalFileParser.is_indexable(event.dest_path):
                self._schedule_event(event.dest_path, "created")


class FeatureRepositoryWatcher:
    """Monitors feature repository directory and synchronizes SQLite, BM25 + Milvus indices."""

    def __init__(
        self,
        watch_dir: str or Path,
        bm25_index: BM25Index,
        milvus_store: MilvusStore,
        embedding_model: EmbeddingModel,
        state_db: Optional[StateDatabase] = None,
        repo_id: str = "default",
        debounce_seconds: float = 1.0,
        on_reindex_done: Optional[Callable[[str, int], None]] = None,
    ):
        self.watch_dir = Path(watch_dir)
        self.bm25_index = bm25_index
        self.milvus_store = milvus_store
        self.embedding_model = embedding_model
        self.state_db = state_db
        self.repo_id = repo_id
        self.debounce_seconds = debounce_seconds
        self.on_reindex_done = on_reindex_done
        self.observer: Optional[Observer] = None
        self._running = False

    def handle_file_change(self, file_path: str, event_type: str) -> None:
        """Processes file event and synchronizes indices."""
        path = Path(file_path)
        p_resolved = str(path.resolve())
        print(f"[Watcher] File event detected: '{event_type}' on '{path.name}' (Repo: {self.repo_id})")

        scenarios_count = 0
        if event_type in ("created", "modified") and path.exists():
            try:
                # Retry loop to gracefully handle Windows in-flight file writes and locks
                f_text = ""
                for attempt in range(5):
                    try:
                        if not path.exists():
                            return
                        f_text = path.read_text(encoding="utf-8", errors="ignore")
                        if f_text.strip():
                            break
                        time.sleep(0.1)
                    except (PermissionError, IOError):
                        time.sleep(0.15)

                if not f_text.strip():
                    print(f"[Watcher] Warning: File '{path.name}' is empty or unreadable after write.")
                    return

                new_hash = fast_hash(f_text)

                # Authoritative change check: skip if hash unchanged
                if self.state_db:
                    existing = self.state_db.get_feature_file(self.repo_id, p_resolved)
                    if existing and existing.get("file_hash") == new_hash:
                        print(f"[Watcher] File '{path.name}' hash unchanged. Index is up to date.")
                        return

                if self.state_db:
                    self.state_db.set_repo_indexing_status(self.repo_id, "INDEXING", current_file=path.name, progress_pct=30)

                # 1. Remove stale entries from Milvus and BM25
                self.milvus_store.delete_by_file(p_resolved)
                self.bm25_index.remove_by_file(p_resolved)

                scenarios = UniversalFileParser.parse_file(path, repo_id=self.repo_id)
                if scenarios:
                    texts = [s.full_text for s in scenarios]
                    embeddings = self.embedding_model.encode(texts)
                    self.milvus_store.upsert(scenarios, embeddings)

                    # Update BM25 with clean deduplicated set
                    all_scenarios = [
                        s for s in self.bm25_index.scenarios
                        if str(Path(s.file_path).resolve()) != p_resolved and s.file_path != str(path)
                    ] + scenarios
                    self.bm25_index.index_scenarios(all_scenarios)
                    scenarios_count = len(scenarios)

                    if self.state_db:
                        try:
                            self.state_db.update_feature_file(
                                file_path=p_resolved,
                                repo_id=self.repo_id,
                                file_hash=new_hash,
                                scenario_count=scenarios_count,
                            )
                            self.state_db.save_scenarios(scenarios, repo_id=self.repo_id)
                            new_ver = self.state_db.increment_corpus_version(self.repo_id)
                            print(f"[Watcher] Updated SQLite metadata. Repo '{self.repo_id}' corpus bumped to v{new_ver}.")
                        except Exception as se:
                            print(f"[Watcher] State DB update notice: {se}")

                if self.state_db:
                    self.state_db.set_repo_indexing_status(self.repo_id, "READY", current_file="", progress_pct=100)

                print(f"[Watcher] Successfully synchronized {scenarios_count} item(s) from '{path.name}'.")
            except Exception as e:
                print(f"[Watcher] Error indexing {file_path}: {e}")
                if self.state_db:
                    self.state_db.set_repo_indexing_status(self.repo_id, "ERROR", current_file="", progress_pct=100)

        elif event_type == "deleted":
            if self.state_db:
                self.state_db.set_repo_indexing_status(self.repo_id, "INDEXING", current_file=path.name, progress_pct=50)

            self.milvus_store.delete_by_file(p_resolved)
            self.bm25_index.remove_by_file(p_resolved)

            if self.state_db:
                try:
                    self.state_db.delete_feature_file(self.repo_id, p_resolved)
                    new_ver = self.state_db.increment_corpus_version(self.repo_id)
                    print(f"[Watcher] Deleted '{path.name}'. Repo '{self.repo_id}' corpus bumped to v{new_ver}.")
                except Exception as se:
                    print(f"[Watcher] State DB delete notice: {se}")
                self.state_db.set_repo_indexing_status(self.repo_id, "READY", current_file="", progress_pct=100)

        if self.on_reindex_done:
            self.on_reindex_done(file_path, scenarios_count)

    def start(self, blocking: bool = False) -> None:
        """Starts watching the directory."""
        if self._running:
            return

        self.watch_dir.mkdir(parents=True, exist_ok=True)
        event_handler = FeatureFileEventHandler(
            on_change_callback=self.handle_file_change,
            debounce_seconds=self.debounce_seconds,
        )
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.watch_dir), recursive=True)
        self.observer.start()
        self._running = True
        print(f"[Watcher] Started real-time monitoring on '{self.watch_dir}' (Repo: {self.repo_id}).")

        if blocking:
            try:
                while self._running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> None:
        """Stops watching."""
        if self.observer and self._running:
            self.observer.stop()
            self.observer.join()
            self._running = False
            print("[Watcher] Stopped monitoring.")


class InProcessWatchdogManager:
    """Manages multi-directory filesystem watching directly within the application process."""

    def __init__(
        self,
        bm25_index: BM25Index,
        milvus_store: MilvusStore,
        embedding_model: EmbeddingModel,
        state_db: Optional[StateDatabase] = None,
        debounce_seconds: float = 1.5,
    ):
        self.bm25_index = bm25_index
        self.milvus_store = milvus_store
        self.embedding_model = embedding_model
        self.state_db = state_db
        self.debounce_seconds = debounce_seconds

        self.observer: Optional[Observer] = None
        self._running = False
        self._watched_paths: dict = {}  # {str(path): {"repo_id": str, "watch": ...}}
        self._events_log: list = []  # Ring buffer of recent change events
        self._lock = threading.Lock()

    def _log_event(self, event_type: str, file_path: str, repo_id: str, scenarios_count: int = 0):
        now_str = time.strftime("%H:%M:%S")
        entry = {
            "time": now_str,
            "event_type": event_type,
            "file_name": Path(file_path).name,
            "file_path": file_path,
            "repo_id": repo_id,
            "scenarios_count": scenarios_count,
            "message": f"[{now_str}]   File {event_type}: {Path(file_path).name} (Repo: {repo_id}, {scenarios_count} scenarios re-indexed)",
        }
        with self._lock:
            self._events_log.insert(0, entry)
            if len(self._events_log) > 50:
                self._events_log.pop()

    def handle_file_change(self, file_path: str, event_type: str, repo_id: str = "default") -> None:
        path = Path(file_path)
        p_resolved = str(path.resolve())
        print(f"[InProcessWatchdog] File event '{event_type}' on '{path.name}' (Repo: {repo_id})")

        scenarios_count = 0
        if event_type in ("created", "modified") and path.exists():
            try:
                # Retry loop to gracefully handle Windows in-flight file writes and locks
                f_text = ""
                for attempt in range(5):
                    try:
                        if not path.exists():
                            return
                        f_text = path.read_text(encoding="utf-8", errors="ignore")
                        if f_text.strip():
                            break
                        time.sleep(0.1)
                    except (PermissionError, IOError):
                        time.sleep(0.15)

                if not f_text.strip():
                    print(f"[InProcessWatchdog] Warning: File '{path.name}' is empty or unreadable after write.")
                    return

                new_hash = fast_hash(f_text)

                # Authoritative change check: skip if hash unchanged
                if self.state_db:
                    existing = self.state_db.get_feature_file(repo_id, p_resolved)
                    if existing and existing.get("file_hash") == new_hash:
                        print(f"[InProcessWatchdog] File '{path.name}' hash unchanged. Index is up to date.")
                        return

                if self.state_db:
                    self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file=path.name, progress_pct=30)

                self.milvus_store.delete_by_file(p_resolved)
                self.bm25_index.remove_by_file(p_resolved)

                scenarios = UniversalFileParser.parse_file(path, repo_id=repo_id)
                if scenarios:
                    texts = [s.full_text for s in scenarios]
                    embeddings = self.embedding_model.encode(texts)
                    self.milvus_store.upsert(scenarios, embeddings)

                    # Update BM25 with clean deduplicated set
                    all_scenarios = [
                        s for s in self.bm25_index.scenarios
                        if str(Path(s.file_path).resolve()) != p_resolved and s.file_path != str(path)
                    ] + scenarios
                    self.bm25_index.index_scenarios(all_scenarios)
                    scenarios_count = len(scenarios)

                    if self.state_db:
                        try:
                            self.state_db.update_feature_file(
                                file_path=p_resolved,
                                repo_id=repo_id,
                                file_hash=new_hash,
                                scenario_count=scenarios_count,
                            )
                            self.state_db.save_scenarios(scenarios, repo_id=repo_id)
                            new_ver = self.state_db.increment_corpus_version(repo_id)
                            print(f"[InProcessWatchdog] Updated SQLite metadata. Repo '{repo_id}' corpus bumped to v{new_ver}.")
                            self.bm25_index.index_scenarios(self.state_db.get_all_scenarios())
                        except Exception as se:
                            print(f"[InProcessWatchdog] DB update error: {se}")

                if self.state_db:
                    self.state_db.set_repo_indexing_status(repo_id, "READY", current_file="", progress_pct=100)

            except Exception as e:
                print(f"[InProcessWatchdog] Error indexing {file_path}: {e}")
                if self.state_db:
                    self.state_db.set_repo_indexing_status(repo_id, "ERROR", current_file="", progress_pct=100)

        elif event_type == "deleted":
            if self.state_db:
                self.state_db.set_repo_indexing_status(repo_id, "INDEXING", current_file=path.name, progress_pct=50)

            self.milvus_store.delete_by_file(p_resolved)
            self.bm25_index.remove_by_file(p_resolved)

            if self.state_db:
                try:
                    self.state_db.delete_feature_file(repo_id, p_resolved)
                    new_ver = self.state_db.increment_corpus_version(repo_id)
                    print(f"[InProcessWatchdog] Deleted '{path.name}'. Repo '{repo_id}' corpus bumped to v{new_ver}.")
                except Exception as se:
                    print(f"[InProcessWatchdog] Delete error: {se}")
                self.state_db.set_repo_indexing_status(repo_id, "READY", current_file="", progress_pct=100)

        self._log_event(event_type=event_type, file_path=file_path, repo_id=repo_id, scenarios_count=scenarios_count)

    def start(self) -> None:
        """Starts the background observer."""
        if self._running:
            return
        self.observer = Observer()
        self.observer.start()
        self._running = True
        print("[InProcessWatchdog] Background watchdog engine started.")

    def add_watch_directory(self, folder_path: str or Path, repo_id: str = "default") -> bool:
        """Dynamically registers and watches a new directory path, syncing any unindexed files."""
        p = Path(folder_path).resolve()
        if not p.exists() or not p.is_dir():
            return False

        p_str = str(p)
        with self._lock:
            if p_str in self._watched_paths:
                return True

            if not self._running or not self.observer:
                self.start()

            handler = FeatureFileEventHandler(
                on_change_callback=lambda fp, et: self.handle_file_change(fp, et, repo_id=repo_id),
                debounce_seconds=self.debounce_seconds,
            )
            watch = self.observer.schedule(handler, p_str, recursive=True)
            self._watched_paths[p_str] = {"repo_id": repo_id, "watch": watch}
            print(f"[InProcessWatchdog] Watching directory: '{p_str}' for repo '{repo_id}'")

        # Initial sync for any existing/new files in the added directory
        try:
            for feat_file in p.rglob("*"):
                if feat_file.is_file() and UniversalFileParser.is_indexable(feat_file):
                    try:
                        self.handle_file_change(str(feat_file), "created", repo_id=repo_id)
                    except Exception as fe:
                        print(f"[InProcessWatchdog] Notice syncing existing file {feat_file.name}: {fe}")
        except Exception as e:
            print(f"[InProcessWatchdog] Directory initial sync notice: {e}")

        return True

    def remove_watch_directory(self, folder_path: str or Path) -> bool:
        """Stops watching a directory."""
        p_str = str(Path(folder_path).resolve())
        with self._lock:
            if p_str in self._watched_paths and self.observer:
                try:
                    watch_info = self._watched_paths.pop(p_str)
                    self.observer.unschedule(watch_info["watch"])
                    print(f"[InProcessWatchdog] Stopped watching directory: '{p_str}'")
                    return True
                except Exception as e:
                    print(f"[InProcessWatchdog] Error unscheduling {p_str}: {e}")
            return False

    def get_status(self) -> dict:
        """Returns live monitoring status and recent event log."""
        with self._lock:
            return {
                "running": self._running,
                "watched_count": len(self._watched_paths),
                "watched_directories": [
                    {"path": p, "repo_id": info["repo_id"]} for p, info in self._watched_paths.items()
                ],
                "recent_events": list(self._events_log[:15]),
            }

    def stop(self) -> None:
        """Stops the observer."""
        if self.observer and self._running:
            self.observer.stop()
            self.observer.join()
            self._running = False
            self._watched_paths.clear()
            print("[InProcessWatchdog] Background watchdog engine stopped.")
