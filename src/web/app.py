"""FastAPI Web Application & RAG Chatbot Dashboard with Integrated Indexer & Multi-Folder Watchdog."""


import os
import sys
import json
import shutil
import time
from pathlib import Path
from typing import Optional, List, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.pipeline import RAGCoveragePipeline
from src.config import load_config
from src.watcher.fs_watcher import InProcessWatchdogManager

app = FastAPI(title="Local RAG BDD Test Automation & Coverage Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipeline: Optional[RAGCoveragePipeline] = None
watchdog_mgr: Optional[InProcessWatchdogManager] = None


def get_pipeline() -> RAGCoveragePipeline:
    global pipeline, watchdog_mgr
    if pipeline is None:
        cfg = load_config()
        pipeline = RAGCoveragePipeline(config=cfg)

        # Initialize In-Process Watchdog Manager
        watchdog_mgr = InProcessWatchdogManager(
            bm25_index=pipeline.bm25_index,
            milvus_store=pipeline.milvus_store,
            embedding_model=pipeline.embedding_model,
            state_db=pipeline.state_db,
            debounce_seconds=1.5,
        )
        watchdog_mgr.start()

        # Sync configured repos from config.yaml or sample_data
        if cfg.repositories:
            for r_cfg in cfg.repositories:
                r_path = Path(r_cfg.path)
                if r_path.exists():
                    pipeline.index_features(
                        feature_dir=r_path.resolve(),
                        repo_id=r_cfg.id,
                        repo_name=r_cfg.name
                    )
        elif Path("sample_data/feature_repos").exists():
            pipeline.index_features(
                feature_dir=Path("sample_data/feature_repos").resolve(),
                repo_id="repo_1",
                repo_name="Reach Automation"
            )

        # Attach all active repo folders to Watchdog
        try:
            active_folders = pipeline.state_db.list_repo_folders()
            for f in active_folders:
                f_path = Path(f["folder_path"])
                if f_path.exists() and f_path.is_dir():
                    watchdog_mgr.add_watch_directory(f_path, repo_id=f["repo_id"])
        except Exception as e:
            print(f"[Startup] Watchdog folder registration notice: {e}")

    return pipeline


def get_watchdog_mgr() -> InProcessWatchdogManager:
    global watchdog_mgr
    if watchdog_mgr is None:
        get_pipeline()
    return watchdog_mgr


# ==================== Pydantic Models ====================

class RepoRegisterRequest(BaseModel):
    repo_name: str
    repo_id: Optional[str] = None
    repo_path: Optional[str] = ""
    branch: Optional[str] = "main"


class FolderAddRequest(BaseModel):
    folder_path: str


class ChatRequest(BaseModel):
    message: str
    repo_id: str = "repo_1"
    chat_id: Optional[str] = None
    bypass_cache: bool = False


class NewChatSessionRequest(BaseModel):
    repo_id: str
    title: Optional[str] = None


class OpenFileLocationRequest(BaseModel):
    file_path: str
    line_number: Optional[int] = 1


# ==================== REST API Endpoints ====================

# 1. Repositories
@app.get("/api/repos")
async def list_repositories():
    p = get_pipeline()
    repos = p.repo_manager.list_repositories()
    return {"status": "success", "repositories": repos}


@app.post("/api/repos")
async def register_repository(req: RepoRegisterRequest):
    p = get_pipeline()
    wm = get_watchdog_mgr()
    try:
        raw_path = (req.repo_path or "").strip().strip('"').strip("'").strip()
        repo_info = p.repo_manager.add_repository(
            repo_name=req.repo_name.strip(),
            repo_path=raw_path,
            repo_id=req.repo_id.strip() if req.repo_id else None,
            branch=req.branch or "main",
        )
        count = 0
        if raw_path:
            p_dir = Path(raw_path).resolve()
            if not p_dir.exists():
                try:
                    p_dir.mkdir(parents=True, exist_ok=True)
                except Exception as me:
                    print(f"[API] Notice creating repo path: {me}")
            if p_dir.exists() and p_dir.is_dir():
                count = p.index_features(
                    feature_dir=p_dir,
                    repo_id=repo_info["repo_id"],
                    repo_name=repo_info["repo_name"]
                )
                p.state_db.add_repo_folder(
                    repo_id=repo_info["repo_id"],
                    folder_path=str(p_dir),
                    scenario_count=count
                )
                wm.add_watch_directory(p_dir, repo_id=repo_info["repo_id"])

        return {"status": "success", "repository": repo_info, "scenarios_indexed": count}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/repos/{repo_id}")
async def delete_repository(repo_id: str):
    p = get_pipeline()
    wm = get_watchdog_mgr()
    try:
        folders = p.state_db.list_repo_folders(repo_id)
        for f in folders:
            wm.remove_watch_directory(f["folder_path"])
        p.repo_manager.delete_repository(repo_id)
        return {"status": "success", "deleted_repo_id": repo_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/repos/{repo_id}/reindex")
async def reindex_repository(repo_id: str):
    p = get_pipeline()
    try:
        count = p.index_repo_folders(repo_id=repo_id, force_reindex=True)
        return {"status": "success", "repo_id": repo_id, "scenarios_indexed": count}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/repos/{repo_id}/status")
async def get_repository_status(repo_id: str):
    p = get_pipeline()
    status = p.state_db.get_repo_indexing_status(repo_id)
    return {"status": "success", "repo_id": repo_id, "index_status": status}


# 2. Multi-Folder Management
@app.get("/api/repos/{repo_id}/folders")
async def list_repo_folders(repo_id: str):
    p = get_pipeline()
    folders = p.state_db.list_repo_folders(repo_id=repo_id)
    return {"status": "success", "repo_id": repo_id, "folders": folders}


@app.post("/api/repos/{repo_id}/folders")
async def add_folder_to_repo(repo_id: str, req: FolderAddRequest):
    p = get_pipeline()
    wm = get_watchdog_mgr()
    raw_path = req.folder_path.strip().strip('"').strip("'").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Folder path cannot be empty.")

    folder_path = Path(raw_path).resolve()
    if not folder_path.exists():
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as ce:
            raise HTTPException(status_code=400, detail=f"Directory does not exist and could not be created: {raw_path} ({ce})")

    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Specified path is not a directory: {raw_path}")

    # Ensure repo is registered in repositories table
    try:
        repo = p.repo_manager.get_repository(repo_id)
        if not repo:
            p.repo_manager.add_repository(repo_name=repo_id, repo_path=str(folder_path), repo_id=repo_id)
    except Exception as re:
        print(f"[API] Repo ensure notice: {re}")

    try:
        # 1. Parse and index features/documents in this folder
        scenarios = p.index_features(
            feature_dir=folder_path,
            repo_id=repo_id,
            force_reindex=True
        )

        # 2. Save in SQLite repo_folders
        folder_info = p.state_db.add_repo_folder(
            repo_id=repo_id,
            folder_path=str(folder_path),
            scenario_count=scenarios
        )

        # 3. Attach In-Process Watchdog for real-time monitoring
        wm.add_watch_directory(folder_path, repo_id=repo_id)

        return {
            "status": "success",
            "folder": folder_info,
            "scenarios_indexed": scenarios,
            "watching": True
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: str):
    p = get_pipeline()
    wm = get_watchdog_mgr()
    try:
        f_data = p.state_db.delete_repo_folder(folder_id)
        if f_data:
            wm.remove_watch_directory(f_data["folder_path"])
        return {"status": "success", "deleted_folder_id": folder_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/folders/{folder_id}/reindex")
async def reindex_folder(folder_id: str):
    p = get_pipeline()
    with p.state_db._get_connection() as conn:
        row = conn.cursor().execute("SELECT * FROM repo_folders WHERE folder_id = ?", (folder_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Folder not found")
        f_data = dict(row)

    count = p.index_features(
        feature_dir=f_data["folder_path"],
        repo_id=f_data["repo_id"],
        force_reindex=True
    )
    p.state_db.update_folder_scenario_count(folder_id, count)
    return {"status": "success", "folder_id": folder_id, "scenarios_indexed": count}


# 3. Watchdog Telemetry & Live Activity Events
@app.get("/api/watcher/status")
async def get_watcher_status():
    wm = get_watchdog_mgr()
    return {"status": "success", "watcher": wm.get_status()}


# 4. Chat & Scenarios
@app.get("/api/chat-sessions")
async def list_chat_sessions(repo_id: Optional[str] = None):
    p = get_pipeline()
    sessions = p.state_db.list_chat_sessions(repo_id=repo_id)
    return {"status": "success", "chat_sessions": sessions}


@app.post("/api/chat-sessions/new")
async def create_new_chat_session(req: NewChatSessionRequest):
    p = get_pipeline()
    chat_id = p.state_db.create_chat_session(repo_id=req.repo_id, title=req.title or "New Conversation")
    return {"status": "success", "chat_id": chat_id, "repo_id": req.repo_id}


@app.post("/api/open-file-location")
async def open_file_location(req: OpenFileLocationRequest):
    raw_path = (req.file_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="file_path is required")

    p = Path(raw_path).resolve()
    if not p.exists():
        p_alt = (Path(os.getcwd()) / raw_path).resolve()
        if p_alt.exists():
            p = p_alt

    import platform
    import subprocess
    sys_name = platform.system()
    try:
        if sys_name == "Windows":
            win_path = str(p).replace("/", "\\")
            if p.exists() and p.is_file():
                # Correct Windows Explorer command line to highlight specific file
                subprocess.Popen(f'explorer.exe /select,"{win_path}"')
            elif p.exists() and p.is_dir():
                subprocess.Popen(f'explorer.exe "{win_path}"')
            elif p.parent.exists():
                parent_path = str(p.parent).replace("/", "\\")
                subprocess.Popen(f'explorer.exe "{parent_path}"')
            else:
                raise HTTPException(status_code=404, detail=f"File path not found on disk: {raw_path}")
        elif sys_name == "Darwin":
            if p.exists():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p.parent)])
        else:
            folder_to_open = p.parent if p.exists() or p.parent.exists() else Path(os.getcwd())
            subprocess.Popen(["xdg-open", str(folder_to_open)])
        return {"status": "success", "file_path": str(p)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat-sessions-clear")
@app.delete("/api/chat-sessions-clear")
@app.delete("/api/chat-sessions/clear")
async def clear_all_chat_sessions(repo_id: Optional[str] = None):
    p = get_pipeline()
    try:
        p.state_db.clear_chat_sessions(repo_id=repo_id)
        return {"status": "success", "cleared_repo": repo_id or "all"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/chat-sessions/{chat_id}")
async def delete_chat_session(chat_id: str):
    p = get_pipeline()
    try:
        p.state_db.delete_chat_session(chat_id)
        return {"status": "success", "deleted_chat_id": chat_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/cache/{repo_id}")
async def clear_repo_cache(repo_id: str):
    p = get_pipeline()
    try:
        p.state_db.clear_semantic_cache(repo_id=repo_id)
        return {"status": "success", "message": f"Semantic judgment cache cleared for repository '{repo_id}'"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/chat-history/{chat_id}")
async def get_chat_history(chat_id: str):
    p = get_pipeline()
    session = p.state_db.get_chat_session(chat_id)
    history = p.state_db.get_chat_history(chat_id)
    return {
        "status": "success",
        "chat_id": chat_id,
        "repo_id": session.get("repo_id") if session else "default",
        "messages": history,
    }


@app.post("/api/chat/stream")
async def handle_chat_stream(req: ChatRequest):
    p = get_pipeline()
    idx_status = p.state_db.get_repo_indexing_status(req.repo_id)
    if idx_status.get("index_status") == "INDEXING":
        file_info = f" ({idx_status.get('current_indexing_file')})" if idx_status.get('current_indexing_file') else ""
        raise HTTPException(
            status_code=423,
            detail=f"Repository '{req.repo_id}' is currently indexing{file_info}. Please wait until indexing completes."
        )

    def event_generator():
        for event in p.chat_engine.chat_stream(
            user_message=req.message,
            repo_id=req.repo_id,
            chat_id=req.chat_id,
            bypass_cache=req.bypass_cache,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/chat")
async def handle_chat(req: ChatRequest):
    p = get_pipeline()
    idx_status = p.state_db.get_repo_indexing_status(req.repo_id)
    if idx_status.get("index_status") == "INDEXING":
        file_info = f" ({idx_status.get('current_indexing_file')})" if idx_status.get('current_indexing_file') else ""
        raise HTTPException(
            status_code=423,
            detail=f"Repository '{req.repo_id}' is currently indexing{file_info}. Please wait until indexing completes."
        )
    try:
        res = p.chat(
            message=req.message,
            repo_id=req.repo_id,
            chat_id=req.chat_id,
            bypass_cache=req.bypass_cache,
        )
        return {
            "status": "success",
            "chat_id": res["chat_id"],
            "repo_id": res["repo_id"],
            "reply": res["reply"],
            "citations": res["citations"],
            "cached": res.get("cached", False),
            "agent_trace": res.get("agent_trace"),
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scenario/{scenario_id}")
async def get_scenario_details(scenario_id: str):
    p = get_pipeline()
    sc = p.state_db.get_scenario(scenario_id)
    if not sc:
        sc_obj = p.state_db.get_scenario_by_id(scenario_id)
        if sc_obj:
            sc = {
                "scenario_id": sc_obj.scenario_id,
                "feature_name": sc_obj.feature_name or sc_obj.feature_title,
                "scenario_name": sc_obj.scenario_name,
                "file_path": sc_obj.file_path,
                "line_number": sc_obj.line_number,
                "tags": sc_obj.tags,
                "raw_gherkin": sc_obj.raw_gherkin,
                "canonical_text": sc_obj.canonical_text,
                "scenario_type": sc_obj.scenario_type,
            }
    if not sc:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")
    return {"status": "success", "scenario": sc}


# ==================== Clean Two-Section Web Dashboard ====================

HTML_DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Local RAG BDD Test Automation Agent</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Fira+Code:wght@400;500;600&display=swap" rel="stylesheet" />
  <!-- Lucide Icons -->
  <script src="https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js" crossorigin="anonymous"></script>
  <style>
    :root, [data-theme="dark"] {
      --bg: #121212;
      --sidebar-bg: #181818;
      --card-bg: #1e1e1e;
      --card-hover: #262626;
      --card-border: #2e2e2e;
      --card-border-subtle: #242424;
      --feed-bg: #141414;
      --feed-entry-bg: #1c1c1c;
      --accent: #ffffff;
      --accent-glow: rgba(255, 255, 255, 0.08);
      --purple: #d4d4d8;
      --purple-glow: rgba(255, 255, 255, 0.06);
      --text: #ececec;
      --text-muted: #8e8e8e;
      --text-subtle: #5e5e5e;
      --input-bg: #1e1e1e;
      --input-border: #333333;
      --btn-primary-bg: #ececec;
      --btn-primary-text: #121212;
      --btn-primary-hover: #ffffff;
      --btn-secondary-bg: #262626;
      --btn-secondary-text: #e2e8f0;
      --btn-secondary-border: #383838;
      --btn-secondary-hover: #333333;
      --user-bubble-bg: #2b2b2b;
      --user-bubble-border: #383838;
      --user-bubble-text: #ffffff;
      --assistant-bubble-bg: #1e1e1e;
      --assistant-bubble-border: #2e2e2e;
      --code-bg: #141414;
      --code-border: #282828;
      --code-text: #e4e4e7;
      --modal-bg: #1e1e1e;
      --modal-border: #333333;
      --toast-bg: #212121;
      --toast-border: #383838;
      --scrollbar-thumb: #2e2e2e;
      --scrollbar-track: #121212;
      --green: #10b981;
      --yellow: #f59e0b;
      --red: #ef4444;
    }

    [data-theme="light"] {
      --bg: #f9f9fb;
      --sidebar-bg: #f3f3f6;
      --card-bg: #ffffff;
      --card-hover: #f7f7fa;
      --card-border: #e2e2e8;
      --card-border-subtle: #eaeaea;
      --feed-bg: #f4f4f7;
      --feed-entry-bg: #ffffff;
      --accent: #111111;
      --accent-glow: rgba(0, 0, 0, 0.06);
      --purple: #4b5563;
      --purple-glow: rgba(0, 0, 0, 0.04);
      --text: #18181b;
      --text-muted: #71717a;
      --text-subtle: #a1a1aa;
      --input-bg: #ffffff;
      --input-border: #d4d4d8;
      --btn-primary-bg: #18181b;
      --btn-primary-text: #ffffff;
      --btn-primary-hover: #000000;
      --btn-secondary-bg: #f4f4f5;
      --btn-secondary-text: #18181b;
      --btn-secondary-border: #d4d4d8;
      --btn-secondary-hover: #e4e4e7;
      --user-bubble-bg: #f4f4f5;
      --user-bubble-border: #e4e4e7;
      --user-bubble-text: #18181b;
      --assistant-bubble-bg: #ffffff;
      --assistant-bubble-border: #e2e2e8;
      --code-bg: #f4f4f5;
      --code-border: #e4e4e7;
      --code-text: #18181b;
      --modal-bg: #ffffff;
      --modal-border: #d4d4d8;
      --toast-bg: #ffffff;
      --toast-border: #e2e2e8;
      --scrollbar-thumb: #d4d4d8;
      --scrollbar-track: #f9f9fb;
      --green: #059669;
      --yellow: #d97706;
      --red: #dc2626;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    /* Lucide Icon Base Styling */
    .lucide {
      vertical-align: middle;
      display: inline-block;
      stroke-width: 1.85;
    }
    .spin-icon {
      animation: spinAnim 1.2s linear infinite;
    }
    @keyframes spinAnim {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    
    /* Modern Minimalist Monochrome Custom Scrollbars */
    * {
      scrollbar-width: thin;
      scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
    }
    ::-webkit-scrollbar {
      width: 6px;
      height: 6px;
    }
    ::-webkit-scrollbar-track {
      background: var(--scrollbar-track);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--scrollbar-thumb);
      border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--text-muted);
    }

    html, body {
      height: 100%;
      max-height: 100vh;
      overflow: hidden;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      transition: background-color 0.2s ease, color 0.2s ease;
    }
    
    /* Top Header */
    header {
      background: var(--sidebar-bg);
      border-bottom: 1px solid var(--card-border);
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-icon {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent);
    }
    .brand-title { font-weight: 700; font-size: 16px; letter-spacing: -0.2px; color: var(--text); }
    .header-actions { display: flex; align-items: center; gap: 10px; }
    .watchdog-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 9999px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      font-size: 11.5px;
      font-weight: 500;
    }
    [data-theme="light"] .watchdog-pill {
      background: rgba(0, 0, 0, 0.03);
    }
    .watchdog-pulse {
      width: 7px; height: 7px; border-radius: 50%; background: var(--green);
      box-shadow: 0 0 6px var(--green);
      animation: pulseAnim 2s infinite;
    }
    @keyframes pulseAnim { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

    .repo-select-box { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-muted); }
    select, input, textarea {
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      color: var(--text);
      padding: 7px 12px;
      border-radius: 7px;
      font-family: inherit;
      font-size: 13px;
      outline: none;
      transition: all 0.2s;
    }
    select:focus, input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }

    .theme-toggle-btn {
      background: var(--btn-secondary-bg);
      border: 1px solid var(--btn-secondary-border);
      color: var(--text);
      width: 32px;
      height: 32px;
      border-radius: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .theme-toggle-btn:hover {
      background: var(--btn-secondary-hover);
      border-color: var(--card-border);
      transform: translateY(-1px);
    }

    .btn {
      background: var(--btn-primary-bg);
      color: var(--btn-primary-text);
      border: none;
      padding: 7px 14px;
      border-radius: 7px;
      font-weight: 600;
      font-size: 12.5px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn:hover { background: var(--btn-primary-hover); transform: translateY(-1px); }
    .btn-secondary { background: var(--btn-secondary-bg); color: var(--btn-secondary-text); border: 1px solid var(--btn-secondary-border); }
    .btn-secondary:hover { background: var(--btn-secondary-hover); border-color: var(--card-border); }
    .btn-danger { background: rgba(239, 68, 68, 0.08); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2); }
    .btn-danger:hover { background: rgba(239, 68, 68, 0.18); border-color: rgba(239, 68, 68, 0.35); }

    /* Navigation Tabs */
    .nav-tabs {
      display: flex;
      background: var(--sidebar-bg);
      border-bottom: 1px solid var(--card-border);
      padding: 0 24px;
      flex-shrink: 0;
    }
    .tab-btn {
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--text-muted);
      padding: 12px 18px;
      font-size: 13.5px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .tab-btn:hover { color: #fff; }
    .tab-btn.active { color: #fff; border-bottom-color: #fff; }

    /* Main Area */
    main {
      flex: 1;
      min-height: 0;
      overflow: hidden;
      display: flex;
      position: relative;
    }
    .tab-content {
      display: none;
      height: 100%;
      width: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .tab-content.active { display: flex; }

    /* ================= TAB 1: INDEXER ================= */
    #indexerTab {
      overflow-y: auto;
      height: 100%;
      width: 100%;
      min-height: 0;
    }
    .indexer-container {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 20px;
      padding: 20px;
      width: 100%;
      min-height: 100%;
      box-sizing: border-box;
      align-items: start;
    }
    .panel-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 12px;
    }
    .panel-header h3 { font-size: 14.5px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 8px; }
    
    .repo-card {
      background: var(--sidebar-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: all 0.2s;
    }
    .repo-card:hover { border-color: var(--card-border); background: var(--card-hover); }
    .repo-card.selected { border-color: var(--accent); background: var(--card-hover); }
    .repo-info h4 { font-size: 13.5px; font-weight: 600; color: var(--text); }
    .repo-info .meta { font-size: 11.5px; color: var(--text-muted); margin-top: 4px; font-family: 'Fira Code', monospace; }

    .folder-item {
      background: var(--sidebar-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 8px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .folder-path { font-family: 'Fira Code', monospace; font-size: 12px; color: var(--text); word-break: break-all; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 5px;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-live { background: rgba(16, 185, 129, 0.1); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.25); }

    /* Live Watchdog Activity Feed */
    .activity-feed {
      background: var(--feed-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 14px;
      flex: 1;
      min-height: 250px;
      max-height: 520px;
      overflow-y: auto;
      font-family: 'Fira Code', monospace;
      font-size: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .event-entry {
      padding: 6px 10px;
      border-radius: 6px;
      background: var(--feed-entry-bg);
      border: 1px solid var(--card-border-subtle);
      border-left: 3px solid var(--text-muted);
      color: var(--text);
      line-height: 1.5;
    }
    .event-entry.modified { border-left-color: var(--yellow); }
    .event-entry.created { border-left-color: var(--green); }
    .event-entry.deleted { border-left-color: var(--red); }

    /* ================= TAB 2: RAG BOT ================= */
    .chat-layout {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 340px;
      width: 100%;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .sessions-col {
      background: var(--sidebar-bg);
      border-right: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .sessions-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
    .session-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding: 10px;
    }
    .session-item {
      padding: 10px 12px;
      border-radius: 7px;
      cursor: pointer;
      margin-bottom: 4px;
      background: transparent;
      border: 1px solid transparent;
      transition: all 0.15s ease;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .session-item:hover { background: var(--card-hover); }
    .session-item.active { background: var(--card-hover); border-color: var(--card-border); }
    .session-title { font-size: 13px; font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .session-time { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

    .chat-window {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      min-width: 0;
      overflow: hidden;
      background: var(--bg);
    }
    .chat-messages {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .chat-bubble {
      max-width: 82%;
      padding: 14px 18px;
      border-radius: 12px;
      font-size: 13.5px;
      line-height: 1.6;
      word-wrap: break-word;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: var(--user-bubble-bg);
      color: var(--user-bubble-text);
      border: 1px solid var(--user-bubble-border);
      border-bottom-right-radius: 2px;
    }
    .chat-bubble.assistant {
      align-self: flex-start;
      background: var(--assistant-bubble-bg);
      border: 1px solid var(--assistant-bubble-border);
      color: var(--text);
      border-bottom-left-radius: 2px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
    }
    .chat-bubble code {
      background: var(--code-bg);
      padding: 2px 6px;
      border-radius: 4px;
      font-family: 'Fira Code', monospace;
      font-size: 12px;
      color: var(--code-text);
      border: 1px solid var(--code-border);
    }

    .chat-input-container {
      padding: 16px 24px;
      background: var(--sidebar-bg);
      border-top: 1px solid var(--card-border);
      display: flex;
      gap: 10px;
      flex-shrink: 0;
    }
    .chat-input-container input { flex: 1; padding: 11px 16px; font-size: 13.5px; border-radius: 8px; background: var(--input-bg); border: 1px solid var(--input-border); color: var(--text); }

    .citations-col {
      background: var(--sidebar-bg);
      border-left: 1px solid var(--card-border);
      padding: 16px;
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
    .citation-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 8px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .citation-card:hover { border-color: var(--accent); background: var(--card-hover); }
    .citation-card .title { font-weight: 600; font-size: 12.5px; color: var(--text); }
    .citation-card .meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; font-family: 'Fira Code', monospace; }

    .citation-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 9px;
      border-radius: 6px;
      font-size: 12px;
      background: var(--card-hover);
      color: var(--text);
      cursor: pointer;
      border: 1px solid var(--card-border);
      transition: all 0.15s ease;
    }
    .citation-pill:hover { background: var(--btn-secondary-hover); border-color: var(--accent); }

    .btn-icon-folder {
      background: var(--card-hover);
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      border-radius: 6px;
      padding: 4px 8px;
      font-size: 11px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      transition: all 0.15s ease;
      flex-shrink: 0;
    }
    .btn-icon-folder:hover {
      background: var(--btn-secondary-hover);
      border-color: var(--accent);
      color: var(--text);
      transform: translateY(-1px);
    }

    /* Evaluation Summary Card in Chat */
    .eval-summary-card {
      background: var(--sidebar-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 16px;
      margin-top: 4px;
    }
    .eval-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--card-border-subtle);
    }
    .eval-title {
      font-weight: 700;
      font-size: 13.5px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .eval-summary-text {
      font-size: 13px;
      line-height: 1.6;
      color: var(--text);
      margin-bottom: 12px;
    }
    .btn-report {
      background: var(--btn-secondary-bg);
      color: var(--btn-secondary-text);
      border: 1px solid var(--btn-secondary-border);
      padding: 7px 14px;
      border-radius: 7px;
      font-size: 11.5px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn-report:hover {
      background: var(--btn-secondary-hover);
      border-color: var(--card-border);
      transform: translateY(-1px);
    }
    
    /* Code Block with Copy Button */
    .code-block-wrapper {
      position: relative;
      margin: 12px 0;
    }
    .code-copy-btn {
      position: absolute;
      top: 8px;
      right: 8px;
      background: var(--btn-secondary-bg);
      border: 1px solid var(--btn-secondary-border);
      color: var(--text-muted);
      border-radius: 5px;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
      z-index: 2;
    }
    .code-copy-btn:hover {
      color: var(--text);
      background: var(--btn-secondary-hover);
      border-color: var(--card-border);
    }

    /* ================= ANTHROPIC-STYLE AGENT THOUGHT & RETRY LOOP ================= */
    
    @keyframes anthropicRotate {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    @keyframes anthropicPulse {
      0%, 100% { transform: scale(0.85); opacity: 0.7; }
      50% { transform: scale(1.2); opacity: 1; filter: drop-shadow(0 0 4px var(--accent)); }
    }
    @keyframes anthropicShimmer {
      0% { background-position: -200% 0; }
      100% { background-position: 200% 0; }
    }
    @keyframes stepFadeIn {
      0% { opacity: 0; transform: translateY(4px); }
      100% { opacity: 1; transform: translateY(0); }
    }

    .shimmer-text {
      background: linear-gradient(90deg, var(--text-muted) 0%, var(--accent) 50%, var(--text-muted) 100%);
      background-size: 200% 100%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      animation: anthropicShimmer 2.4s infinite linear;
    }

    /* Live Thinking Container (In-Flight) */
    .live-thought-container {
      background: var(--sidebar-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 12px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
      position: relative;
      overflow: hidden;
    }
    .live-thought-container::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0; height: 2px;
      background: linear-gradient(90deg, transparent, var(--accent), var(--text-muted), transparent);
      animation: anthropicShimmer 2s infinite linear;
      background-size: 200% 100%;
    }
    .live-thought-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 10px;
    }
    .live-thought-title-group {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .anthropic-spinner-ring {
      position: relative;
      width: 20px;
      height: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    .anthropic-spinner-ring::before {
      content: '';
      position: absolute;
      inset: 0;
      border-radius: 50%;
      border: 2px solid var(--card-border);
      border-top-color: var(--accent);
      border-right-color: var(--text-muted);
      animation: anthropicRotate 0.9s linear infinite;
    }
    .anthropic-spinner-center {
      width: 5px;
      height: 5px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 6px var(--accent);
      animation: anthropicPulse 1.4s ease-in-out infinite;
    }
    .live-agent-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 8px;
      border-radius: 9999px;
      background: var(--card-hover);
      border: 1px solid var(--card-border);
      color: var(--text);
      font-size: 11px;
      font-weight: 600;
    }
    .live-step-list {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }
    .live-step-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 6px 10px;
      border-radius: 6px;
      background: var(--card-bg);
      border: 1px solid var(--card-border-subtle);
      animation: stepFadeIn 0.3s ease-out;
      transition: all 0.15s ease;
    }
    .live-step-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-size: 12px;
      color: var(--text-muted);
    }
    .live-step-left {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .live-step-item.active {
      background: var(--card-hover);
      border-color: var(--accent);
    }
    .live-step-item.active .live-step-header {
      color: var(--text);
      font-weight: 600;
    }
    .live-step-item.completed {
      background: var(--card-bg);
      border-color: var(--card-border);
    }
    .live-step-item.completed .live-step-header {
      color: var(--text);
    }
    .live-step-detail {
      font-size: 11px;
      color: var(--text-muted);
      padding-left: 24px;
      font-family: 'Fira Code', monospace;
    }
    .live-step-item.active .live-step-detail {
      color: var(--text);
    }
    .live-step-dur {
      font-size: 10.5px;
      font-family: 'Fira Code', monospace;
      color: var(--text-muted);
    }
    .live-step-item.completed .live-step-dur {
      color: var(--green);
    }
    .step-indicator-icon {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 9px;
      flex-shrink: 0;
      background: var(--card-hover);
      color: var(--text-muted);
    }
    .live-step-item.active .step-indicator-icon {
      background: var(--btn-primary-bg);
      color: var(--btn-primary-text);
    }
    .live-step-item.completed .step-indicator-icon {
      background: rgba(16, 185, 129, 0.18);
      color: var(--green);
    }

    /* Finished Thought Accordion (Anthropic Style) */
    .anthropic-thought-card {
      margin-bottom: 12px;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      background: var(--sidebar-bg);
      overflow: hidden;
      transition: all 0.15s ease;
    }
    .anthropic-thought-card:hover {
      border-color: var(--accent);
    }
    .thought-toggle-btn {
      width: 100%;
      background: none;
      border: none;
      padding: 8px 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      text-align: left;
      user-select: none;
      transition: background 0.15s;
    }
    .thought-toggle-btn:hover {
      background: var(--card-hover);
      color: var(--text);
    }
    .thought-header-left {
      display: flex;
      align-items: center;
      gap: 7px;
    }
    .thought-sparkle {
      color: var(--text-muted);
      font-size: 13px;
    }
    .thought-chevron {
      font-size: 11px;
      color: var(--text-muted);
      transition: transform 0.2s ease;
    }
    .anthropic-thought-card.expanded .thought-chevron {
      transform: rotate(180deg);
      color: var(--text);
    }
    .thought-drawer {
      display: none;
      padding: 12px 14px 14px 14px;
      border-top: 1px solid var(--card-border-subtle);
      background: var(--feed-bg);
      animation: stepFadeIn 0.15s ease-out;
    }
    .anthropic-thought-card.expanded .thought-drawer {
      display: block;
    }

    /* Vertical Timeline inside Thought Drawer */
    .thought-timeline {
      position: relative;
      padding-left: 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 4px;
    }
    .thought-timeline::before {
      content: '';
      position: absolute;
      left: 6px;
      top: 5px;
      bottom: 5px;
      width: 2px;
      background: var(--card-border);
    }
    .timeline-node {
      position: relative;
    }
    .timeline-node::before {
      content: '';
      position: absolute;
      left: -15px;
      top: 5px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--card-border);
      border: 2px solid var(--feed-bg);
    }
    .timeline-node.completed::before {
      background: var(--green);
    }
    .timeline-node.retried::before {
      background: var(--yellow);
    }
    .timeline-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 11.5px;
      font-weight: 600;
      color: var(--text);
    }
    .timeline-detail {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 2px;
      line-height: 1.4;
    }
    .timeline-dur {
      font-size: 10px;
      color: var(--text-muted);
      font-family: 'Fira Code', monospace;
    }

    /* Modals & Viewer */
    .modal {
      display: none;
      position: fixed;
      z-index: 9999;
      left: 0; top: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(6px);
      align-items: center; justify-content: center;
    }
    .modal.show { display: flex; }
    .modal-content {
      background: var(--modal-bg);
      border: 1px solid var(--modal-border);
      border-radius: 12px;
      width: 90%; max-width: 800px;
      max-height: 85vh;
      overflow-y: auto;
      padding: 22px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
    }
    .modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 12px; }
    .modal-close { background: none; border: none; font-size: 22px; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; }
    .modal-close:hover { color: var(--text); }
    .gherkin-viewer {
      margin-top: 14px;
      background: var(--code-bg);
      border: 1px solid var(--code-border);
      border-radius: 8px;
      padding: 14px;
      font-family: 'Fira Code', monospace;
      font-size: 12.5px;
      color: var(--code-text);
      white-space: pre-wrap;
      line-height: 1.6;
    }

    /* Custom Toast Notifications */
    .toast-container {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99999;
      display: flex;
      flex-direction: column;
      gap: 8px;
      pointer-events: none;
    }
    .toast {
      pointer-events: auto;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      background: var(--toast-bg);
      border: 1px solid var(--toast-border);
      border-radius: 8px;
      color: var(--text);
      font-size: 13px;
      font-weight: 500;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
      min-width: 250px;
      max-width: 380px;
      animation: toastSlideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      transition: all 0.2s ease;
    }
    .toast.toast-success { border-left: 3px solid var(--green); }
    .toast.toast-error { border-left: 3px solid var(--red); }
    .toast.toast-warning { border-left: 3px solid var(--yellow); }
    .toast.toast-info { border-left: 3px solid var(--text-muted); }
    @keyframes toastSlideIn {
      from { opacity: 0; transform: translateY(12px) scale(0.96); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    @keyframes toastFadeOut {
      from { opacity: 1; transform: translateY(0); }
      to { opacity: 0; transform: translateY(8px); }
    }
  </style>
</head>
<body>

  <!-- Toast Container -->
  <div id="toastContainer" class="toast-container"></div>

  <!-- Custom Confirmation Dialog Modal -->
  <div id="customConfirmModal" class="modal" onclick="if(event.target===this) closeConfirmDialog(false)">
    <div class="modal-content" style="max-width: 420px; padding: 20px; border-radius: 12px; background: #1e1e1e; border: 1px solid #333;">
      <div style="display: flex; align-items: flex-start; gap: 12px;">
        <div id="confirmIconBox" style="width: 36px; height: 36px; border-radius: 8px; background: rgba(239, 68, 68, 0.1); color: #f87171; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
          <i id="confirmIcon" data-lucide="alert-triangle" style="width: 18px; height: 18px;"></i>
        </div>
        <div style="flex: 1;">
          <h3 id="confirmTitle" style="font-size: 14.5px; font-weight: 600; color: #fff; margin-bottom: 5px;">Confirm Action</h3>
          <p id="confirmMessage" style="font-size: 12.5px; color: #a1a1aa; line-height: 1.5;">Are you sure you want to proceed?</p>
        </div>
      </div>
      <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px;">
        <button id="confirmCancelBtn" class="btn btn-secondary" style="padding: 6px 13px; font-size: 12px;" onclick="closeConfirmDialog(false)">Cancel</button>
        <button id="confirmActionBtn" class="btn" style="padding: 6px 15px; font-size: 12px;" onclick="executeConfirmDialog()">Confirm</button>
      </div>
    </div>
  </div>

  <!-- Top Header -->
  <header>
    <div class="brand">
      <div class="brand-icon">
        <i data-lucide="shield-check" style="width: 18px; height: 18px;"></i>
      </div>
      <span class="brand-title">Local RAG BDD Automation Agent</span>
    </div>
    <div class="header-actions">
      <div class="watchdog-pill" id="repoStatusPill" title="Repository Index Freshness Status">
        <span class="watchdog-pulse" id="repoStatusPulse"></span>
        <span id="repoStatusText">Index: Checking...</span>
      </div>
      <div class="watchdog-pill" id="watchdogHeaderPill" title="Integrated Watchdog Engine">
        <span class="watchdog-pulse"></span>
        <span id="watchdogStatusText">Watchdog: Monitoring</span>
      </div>
      <div class="repo-select-box">
        <label style="display: flex; align-items: center; gap: 4px;"><i data-lucide="database" style="width: 14px; height: 14px;"></i> Active Repo:</label>
        <select id="globalRepoSelector" onchange="onGlobalRepoChange()"></select>
      </div>
      <button id="themeToggleBtn" class="theme-toggle-btn" title="Toggle Light / Dark Theme" onclick="toggleTheme()">
        <i id="themeToggleIcon" data-lucide="sun" style="width: 15px; height: 15px;"></i>
      </button>
    </div>
  </header>

  <!-- 2 Main Navigation Tabs -->
  <nav class="nav-tabs">
    <button class="tab-btn active" onclick="switchMainTab('indexerTab', this)">
      <i data-lucide="folder-tree" style="width: 15px; height: 15px;"></i> Indexer
    </button>
    <button class="tab-btn" onclick="switchMainTab('ragBotTab', this)">
      <i data-lucide="bot" style="width: 15px; height: 15px;"></i> RAG Bot
    </button>
  </nav>

  <!-- Main Content Area -->
  <main>

    <!-- ================= SECTION 1: INDEXER ================= -->
    <div id="indexerTab" class="tab-content active">
      <div class="indexer-container">
        
        <!-- Left Column: Repositories & Multi-Folder Management -->
        <div style="display: flex; flex-direction: column; gap: 20px;">
          
          <!-- Sub-Section A: Repositories Management -->
          <div class="panel-card">
            <div class="panel-header">
              <h3><i data-lucide="git-branch" style="width: 17px; height: 17px; color: var(--accent);"></i> Automation Repositories</h3>
              <button class="btn btn-secondary" onclick="toggleAddRepoForm()"><i data-lucide="plus" style="width: 13px; height: 13px;"></i> New Repo</button>
            </div>

            <!-- Add Repo Form -->
            <div id="addRepoForm" style="display: none; background: var(--sidebar-bg); padding: 14px; border-radius: 8px; border: 1px solid var(--card-border);">
              <h4 style="font-size: 13px; margin-bottom: 10px; color: var(--text); display: flex; align-items: center; gap: 6px;">
                <i data-lucide="plus-circle" style="width: 14px; height: 14px; color: var(--accent);"></i> Register New Automation Repository
              </h4>
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px;">
                <input type="text" id="newRepoName" placeholder="Repository Name (e.g. E-Commerce Core)" />
                <input type="text" id="newRepoId" placeholder="Unique Repo ID (e.g. ecommerce_core)" />
              </div>
              <div style="display: flex; justify-content: flex-end; gap: 8px;">
                <button class="btn btn-secondary" onclick="toggleAddRepoForm()"><i data-lucide="x" style="width: 13px; height: 13px;"></i> Cancel</button>
                <button class="btn" onclick="submitNewRepo()"><i data-lucide="check" style="width: 13px; height: 13px;"></i> Save Repository</button>
              </div>
            </div>

            <div id="reposList"></div>
          </div>

          <!-- Sub-Section B: Indexing Folders (Multi-Folder per Repo) -->
          <div class="panel-card">
            <div class="panel-header">
              <h3><i data-lucide="folders" style="width: 17px; height: 17px; color: var(--accent);"></i> Indexing Folders (<span id="selectedRepoTitle">Selected Repo</span>)</h3>
              <button class="btn btn-secondary" onclick="reindexCurrentRepo()"><i data-lucide="refresh-cw" style="width: 13px; height: 13px;"></i> Re-index All Folders</button>
            </div>
            
            <p style="font-size: 12.5px; color: var(--text-muted);">
              Add multiple feature directories for this repository. The in-process watchdog will monitor all registered folders in real-time.
            </p>

            <div style="display: flex; gap: 8px;">
              <input type="text" id="newFolderPath" style="flex: 1;" placeholder="Enter absolute or relative directory path (e.g. sample_data/feature_repos)" />
              <button class="btn" onclick="submitAddFolder()"><i data-lucide="folder-plus" style="width: 14px; height: 14px;"></i> Add Folder & Watch</button>
            </div>

            <div id="foldersList" style="margin-top: 4px;"></div>
          </div>

        </div>

        <!-- Right Column: Live Watchdog Activity & Telemetry -->
        <div class="panel-card">
          <div class="panel-header">
            <h3><i data-lucide="activity" style="width: 17px; height: 17px; color: var(--accent);"></i> Live Watchdog & Re-indexing Activity</h3>
            <span class="badge badge-live" id="watchdogLiveCount"><i data-lucide="radio" style="width: 11px; height: 11px;"></i> Watching 0 Folders</span>
          </div>

          <p style="font-size: 12.5px; color: var(--text-muted);">
            Changes to test & specification files (<code>.feature</code>, <code>.md</code>, <code>.txt</code>, <code>.json</code>, <code>.yaml</code>, etc.) in any registered folder are automatically detected and incrementally re-indexed.
          </p>

          <div class="activity-feed" id="activityFeed">
            <div class="event-entry">
              <i data-lucide="check-circle" style="width: 14px; height: 14px; color: var(--green); flex-shrink: 0;"></i>
              <span>In-process watchdog engine running. Real-time file change logs will appear here.</span>
            </div>
          </div>
        </div>

      </div>
    </div>

    <!-- ================= SECTION 2: RAG BOT ================= -->
    <div id="ragBotTab" class="tab-content">
      <div class="chat-layout">
        
        <!-- Left: Chat Sessions Sidebar -->
        <div class="sessions-col">
          <div class="sessions-header">
            <h3 style="font-size: 14px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 6px;">
              <i data-lucide="messages-square" style="width: 15px; height: 15px; color: var(--accent);"></i> Chat Sessions
            </h3>
            <div style="display: flex; gap: 4px;">
              <button class="btn btn-danger" style="padding: 3px 7px; font-size: 11px;" onclick="clearAllChatSessions()"><i data-lucide="trash" style="width: 11px; height: 11px;"></i> Clear</button>
              <button class="btn" style="padding: 3px 8px; font-size: 11px;" onclick="createNewChatSession()"><i data-lucide="plus" style="width: 11px; height: 11px;"></i> New</button>
            </div>
          </div>
          <div class="session-list" id="chatSessionsList"></div>
        </div>

        <!-- Center: Chat Window -->
        <div class="chat-window">
          <!-- Live Indexing Banner -->
          <div id="indexingBanner" style="display: none; background: rgba(234, 179, 8, 0.12); border-bottom: 1px solid rgba(234, 179, 8, 0.3); padding: 10px 20px; font-size: 13px; color: #fde047; align-items: center; justify-content: space-between; flex-shrink: 0;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <i data-lucide="loader-2" class="spin-icon" style="width: 15px; height: 15px; color: #fde047;"></i>
              <span id="indexingBannerText">Indexing in progress... Analysis will be available as soon as indexing completes.</span>
            </div>
            <div style="width: 140px; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden;">
              <div id="indexingProgressBar" style="width: 50%; height: 100%; background: #fde047; transition: width 0.3s;"></div>
            </div>
          </div>

          <div class="chat-messages" id="chatMessages">
            <div class="chat-bubble assistant">
              <div style="display: flex; align-items: flex-start; gap: 10px;">
                <i data-lucide="sparkles" style="width: 18px; height: 18px; color: var(--accent); flex-shrink: 0; margin-top: 2px;"></i>
                <div>
                  Welcome to <strong>RAG Bot</strong>! Ask any question regarding Gherkin scenario coverage, acceptance criteria, or test steps scoped to your selected repository.
                </div>
              </div>
            </div>
          </div>
          <div class="chat-input-container">
            <input type="text" id="chatInput" placeholder="Ask about requirement coverage, login scenarios, checkout tests..." onkeydown="if(event.key==='Enter') sendChatMessage()" />
            <button class="btn" id="chatSendBtn" onclick="sendChatMessage()"><i data-lucide="send" style="width: 14px; height: 14px;"></i> Send</button>
          </div>
        </div>

        <!-- Right: Scenario Citations Sidebar -->
        <div class="citations-col">
          <h3 style="font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 6px; display: flex; align-items: center; gap: 6px;">
            <i data-lucide="book-open" style="width: 15px; height: 15px; color: var(--accent);"></i> Scenario Citations
          </h3>
          <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 12px;">Retrieved grounded Gherkin scenarios for the latest query.</p>
          <div id="citationsList"></div>
        </div>

      </div>
    </div>

  </main>

  <!-- Scenario Details Modal -->
  <div id="scenarioModal" class="modal" onclick="if(event.target===this) closeScenarioModal()">
    <div class="modal-content">
      <div class="modal-header">
        <h3 id="modalTitle" style="font-size: 16px; color: #fff; display: flex; align-items: center; gap: 8px;">
          <i data-lucide="file-code" style="width: 18px; height: 18px; color: var(--accent);"></i> Scenario Details
        </h3>
        <button class="modal-close" aria-label="Close scenario details" onclick="closeScenarioModal()"><i data-lucide="x" style="width: 18px; height: 18px;"></i></button>
      </div>
      <div class="modal-meta" id="modalMeta"></div>
      <div class="gherkin-viewer" id="modalGherkin"></div>
    </div>
  </div>

  <!-- Full Evaluation Details Modal -->
  <div id="reportModal" class="modal" onclick="if(event.target===this) closeReportModal()">
    <div class="modal-content" style="max-width: 860px; max-height: 88vh;">
      <div class="modal-header">
        <div style="display: flex; align-items: center; gap: 10px;">
          <i data-lucide="clipboard-check" style="width: 22px; height: 22px; color: var(--accent);"></i>
          <div>
            <h3 id="reportModalTitle" style="font-size: 16px; color: #fff;">Coverage Assessment & Gap Analysis</h3>
            <div id="reportModalSubtitle" style="font-size: 12px; color: var(--text-muted); margin-top: 2px;"></div>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11.5px;" onclick="copyReportModalContent()"><i data-lucide="copy" style="width: 12px; height: 12px;"></i> Copy Report</button>
          <button class="modal-close" aria-label="Close report" onclick="closeReportModal()"><i data-lucide="x" style="width: 18px; height: 18px;"></i></button>
        </div>
      </div>
      <div id="reportModalBody" style="margin-top: 16px; max-height: 70vh; overflow-y: auto; line-height: 1.7; font-size: 13.5px; color: #e2e8f0;"></div>
    </div>
  </div>

  <!-- JavaScript Application Logic -->
  <script>
    let activeRepoId = 'repo_1';
    let activeChatId = null;
    window.reportStore = {};
    window.activeReportId = null;

    function refreshIcons(container) {
      if (window.lucide && typeof window.lucide.createIcons === 'function') {
        try {
          window.lucide.createIcons(container ? { root: container } : undefined);
        } catch (e) {
          console.warn('Lucide icon refresh notice:', e);
        }
      }
    }

    // Cross-platform file basename helper
    function getFileName(filePath) {
      if (!filePath) return '';
      const normalized = String(filePath).replace(/\\/g, '/');
      return normalized.split('/').pop();
    }

    function escapeHtml(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function formatMarkdown(text) {
      if (!text) return '';
      let out = String(text);

      // Extract and replace code blocks
      const codeBlocks = [];
      out = out.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const idx = codeBlocks.length;
        codeBlocks.push({ lang, code: code.trim() });
        return `@@@CODEBLOCK_${idx}@@@`;
      });

      // Escape basic HTML
      out = escapeHtml(out);

      // Restore code blocks with syntax styled container and copy button
      out = out.replace(/@@@CODEBLOCK_(\d+)@@@/g, (match, idx) => {
        const item = codeBlocks[parseInt(idx)];
        return `
          <div class="code-block-wrapper">
            <button class="code-copy-btn" onclick="copyCode(this)"><i data-lucide="copy" style="width: 12px; height: 12px;"></i> Copy Gherkin</button>
            <div class="gherkin-viewer">${escapeHtml(item.code)}</div>
          </div>
        `;
      });

      // Headers
      out = out.replace(/^### (.*?)$/gm, '<h3 style="font-size: 15px; font-weight: 700; color: var(--accent); margin: 14px 0 8px 0;">$1</h3>');
      out = out.replace(/^## (.*?)$/gm, '<h2 style="font-size: 16px; font-weight: 800; color: var(--text); margin: 16px 0 10px 0;">$1</h2>');
      out = out.replace(/^# (.*?)$/gm, '<h1 style="font-size: 18px; font-weight: 800; color: var(--text); margin: 18px 0 12px 0;">$1</h1>');

      // Horizontal rules
      out = out.replace(/^---$/gm, '<hr style="border: none; border-top: 1px solid var(--card-border); margin: 14px 0;" />');

      // Bold & Italic & Inline code
      out = out.replace(/\*\*(.*?)\*\*/g, '<strong style="color: var(--text);">$1</strong>');
      out = out.replace(/\*(.*?)\*/g, '<em style="color: var(--text-muted);">$1</em>');
      out = out.replace(/`([^`]+)`/g, '<code>$1</code>');

      // Unordered lists
      out = out.replace(/^\* (.*?)$/gm, '<div style="margin-left: 14px; margin-bottom: 5px;">• $1</div>');
      out = out.replace(/^- (.*?)$/gm, '<div style="margin-left: 14px; margin-bottom: 5px;">• $1</div>');

      // Links
      out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color: var(--accent); text-decoration: underline;">$1</a>');

      // Line breaks
      out = out.replace(/\n/g, '<br/>');

      return out;
    }

    // ==================== THEME MANAGEMENT ====================

    function initTheme() {
      const saved = localStorage.getItem('rag_theme') || 'dark';
      setTheme(saved, false);
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      setTheme(next, true);
    }

    function setTheme(theme, notify = false) {
      document.documentElement.setAttribute('data-theme', theme);
      localStorage.setItem('rag_theme', theme);
      const icon = document.getElementById('themeToggleIcon');
      if (icon) {
        icon.setAttribute('data-lucide', theme === 'dark' ? 'sun' : 'moon');
        refreshIcons(document.getElementById('themeToggleBtn'));
      }
      if (notify) {
        showToast(`Switched to ${theme === 'dark' ? 'Dark' : 'Light'} theme`, 'info', 1800);
      }
    }

    function switchMainTab(tabId, btn) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      btn.classList.add('active');
      if (tabId === 'indexerTab') loadIndexerData();
      if (tabId === 'ragBotTab') loadChatSessions();
      refreshIcons();
    }

    // ==================== CUSTOM TOAST & CONFIRMATION DIALOG ====================

    function showToast(message, type = 'info', duration = 3200) {
      const container = document.getElementById('toastContainer');
      if (!container) return;

      const toast = document.createElement('div');
      toast.className = `toast toast-${type}`;

      let iconName = 'info';
      let iconColor = '#ececec';
      if (type === 'success') { iconName = 'check-circle-2'; iconColor = '#10b981'; }
      else if (type === 'error') { iconName = 'alert-circle'; iconColor = '#ef4444'; }
      else if (type === 'warning') { iconName = 'alert-triangle'; iconColor = '#f59e0b'; }

      toast.innerHTML = `
        <i data-lucide="${iconName}" style="width: 16px; height: 16px; color: ${iconColor}; flex-shrink: 0;"></i>
        <div style="flex: 1; line-height: 1.4;">${escapeHtml(message)}</div>
        <button style="background: none; border: none; color: #71717a; cursor: pointer; display: flex; align-items: center; padding: 2px;" onclick="this.parentElement.remove()">
          <i data-lucide="x" style="width: 12px; height: 12px;"></i>
        </button>
      `;

      container.appendChild(toast);
      refreshIcons(toast);

      setTimeout(() => {
        toast.style.animation = 'toastFadeOut 0.2s forwards';
        setTimeout(() => toast.remove(), 200);
      }, duration);
    }

    let activeConfirmCallback = null;

    function showConfirmDialog({ title, message, confirmText = 'Confirm', cancelText = 'Cancel', isDanger = false, onConfirm }) {
      document.getElementById('confirmTitle').textContent = title || 'Confirm Action';
      document.getElementById('confirmMessage').textContent = message || 'Are you sure you want to proceed?';
      
      const actionBtn = document.getElementById('confirmActionBtn');
      const cancelBtn = document.getElementById('confirmCancelBtn');
      const iconBox = document.getElementById('confirmIconBox');
      const icon = document.getElementById('confirmIcon');

      actionBtn.textContent = confirmText;
      cancelBtn.textContent = cancelText;

      if (isDanger) {
        actionBtn.className = 'btn btn-danger';
        actionBtn.style.background = 'rgba(239, 68, 68, 0.15)';
        actionBtn.style.color = '#f87171';
        actionBtn.style.border = '1px solid rgba(239, 68, 68, 0.3)';
        iconBox.style.background = 'rgba(239, 68, 68, 0.12)';
        iconBox.style.color = '#f87171';
        icon.setAttribute('data-lucide', 'alert-triangle');
      } else {
        actionBtn.className = 'btn';
        actionBtn.style.background = 'var(--btn-primary-bg)';
        actionBtn.style.color = 'var(--btn-primary-text)';
        actionBtn.style.border = 'none';
        iconBox.style.background = 'rgba(255, 255, 255, 0.08)';
        iconBox.style.color = '#fff';
        icon.setAttribute('data-lucide', 'help-circle');
      }

      activeConfirmCallback = onConfirm;
      document.getElementById('customConfirmModal').classList.add('show');
      refreshIcons(document.getElementById('customConfirmModal'));
    }

    function closeConfirmDialog(isConfirmed = false) {
      document.getElementById('customConfirmModal').classList.remove('show');
      if (!isConfirmed) activeConfirmCallback = null;
    }

    async function executeConfirmDialog() {
      const cb = activeConfirmCallback;
      closeConfirmDialog(true);
      if (typeof cb === 'function') {
        await cb();
      }
    }

    // ==================== REPOSITORIES & FOLDERS ====================

    async function loadRepos() {
      const res = await fetch('/api/repos');
      const data = await res.json();
      const repos = data.repositories || [];

      // Populate Global Header Selector
      const selector = document.getElementById('globalRepoSelector');
      selector.innerHTML = '';
      repos.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.repo_id;
        opt.textContent = `${r.repo_name} (${r.scenario_count || 0} scenarios)`;
        if (r.repo_id === activeRepoId) opt.selected = true;
        selector.appendChild(opt);
      });

      if (!repos.find(r => r.repo_id === activeRepoId) && repos.length > 0) {
        activeRepoId = repos[0].repo_id;
      }

      // Render Repos List in Indexer
      const list = document.getElementById('reposList');
      list.innerHTML = '';
      repos.forEach(r => {
        const isSelected = r.repo_id === activeRepoId;
        const card = document.createElement('div');
        card.className = 'repo-card' + (isSelected ? ' selected' : '');
        card.onclick = () => selectRepo(r.repo_id);
        card.innerHTML = `
          <div class="repo-info">
            <h4><i data-lucide="folder-git-2" style="width: 15px; height: 15px; color: var(--accent);"></i> ${escapeHtml(r.repo_name)} ${isSelected ? '<span class="badge badge-live" style="margin-left: 6px;"><i data-lucide="check" style="width: 11px; height: 11px;"></i> Active</span>' : ''}</h4>
            <div class="meta">ID: <code>${escapeHtml(r.repo_id)}</code> | Scenarios: <b>${r.scenario_count || 0}</b> | Corpus: <b>v${r.corpus_version || 1}</b></div>
          </div>
          <div style="display: flex; gap: 6px;" onclick="event.stopPropagation()">
            <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="reindexRepo('${r.repo_id}')"><i data-lucide="refresh-cw" style="width: 11px; height: 11px;"></i> Re-index</button>
            <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="deleteRepo('${r.repo_id}')"><i data-lucide="trash-2" style="width: 11px; height: 11px;"></i> Delete</button>
          </div>
        `;
        list.appendChild(card);
      });

      document.getElementById('selectedRepoTitle').textContent = activeRepoId;
      loadFoldersForActiveRepo();
      refreshIcons(list);
    }

    function selectRepo(repoId) {
      activeRepoId = repoId;
      document.getElementById('globalRepoSelector').value = repoId;
      loadRepos();
      loadChatSessions();
      pollRepoStatus();
    }

    function onGlobalRepoChange() {
      activeRepoId = document.getElementById('globalRepoSelector').value;
      loadRepos();
      loadChatSessions();
      createNewChatSession();
      pollRepoStatus();
    }

    function toggleAddRepoForm() {
      const form = document.getElementById('addRepoForm');
      form.style.display = form.style.display === 'none' ? 'block' : 'none';
      refreshIcons(form);
    }

    async function submitNewRepo() {
      const name = document.getElementById('newRepoName').value.trim();
      const id = document.getElementById('newRepoId').value.trim();
      if (!name) { showToast('Please enter repository name', 'warning'); return; }

      const res = await fetch('/api/repos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_name: name, repo_id: id || undefined })
      });
      if (res.ok) {
        toggleAddRepoForm();
        document.getElementById('newRepoName').value = '';
        document.getElementById('newRepoId').value = '';
        showToast(`Repository '${name}' created successfully`, 'success');
        await loadRepos();
      } else {
        const err = await res.json();
        showToast('Error creating repo: ' + (err.detail || err), 'error');
      }
    }

    async function deleteRepo(repoId) {
      showConfirmDialog({
        title: 'Delete Repository',
        message: `Are you sure you want to delete repository '${repoId}' and all its indexed data?`,
        confirmText: 'Delete Repo',
        isDanger: true,
        onConfirm: async () => {
          await fetch(`/api/repos/${encodeURIComponent(repoId)}`, { method: 'DELETE' });
          showToast(`Repository '${repoId}' deleted`, 'info');
          await loadRepos();
        }
      });
    }

    async function reindexRepo(repoId) {
      showToast(`Re-indexing repository '${repoId}'...`, 'info');
      const res = await fetch(`/api/repos/${encodeURIComponent(repoId)}/reindex`, { method: 'POST' });
      const data = await res.json();
      showToast(`Re-indexed ${data.scenarios_indexed} scenarios for repository '${repoId}'!`, 'success');
      loadRepos();
    }

    async function reindexCurrentRepo() {
      await reindexRepo(activeRepoId);
    }

    // Folders
    async function loadFoldersForActiveRepo() {
      if (!activeRepoId) return;
      const res = await fetch(`/api/repos/${encodeURIComponent(activeRepoId)}/folders`);
      const data = await res.json();
      const folders = data.folders || [];

      const list = document.getElementById('foldersList');
      list.innerHTML = '';
      if (folders.length === 0) {
        list.innerHTML = '<p style="color: var(--text-muted); font-size: 13px; padding: 10px;">No folders added yet. Enter a feature directory path above.</p>';
        return;
      }

      folders.forEach(f => {
        const div = document.createElement('div');
        div.className = 'folder-item';
        div.innerHTML = `
          <div>
            <div class="folder-path"><i data-lucide="folder" style="width: 13px; height: 13px; color: var(--accent);"></i> ${escapeHtml(f.folder_path)}</div>
            <div style="font-size: 11.5px; color: var(--text-muted); margin-top: 4px; display: flex; align-items: center; gap: 8px;">
              <span>Scenarios: <b>${f.scenario_count}</b></span>
              <span class="badge badge-live"><i data-lucide="radio" style="width: 11px; height: 11px;"></i> Watching (Live)</span>
            </div>
          </div>
          <div style="display: flex; gap: 6px;">
            <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="reindexFolder('${f.folder_id}')"><i data-lucide="refresh-cw" style="width: 11px; height: 11px;"></i> Reindex</button>
            <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="removeFolder('${f.folder_id}')"><i data-lucide="trash-2" style="width: 11px; height: 11px;"></i> Delete</button>
          </div>
        `;
        list.appendChild(div);
      });
      refreshIcons(list);
    }

    async function submitAddFolder() {
      const input = document.getElementById('newFolderPath');
      const pathVal = input.value.trim();
      if (!pathVal) { showToast('Please enter a folder path', 'warning'); return; }

      const res = await fetch(`/api/repos/${encodeURIComponent(activeRepoId)}/folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: pathVal })
      });
      if (res.ok) {
        input.value = '';
        showToast('Folder added and indexed into watchdog', 'success');
        await loadRepos();
        await loadFoldersForActiveRepo();
        pollWatchdogStatus();
      } else {
        const err = await res.json();
        showToast('Error adding folder: ' + (err.detail || err), 'error');
      }
    }

    async function removeFolder(folderId) {
      showConfirmDialog({
        title: 'Remove Folder',
        message: 'Remove this folder from indexing and watchdog monitoring?',
        confirmText: 'Remove Folder',
        isDanger: true,
        onConfirm: async () => {
          await fetch(`/api/folders/${encodeURIComponent(folderId)}`, { method: 'DELETE' });
          showToast('Folder removed from index', 'info');
          await loadRepos();
          await loadFoldersForActiveRepo();
        }
      });
    }

    async function reindexFolder(folderId) {
      showToast('Re-indexing folder scenarios...', 'info');
      const res = await fetch(`/api/folders/${encodeURIComponent(folderId)}/reindex`, { method: 'POST' });
      const data = await res.json();
      showToast(`Re-indexed ${data.scenarios_indexed} scenarios for this folder!`, 'success');
      loadRepos();
    }

    // ==================== LIVE WATCHDOG TELEMETRY ====================

    async function pollWatchdogStatus() {
      try {
        const res = await fetch('/api/watcher/status');
        const data = await res.json();
        const w = data.watcher;

        document.getElementById('watchdogLiveCount').innerHTML = `<i data-lucide="radio" style="width: 11px; height: 11px;"></i> Watching ${w.watched_count} Folders`;
        document.getElementById('watchdogStatusText').textContent = `Watchdog: Monitoring ${w.watched_count} Folder(s)`;

        const feed = document.getElementById('activityFeed');
        if (w.recent_events && w.recent_events.length > 0) {
          feed.innerHTML = w.recent_events.map(e => `
            <div class="event-entry ${e.event_type}">
              <i data-lucide="${e.event_type === 'deleted' ? 'file-minus' : (e.event_type === 'created' ? 'file-plus' : 'file-edit')}" style="width: 13px; height: 13px; flex-shrink: 0;"></i>
              <span><b>[${e.time}]</b> File <b>${e.event_type}</b>: <code>${escapeHtml(e.file_name)}</code> (Repo: <b>${escapeHtml(e.repo_id)}</b>, ${e.scenarios_count} scenarios re-indexed)</span>
            </div>
          `).join('');
          refreshIcons(feed);
        }
        refreshIcons(document.getElementById('watchdogLiveCount'));
      } catch (err) {}
    }

    function loadIndexerData() {
      loadRepos();
      pollWatchdogStatus();
    }

    // ==================== RAG BOT CHAT ====================

    async function loadChatSessions() {
      try {
        const res = await fetch('/api/chat-sessions' + (activeRepoId ? '?repo_id=' + encodeURIComponent(activeRepoId) : ''));
        const data = await res.json();
        const list = document.getElementById('chatSessionsList');
        if (!list) return;
        list.innerHTML = '';
        const sessions = data.chat_sessions || [];

        if (sessions.length === 0) {
          list.innerHTML = '<p style="color: var(--text-muted); font-size: 12px; padding: 12px 10px;">No chat sessions.</p>';
          activeChatId = null;
          return;
        }

        sessions.forEach(s => {
          const div = document.createElement('div');
          div.className = 'session-item' + (s.chat_id === activeChatId ? ' active' : '');
          div.onclick = () => selectChatSession(s.chat_id);
          div.innerHTML = `
            <i data-lucide="message-square" style="width: 14px; height: 14px; color: var(--text-muted); flex-shrink: 0;"></i>
            <div style="overflow: hidden; flex: 1;">
              <div class="session-title">${escapeHtml(s.title || 'Conversation')}</div>
              <div class="session-time">${String(s.updated_at || s.created_at).slice(0, 16)}</div>
            </div>
            <button class="btn btn-danger" aria-label="Delete chat session" style="padding: 2px 6px; font-size: 10px;" onclick="event.stopPropagation(); deleteChatSession('${s.chat_id}')"><i data-lucide="trash-2" style="width: 10px; height: 10px;"></i></button>
          `;
          list.appendChild(div);
        });

        refreshIcons(list);

        if (!activeChatId && sessions.length > 0) {
          selectChatSession(sessions[0].chat_id);
        }
      } catch (err) {
        console.error('Error loading chat sessions:', err);
      }
    }

    function createNewChatSession() {
      activeChatId = null;
      document.getElementById('chatMessages').innerHTML = `
        <div class="chat-bubble assistant">
          <div style="display: flex; align-items: flex-start; gap: 10px;">
            <i data-lucide="sparkles" style="width: 18px; height: 18px; color: var(--accent); flex-shrink: 0; margin-top: 2px;"></i>
            <div>Ready for a new inquiry in repository <b>${activeRepoId}</b>. What test scenario or requirement would you like to verify?</div>
          </div>
        </div>
      `;
      document.getElementById('citationsList').innerHTML = '';
      refreshIcons(document.getElementById('chatMessages'));
      loadChatSessions();
    }

    async function selectChatSession(chatId) {
      activeChatId = chatId;
      const res = await fetch('/api/chat-history/' + encodeURIComponent(chatId));
      const data = await res.json();
      if (data.repo_id && data.repo_id !== activeRepoId) {
        activeRepoId = data.repo_id;
        const selector = document.getElementById('globalRepoSelector');
        if (selector) selector.value = data.repo_id;
        const titleEl = document.getElementById('selectedRepoTitle');
        if (titleEl) titleEl.textContent = data.repo_id;
      }
      loadChatSessions();
      const box = document.getElementById('chatMessages');
      box.innerHTML = '';

      if (!data.messages || data.messages.length === 0) {
        box.innerHTML = `
          <div class="chat-bubble assistant">
            <div style="display: flex; align-items: center; gap: 8px;">
              <i data-lucide="message-circle" style="width: 16px; height: 16px; color: var(--accent);"></i>
              <span>Empty conversation. Type your question below!</span>
            </div>
          </div>
        `;
        refreshIcons(box);
        return;
      }

      let lastCitations = [];
      data.messages.forEach(m => {
        if (m.role === 'user') {
          addUserMessage(m.content);
        } else {
          const isCached = m.cached || (m.agent_trace && m.agent_trace.cached) || false;
          addAssistantMessage(m.content, m.citations, isCached, m.agent_trace);
          if (m.citations && m.citations.length > 0) lastCitations = m.citations;
        }
      });
      renderCitationsSidebar(lastCitations);
      refreshIcons(box);
    }

    async function deleteChatSession(chatId) {
      try {
        await fetch('/api/chat-sessions/' + encodeURIComponent(chatId), { method: 'DELETE' });
        showToast('Chat session deleted', 'info');
        if (activeChatId === chatId) {
          activeChatId = null;
          document.getElementById('chatMessages').innerHTML = `
            <div class="chat-bubble assistant">
              <div style="display: flex; align-items: flex-start; gap: 10px;">
                <i data-lucide="sparkles" style="width: 18px; height: 18px; color: var(--accent); flex-shrink: 0; margin-top: 2px;"></i>
                <div>Chat session deleted. What would you like to verify?</div>
              </div>
            </div>
          `;
          document.getElementById('citationsList').innerHTML = '';
          refreshIcons(document.getElementById('chatMessages'));
        }
        await loadChatSessions();
      } catch (err) {
        showToast('Error deleting session: ' + err, 'error');
      }
    }

    async function clearAllChatSessions() {
      showConfirmDialog({
        title: 'Clear Chat Sessions',
        message: 'Clear all chat sessions and reset semantic cache for this repository?',
        confirmText: 'Clear All',
        isDanger: true,
        onConfirm: async () => {
          try {
            await fetch('/api/chat-sessions-clear', { method: 'POST' });
            activeChatId = null;
            document.getElementById('chatMessages').innerHTML = `
              <div class="chat-bubble assistant">
                <div style="display: flex; align-items: flex-start; gap: 10px;">
                  <i data-lucide="sparkles" style="width: 18px; height: 18px; color: var(--accent); flex-shrink: 0; margin-top: 2px;"></i>
                  <div>All chat sessions cleared and cache reset. What would you like to verify?</div>
                </div>
              </div>
            `;
            document.getElementById('citationsList').innerHTML = '';
            refreshIcons(document.getElementById('chatMessages'));
            showToast('All chat sessions cleared & cache reset', 'success');
            await loadChatSessions();
          } catch (err) {
            showToast('Error clearing sessions: ' + err, 'error');
          }
        }
      });
    }

    // ==================== ANTHROPIC-STYLE REAL-TIME STREAMING AGENT LOOP ====================

    let liveThinkingTimer = null;
    let liveThinkingStart = 0;

    function showLiveAgentThinking() {
      removeLiveAgentThinking();
      const box = document.getElementById('chatMessages');
      const div = document.createElement('div');
      div.id = 'liveAgentThinkingBox';
      div.className = 'live-thought-container';

      div.innerHTML = `
        <div class="live-thought-header">
          <div class="live-thought-title-group">
            <div class="anthropic-spinner-ring">
              <div class="anthropic-spinner-center"></div>
            </div>
            <span class="live-agent-badge"><i data-lucide="sparkles" style="width: 12px; height: 12px;"></i> Live Agentic Execution</span>
            <span id="liveStatusShimmer" class="shimmer-text" style="font-size: 12px; font-weight: 600;">Initializing verification pipeline...</span>
          </div>
          <span id="liveElapsedTimer" style="font-size: 11px; color: #64748b; font-family: 'Fira Code', monospace;">0.0s</span>
        </div>
        <div class="live-step-list" id="liveStepList"></div>
      `;

      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
      refreshIcons(div);

      liveThinkingStart = Date.now();
      liveThinkingTimer = setInterval(() => {
        const elapsed = ((Date.now() - liveThinkingStart) / 1000).toFixed(1);
        const timerEl = document.getElementById('liveElapsedTimer');
        if (timerEl) timerEl.textContent = `${elapsed}s`;
      }, 100);
    }

    function handleLiveAgentEvent(evt) {
      const stepList = document.getElementById('liveStepList');
      const shimmerEl = document.getElementById('liveStatusShimmer');
      if (!stepList) return;

      if (evt.type === 'stage_start') {
        if (shimmerEl) shimmerEl.textContent = evt.name || 'Processing...';

        // Mark any currently active steps as completed (if not already completed)
        stepList.querySelectorAll('.live-step-item.active').forEach(el => {
          el.className = 'live-step-item completed';
          const icon = el.querySelector('.step-indicator-icon');
          if (icon) icon.innerHTML = '<i data-lucide="check" style="width: 11px; height: 11px; color: #4ade80;"></i>';
        });

        // Add or activate this step
        let stepEl = document.getElementById('liveStage_' + evt.stage_id);
        if (!stepEl) {
          stepEl = document.createElement('div');
          stepEl.id = 'liveStage_' + evt.stage_id;
          stepList.appendChild(stepEl);
        }
        stepEl.className = 'live-step-item active';
        stepEl.innerHTML = `
          <div class="live-step-header">
            <div class="live-step-left">
              <div class="step-indicator-icon"><i data-lucide="loader-2" class="spin-icon" style="width: 11px; height: 11px; color: var(--accent);"></i></div>
              <span>${escapeHtml(evt.name)}</span>
            </div>
            <span class="live-step-dur" id="dur_${evt.stage_id}">active</span>
          </div>
          ${evt.detail ? `<div class="live-step-detail">${escapeHtml(evt.detail)}</div>` : ''}
        `;
        refreshIcons(stepEl);
        const box = document.getElementById('chatMessages');
        if (box) box.scrollTop = box.scrollHeight;
      } else if (evt.type === 'stage_end' && evt.stage) {
        let stepEl = document.getElementById('liveStage_' + evt.stage.id);
        if (!stepEl) {
          stepEl = document.createElement('div');
          stepEl.id = 'liveStage_' + evt.stage.id;
          stepList.appendChild(stepEl);
        }
        stepEl.className = 'live-step-item completed';
        const durText = evt.stage.duration_ms !== undefined ? `${evt.stage.duration_ms}ms` : '';
        stepEl.innerHTML = `
          <div class="live-step-header">
            <div class="live-step-left">
              <div class="step-indicator-icon"><i data-lucide="check" style="width: 11px; height: 11px; color: #4ade80;"></i></div>
              <span>${escapeHtml(evt.stage.name)}</span>
            </div>
            <span class="live-step-dur">${durText}</span>
          </div>
          ${evt.stage.detail ? `<div class="live-step-detail">${escapeHtml(evt.stage.detail)}</div>` : ''}
        `;
        refreshIcons(stepEl);
        const box = document.getElementById('chatMessages');
        if (box) box.scrollTop = box.scrollHeight;
      }
    }

    function removeLiveAgentThinking() {
      if (liveThinkingTimer) {
        clearInterval(liveThinkingTimer);
        liveThinkingTimer = null;
      }
      const el = document.getElementById('liveAgentThinkingBox');
      if (el) el.remove();
    }

    function renderThoughtAccordion(trace) {
      if (!trace) return '';
      const durationSec = trace.total_duration_sec !== undefined ? trace.total_duration_sec : (trace.total_duration_ms ? (trace.total_duration_ms / 1000).toFixed(1) : '1.2');
      const callsCount = trace.llm_calls_count || 1;
      const wasRetried = trace.was_retried;
      const retryStrategy = trace.retry_strategy || 'NONE';

      let statusBadge = '';
      if (trace.cached) {
        statusBadge = '<span class="badge" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; font-size: 10.5px;"><i data-lucide="zap" style="width: 10px; height: 10px;"></i> Semantic Cache</span>';
      } else if (wasRetried) {
        statusBadge = `<span class="badge" style="background: rgba(234, 179, 8, 0.15); color: #fde047; font-size: 10.5px;"><i data-lucide="refresh-cw" style="width: 10px; height: 10px;"></i> Retried (${retryStrategy})</span>`;
      } else {
        statusBadge = '<span class="badge" style="background: rgba(34, 197, 94, 0.15); color: #4ade80; font-size: 10.5px;"><i data-lucide="check" style="width: 10px; height: 10px;"></i> Sufficient (1 Call)</span>';
      }

      const stages = trace.stages || [
        { name: 'Sparse + Dense Search', detail: 'Retrieved Top 50 BM25 + Top 50 Milvus candidate pools', duration_ms: 32 },
        { name: 'Balanced RRF & Rerank', detail: 'Reciprocal Rank Fusion (Top 25) ➔ Cross-Encoder (Top 10)', duration_ms: 24 },
        { name: 'LLM Grounded Evaluation', detail: 'Evaluated candidate scenarios with Set-Union criteria', duration_ms: Math.round(durationSec * 800) }
      ];

      const timelineHtml = stages.map(st => {
        const isRetriedNode = st.id === 'retry';
        const nodeClass = isRetriedNode ? 'timeline-node retried' : 'timeline-node completed';
        return `
          <div class="${nodeClass}">
            <div class="timeline-header">
              <span>${escapeHtml(st.name)}</span>
              <span class="timeline-dur">${st.duration_ms ? st.duration_ms + 'ms' : ''}</span>
            </div>
            <div class="timeline-detail">${escapeHtml(st.detail || '')}</div>
          </div>
        `;
      }).join('');

      const callsLabel = trace.cached || callsCount === 0 ? '0 LLM Calls (Cached)' : `${callsCount} LLM Call${callsCount > 1 ? 's' : ''}`;

      return `
        <div class="anthropic-thought-card">
          <button class="thought-toggle-btn" onclick="this.parentElement.classList.toggle('expanded')">
            <div class="thought-header-left">
              <i data-lucide="sparkles" class="thought-sparkle" style="width: 14px; height: 14px;"></i>
              <span>Thought for ${durationSec}s</span>
              <span style="opacity: 0.4">•</span>
              <span style="color: #94a3b8; font-size: 11.5px;">${callsLabel}</span>
              ${statusBadge}
            </div>
            <i data-lucide="chevron-down" class="thought-chevron" style="width: 14px; height: 14px;"></i>
          </button>
          <div class="thought-drawer">
            <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Agent Execution Trace & Verification Loop</div>
            <div class="thought-timeline">
              ${timelineHtml}
            </div>
          </div>
        </div>
      `;
    }

    async function sendChatMessage() {
      const input = document.getElementById('chatInput');
      const msg = input.value.trim();
      if (!msg) return;

      addUserMessage(msg);
      input.value = '';
      showLiveAgentThinking();

      try {
        const response = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: msg,
            repo_id: activeRepoId,
            chat_id: activeChatId
          })
        });

        if (!response.ok) {
          const errData = await response.json().catch(() => ({ detail: response.statusText }));
          removeLiveAgentThinking();
          addAssistantMessage('Error: ' + (errData.detail || response.statusText));
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              const dataStr = trimmed.slice(6);
              try {
                const evt = JSON.parse(dataStr);
                if (evt.type === 'done') {
                  removeLiveAgentThinking();
                  activeChatId = evt.chat_id;
                  addAssistantMessage(evt.reply, evt.citations, evt.cached, evt.agent_trace);
                  renderCitationsSidebar(evt.citations);
                  loadChatSessions();
                } else {
                  handleLiveAgentEvent(evt);
                }
              } catch (e) {
                console.error('Error parsing SSE event', e);
              }
            }
          }
        }
      } catch (err) {
        removeLiveAgentThinking();
        addAssistantMessage('Error communicating with backend: ' + err);
      }
    }

    async function openFileLocation(filePath, event) {
      if (event) event.stopPropagation();
      if (!filePath) return;
      try {
        const res = await fetch('/api/open-file-location', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_path: filePath })
        });
        const data = await res.json();
        if (res.ok) {
          showToast('Revealed feature file in File Explorer', 'success');
        } else {
          showToast('Could not open location: ' + (data.detail || 'File not found on disk'), 'error');
        }
      } catch (err) {
        showToast('Error opening folder: ' + err, 'error');
      }
    }

    function addUserMessage(text) {
      const box = document.getElementById('chatMessages');
      const div = document.createElement('div');
      div.className = 'chat-bubble user';
      div.textContent = text;
      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
    }

    function addAssistantMessage(text, citations = [], isCached = false, agentTrace = null) {
      const box = document.getElementById('chatMessages');
      const div = document.createElement('div');
      div.className = 'chat-bubble assistant';

      const reportId = 'rep_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);
      window.reportStore[reportId] = { text, citations, isCached, agentTrace };

      const thoughtAccordionHtml = renderThoughtAccordion(agentTrace);

      // Check if this is a structured evaluation response
      const isEval = text && (text.includes('Coverage Assessment') || text.includes('### 1.') || text.includes('Analysis & Grounded Evidence'));

      if (isEval) {
        // Extract Status & Match Percentage
        let statusText = 'Evaluated';
        let badgeColor = 'var(--accent)';
        const statusMatch = text.match(/Status:\s*([^\n\*\#]+)/i) || text.match(/Coverage:\s*([^\n\*\#]+)/i);
        if (statusMatch) {
          statusText = statusMatch[1].trim();
          const pctMatch = statusText.match(/(\d{1,3})%/);
          if (pctMatch) {
            const val = parseInt(pctMatch[1]);
            badgeColor = val >= 75 ? 'var(--green)' : (val >= 35 ? 'var(--yellow)' : 'var(--red)');
          } else if (statusText.toLowerCase().includes('fully') || statusText.toLowerCase().includes('covered')) {
            badgeColor = 'var(--green)';
          } else if (statusText.toLowerCase().includes('partial')) {
            badgeColor = 'var(--yellow)';
          }
        }

        // Extract Executive Analysis Summary (1-2 clean summary paragraphs)
        let summaryText = '';
        const s2Match = text.match(/###\s*2\.\s*Analysis[^\n]*\n+([\s\S]*?)(?=(###\s*3|\n\s*\* Feature:|\n\s*Scenario:|$))/i);
        if (s2Match) {
          summaryText = s2Match[1].trim();
        }
        if (!summaryText) {
          const lines = text.split('\n').filter(l => l.trim() && !l.startsWith('#') && !l.startsWith('*') && !l.startsWith('---'));
          summaryText = lines.slice(0, 2).join(' ') || 'Evaluation completed against test scenarios in the repository.';
        }

        // Build Citation pills (only for cited scenarios with verified coverage)
        let citationPillsHtml = '';
        if (citations && citations.length > 0) {
          const citedList = citations.filter(c => c.is_cited && c.match_percentage > 0);
          if (citedList.length > 0) {
            const pills = citedList.slice(0, 4).map(c => {
              const fileName = getFileName(c.file_path);
              const pct = c.match_percentage || 0;
              const pillColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)');
              const rawPath = c.file_path || '';
              return `
                <div style="display: inline-flex; align-items: center; gap: 4px; margin-right: 6px; margin-top: 6px;">
                  <span class="citation-pill" title="Click to view verified Gherkin steps" onclick="openScenarioModal('${c.scenario_id}')">
                    <i data-lucide="file-code-2" style="width: 12px; height: 12px;"></i>
                    <strong>${escapeHtml(c.scenario_name)}</strong>
                    <span style="opacity: 0.8">(${fileName}:${c.line_number})</span>
                    <span class="badge" style="background: rgba(255,255,255,0.08); color: ${pillColor}; font-size: 10px; padding: 1px 5px; margin-left: 4px;">${pct}% Coverage</span>
                  </span>
                  <button class="btn-icon-folder" data-filepath="${escapeHtml(rawPath)}" title="Open feature folder in Explorer" onclick="openFileLocation(this.getAttribute('data-filepath'), event)">
                    <i data-lucide="folder" style="width: 13px; height: 13px;"></i>
                  </button>
                </div>
              `;
            }).join('');
            citationPillsHtml = `<div style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 4px;">${pills}</div>`;
          }
        }

        div.innerHTML = `
          ${thoughtAccordionHtml}
          <div class="eval-summary-card">
            <div class="eval-header">
              <div class="eval-title"><i data-lucide="shield-check" style="width: 16px; height: 16px; color: var(--accent);"></i> Coverage Assessment</div>
              <span class="badge" style="background: rgba(255,255,255,0.08); color: ${badgeColor}; font-size: 12px; padding: 3px 9px;">${escapeHtml(statusText)}</span>
            </div>
            <div class="eval-summary-text">${formatMarkdown(summaryText)}</div>
            ${citationPillsHtml}
            <div style="margin-top: 14px; display: flex; justify-content: flex-end;">
              <button class="btn-report" onclick="openReportModal('${reportId}')">
                <i data-lucide="file-text" style="width: 13px; height: 13px;"></i> View Full Details & Grounded Evidence <i data-lucide="arrow-right" style="width: 13px; height: 13px;"></i>
              </button>
            </div>
          </div>
        `;
      } else {
        // Standard conversational chat bubble
        div.innerHTML = thoughtAccordionHtml + formatMarkdown(text);
        if (citations && citations.length > 0) {
          const citedList = citations.filter(c => c.is_cited && c.match_percentage > 0);
          if (citedList.length > 0) {
            const p = document.createElement('div');
            p.style.marginTop = '12px';
            p.style.display = 'flex';
            p.style.flexWrap = 'wrap';
            p.style.gap = '4px';
            citedList.slice(0, 4).forEach(c => {
              const fileName = getFileName(c.file_path);
              const pct = c.match_percentage || 0;
              const badgeColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)');
              const rawPath = c.file_path || '';
              const itemDiv = document.createElement('div');
              itemDiv.style.display = 'inline-flex';
              itemDiv.style.alignItems = 'center';
              itemDiv.style.gap = '4px';
              itemDiv.style.marginRight = '6px';
              itemDiv.style.marginTop = '6px';
              itemDiv.innerHTML = `
                <span class="citation-pill" onclick="openScenarioModal('${c.scenario_id}')">
                  <i data-lucide="file-code-2" style="width: 12px; height: 12px;"></i>
                  <strong>${escapeHtml(c.scenario_name)}</strong>
                  <span style="opacity: 0.8">(${fileName}:${c.line_number})</span>
                  <span class="badge" style="background: rgba(255,255,255,0.08); color: ${badgeColor}; font-size: 10px; margin-left: 4px;">${pct}% Coverage</span>
                </span>
                <button class="btn-icon-folder" data-filepath="${escapeHtml(rawPath)}" title="Open feature folder in Explorer" onclick="openFileLocation(this.getAttribute('data-filepath'), event)">
                  <i data-lucide="folder" style="width: 13px; height: 13px;"></i>
                </button>
              `;
              p.appendChild(itemDiv);
            });
            div.appendChild(p);
          }
        }
      }

      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
      refreshIcons(div);
    }

    function renderCitationsSidebar(citations) {
      const list = document.getElementById('citationsList');
      list.innerHTML = '';
      if (!citations || citations.length === 0) {
        list.innerHTML = '<p style="color: var(--text-muted); font-size: 12.5px;">No direct scenario citations found for this query.</p>';
        return;
      }
      citations.forEach(c => {
        const card = document.createElement('div');
        card.className = 'citation-card';
        card.onclick = () => openScenarioModal(c.scenario_id);
        const fileName = getFileName(c.file_path);
        const pct = c.match_percentage !== undefined ? c.match_percentage : 0;
        const isCited = c.is_cited && pct > 0;
        const badgeColor = isCited ? (pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)')) : '#64748b';
        const badgeLabel = isCited ? `${pct}% Coverage` : '0% (Not Relevant)';
        const rawPath = c.file_path || '';
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 6px;">
            <div class="title" style="flex: 1;"><i data-lucide="${isCited ? 'file-check-2' : 'file-minus'}" style="width: 14px; height: 14px; color: ${isCited ? 'var(--accent)' : '#64748b'}; flex-shrink: 0;"></i> <span>${escapeHtml(c.scenario_name)}</span></div>
            <span class="badge" style="background: rgba(255,255,255,0.06); color: ${badgeColor}; font-size: 10.5px;">${badgeLabel}</span>
          </div>
          <div class="meta" style="margin-top: 6px;">Feature: ${escapeHtml(c.feature_title || '')}</div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
            <div class="meta" style="color: var(--accent); margin-top: 0;">${escapeHtml(fileName)} : Line ${c.line_number}</div>
            <button class="btn-icon-folder" data-filepath="${escapeHtml(rawPath)}" title="Open feature folder in Explorer" style="padding: 2px 7px; font-size: 10.5px;" onclick="openFileLocation(this.getAttribute('data-filepath'), event)">
              <i data-lucide="folder" style="width: 12px; height: 12px;"></i> Open
            </button>
          </div>
        `;
        list.appendChild(card);
      });
      refreshIcons(list);
    }

    // ==================== POPUP MODALS ====================

    async function openScenarioModal(scenarioId) {
      if (!scenarioId) return;
      try {
        const res = await fetch(`/api/scenario/${encodeURIComponent(scenarioId)}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Server returned ${res.status}`);
        }
        const data = await res.json();
        const sc = data.scenario;
        document.getElementById('modalTitle').innerHTML = `<i data-lucide="file-code" style="width: 18px; height: 18px; color: var(--accent);"></i> <span>${escapeHtml(sc.scenario_name)}</span>`;
        const fileName = getFileName(sc.file_path);
        const featName = sc.feature_name || sc.feature_title || 'Feature';
        const rawPath = sc.file_path || '';
        document.getElementById('modalMeta').innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px;">
            <div>
              <div><strong>Feature:</strong> ${escapeHtml(featName)}</div>
              <div style="margin-top: 4px;"><strong>Location:</strong> <code style="color: var(--accent);">${escapeHtml(fileName)} (Line ${sc.line_number})</code></div>
              <div style="margin-top: 4px; font-size: 12px; opacity: 0.8;">${escapeHtml(rawPath)}</div>
            </div>
            <button class="btn btn-secondary" data-filepath="${escapeHtml(rawPath)}" style="padding: 6px 12px; font-size: 11.5px; display: flex; align-items: center; gap: 6px; flex-shrink: 0;" onclick="openFileLocation(this.getAttribute('data-filepath'), event)">
              <i data-lucide="folder-open" style="width: 14px; height: 14px; color: var(--accent);"></i> Reveal in Explorer
            </button>
          </div>
          ${sc.tags && sc.tags.length ? `<div style="margin-top: 8px;">${sc.tags.map(t => `<span class="badge" style="background: rgba(255,255,255,0.06); border: 1px solid #383838; color: #ececec; font-size: 11px; margin-right: 4px;">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
        `;
        document.getElementById('modalGherkin').textContent = sc.raw_gherkin || sc.canonical_text;
        document.getElementById('scenarioModal').classList.add('show');
        refreshIcons(document.getElementById('scenarioModal'));
      } catch (e) {
        showToast('Could not load scenario details: ' + e.message, 'error');
      }
    }

    function closeScenarioModal() {
      document.getElementById('scenarioModal').classList.remove('show');
    }

    function openReportModal(reportId) {
      const item = window.reportStore[reportId];
      if (!item) return;
      window.activeReportId = reportId;

      document.getElementById('reportModalSubtitle').textContent = `Repository: ${activeRepoId} | Grounded against local automation scenarios`;
      document.getElementById('reportModalBody').innerHTML = formatMarkdown(item.text);
      document.getElementById('reportModal').classList.add('show');
      refreshIcons(document.getElementById('reportModal'));
    }

    function closeReportModal() {
      document.getElementById('reportModal').classList.remove('show');
      window.activeReportId = null;
    }

    function copyReportModalContent() {
      if (!window.activeReportId || !window.reportStore[window.activeReportId]) return;
      const text = window.reportStore[window.activeReportId].text;
      navigator.clipboard.writeText(text).then(() => {
        showToast('Full evaluation report copied to clipboard!', 'success');
      });
    }

    function copyCode(btn) {
      const wrapper = btn.closest('.code-block-wrapper');
      const code = wrapper.querySelector('.gherkin-viewer').textContent;
      navigator.clipboard.writeText(code).then(() => {
        showToast('Gherkin code copied to clipboard', 'success');
        const oldHtml = btn.innerHTML;
        btn.innerHTML = '<i data-lucide="check" style="width: 12px; height: 12px;"></i> Copied!';
        refreshIcons(btn);
        setTimeout(() => {
          btn.innerHTML = oldHtml;
          refreshIcons(btn);
        }, 2000);
      });
    }

    async function pollRepoStatus() {
      if (!activeRepoId) return;
      try {
        const res = await fetch(`/api/repos/${encodeURIComponent(activeRepoId)}/status`);
        const data = await res.json();
        const st = data.index_status;

        const statusPill = document.getElementById('repoStatusPill');
        const statusPulse = document.getElementById('repoStatusPulse');
        const statusText = document.getElementById('repoStatusText');
        const banner = document.getElementById('indexingBanner');
        const bannerText = document.getElementById('indexingBannerText');
        const progressBar = document.getElementById('indexingProgressBar');
        const chatInput = document.getElementById('chatInput');
        const sendBtn = document.getElementById('chatSendBtn');

        if (st.index_status === 'INDEXING') {
          // Yellow pulse
          statusPill.style.background = 'rgba(234, 179, 8, 0.12)';
          statusPill.style.borderColor = 'rgba(234, 179, 8, 0.3)';
          statusPill.style.color = '#fde047';
          statusPulse.style.background = '#fde047';
          statusPulse.style.boxShadow = '0 0 8px #fde047';
          statusText.textContent = `Indexing: ${st.current_indexing_file || 'In progress'} (${st.indexing_progress_pct}%)`;

          // Show banner
          banner.style.display = 'flex';
          bannerText.textContent = `Indexing in progress: ${st.current_indexing_file || 'Processing'} (${st.indexing_progress_pct}%). Analysis will unlock when completed.`;
          progressBar.style.width = `${st.indexing_progress_pct}%`;

          // Disable chat inputs
          if (chatInput) {
            chatInput.disabled = true;
            chatInput.placeholder = '⚠️ Indexing in progress — please wait until completed...';
          }
          if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.style.opacity = '0.5';
            sendBtn.style.cursor = 'not-allowed';
          }
        } else if (st.index_status === 'ERROR') {
          statusPill.style.background = 'rgba(239, 68, 68, 0.12)';
          statusPill.style.borderColor = 'rgba(239, 68, 68, 0.3)';
          statusPill.style.color = '#f87171';
          statusPulse.style.background = '#f87171';
          statusPulse.style.boxShadow = '0 0 8px #f87171';
          statusText.textContent = 'Index Error';
          banner.style.display = 'none';
          if (chatInput) chatInput.disabled = false;
          if (sendBtn) { sendBtn.disabled = false; sendBtn.style.opacity = '1'; sendBtn.style.cursor = 'pointer'; }
        } else {
          // Green Up to date
          statusPill.style.background = 'rgba(34, 197, 94, 0.12)';
          statusPill.style.borderColor = 'rgba(34, 197, 94, 0.3)';
          statusPill.style.color = 'var(--green)';
          statusPulse.style.background = 'var(--green)';
          statusPulse.style.boxShadow = '0 0 8px var(--green)';
          statusText.textContent = ` Up to date (${st.scenario_count} scenarios, v${st.corpus_version})`;

          // Hide banner
          banner.style.display = 'none';

          // Enable chat inputs
          if (chatInput && chatInput.disabled) {
            chatInput.disabled = false;
            chatInput.placeholder = 'Ask about requirement coverage, login scenarios, checkout tests...';
          }
          if (sendBtn && sendBtn.disabled) {
            sendBtn.disabled = false;
            sendBtn.style.opacity = '1';
            sendBtn.style.cursor = 'pointer';
          }
        }
      } catch (err) {}
    }

    // Auto-poll watchdog and repo status every 2.5 seconds
    setInterval(pollWatchdogStatus, 2500);
    setInterval(pollRepoStatus, 2500);

    // Initial Load
    window.onload = () => {
      initTheme();
      loadRepos();
      pollWatchdogStatus();
      pollRepoStatus();
      refreshIcons();
    };
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTML_DASHBOARD_TEMPLATE
