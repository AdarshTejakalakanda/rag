"""FastAPI Web Application & RAG Chatbot Dashboard with Integrated Indexer & Multi-Folder Watchdog."""

import os
import sys
import json
import shutil
import time
from pathlib import Path
from typing import Optional, List, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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


class NewChatSessionRequest(BaseModel):
    repo_id: str
    title: Optional[str] = None


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
        repo_info = p.repo_manager.add_repository(
            repo_name=req.repo_name,
            repo_path=req.repo_path or "",
            repo_id=req.repo_id,
            branch=req.branch or "main",
        )
        count = 0
        if req.repo_path and Path(req.repo_path).exists() and Path(req.repo_path).is_dir():
            count = p.index_features(
                feature_dir=req.repo_path,
                repo_id=repo_info["repo_id"],
                repo_name=repo_info["repo_name"]
            )
            wm.add_watch_directory(req.repo_path, repo_id=repo_info["repo_id"])

        return {"status": "success", "repository": repo_info, "scenarios_indexed": count}
    except Exception as e:
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
    folder_path = Path(req.folder_path).resolve()
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {req.folder_path}")

    try:
        # 1. Parse and index features in this folder
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


@app.delete("/api/chat-sessions/{chat_id}")
async def delete_chat_session(chat_id: str):
    p = get_pipeline()
    try:
        p.state_db.delete_chat_session(chat_id)
        return {"status": "success", "deleted_chat_id": chat_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/chat-sessions/clear")
async def clear_all_chat_sessions(repo_id: Optional[str] = None):
    p = get_pipeline()
    try:
        p.state_db.clear_chat_sessions(repo_id=repo_id)
        return {"status": "success", "cleared_repo": repo_id or "all"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/chat-history/{chat_id}")
async def get_chat_history(chat_id: str):
    p = get_pipeline()
    history = p.state_db.get_chat_history(chat_id)
    return {"status": "success", "chat_id": chat_id, "messages": history}


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
        res = p.chat(message=req.message, repo_id=req.repo_id, chat_id=req.chat_id)
        return {
            "status": "success",
            "chat_id": res["chat_id"],
            "repo_id": res["repo_id"],
            "reply": res["reply"],
            "citations": res["citations"],
            "cached": res.get("cached", False),
        }
    except Exception as e:
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
  <style>
    :root {
      --bg: #0b1120;
      --card-bg: #131d31;
      --card-border: #1e293b;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.18);
      --purple: #a855f7;
      --purple-glow: rgba(168, 85, 247, 0.15);
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --green: #22c55e;
      --yellow: #eab308;
      --red: #ef4444;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    /* Modern Visible Custom Scrollbars */
    * {
      scrollbar-width: thin;
      scrollbar-color: #334155 rgba(15, 23, 42, 0.6);
    }
    ::-webkit-scrollbar {
      width: 7px;
      height: 7px;
    }
    ::-webkit-scrollbar-track {
      background: rgba(15, 23, 42, 0.6);
    }
    ::-webkit-scrollbar-thumb {
      background: #334155;
      border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--accent);
    }

    html, body {
      height: 100%;
      max-height: 100vh;
      overflow: hidden;
    }
    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
    }
    
    /* Top Header */
    header {
      background: #0f172a;
      border-bottom: 1px solid var(--card-border);
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
    .brand { display: flex; align-items: center; gap: 10px; }
    .brand-icon { font-size: 22px; color: var(--accent); }
    .brand-title { font-weight: 800; font-size: 17px; letter-spacing: -0.3px; color: #fff; }
    .header-actions { display: flex; align-items: center; gap: 14px; }
    .watchdog-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 9999px;
      background: rgba(34, 197, 94, 0.12);
      border: 1px solid rgba(34, 197, 94, 0.3);
      color: var(--green);
      font-size: 11.5px;
      font-weight: 600;
    }
    .watchdog-pulse {
      width: 7px; height: 7px; border-radius: 50%; background: var(--green);
      box-shadow: 0 0 8px var(--green);
      animation: pulseAnim 2s infinite;
    }
    @keyframes pulseAnim { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

    .repo-select-box { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-muted); }
    select, input, textarea {
      background: #1e293b;
      border: 1px solid #334155;
      color: #fff;
      padding: 7px 12px;
      border-radius: 7px;
      font-family: inherit;
      font-size: 13px;
      outline: none;
      transition: all 0.2s;
    }
    select:focus, input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }

    .btn {
      background: var(--accent);
      color: #0f172a;
      border: none;
      padding: 7px 14px;
      border-radius: 7px;
      font-weight: 700;
      font-size: 12.5px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }
    .btn:hover { background: #7dd3fc; transform: translateY(-1px); }
    .btn-secondary { background: #1e293b; color: #fff; border: 1px solid #334155; }
    .btn-secondary:hover { background: #334155; }
    .btn-danger { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
    .btn-danger:hover { background: rgba(239, 68, 68, 0.3); }

    /* Navigation Tabs */
    .nav-tabs {
      display: flex;
      background: #0f172a;
      border-bottom: 1px solid var(--card-border);
      padding: 0 24px;
      flex-shrink: 0;
    }
    .tab-btn {
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--text-muted);
      padding: 13px 20px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .tab-btn:hover { color: #fff; }
    .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

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
      border-radius: 12px;
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
    .panel-header h3 { font-size: 15px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }
    
    .repo-card {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 9px;
      padding: 14px;
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: all 0.2s;
    }
    .repo-card:hover { border-color: #334155; }
    .repo-card.selected { border-color: var(--accent); background: #14213d; }
    .repo-info h4 { font-size: 14px; font-weight: 700; color: #fff; }
    .repo-info .meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; font-family: 'Fira Code', monospace; }

    .folder-item {
      background: #0f172a;
      border: 1px solid #1e293b;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 8px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .folder-path { font-family: 'Fira Code', monospace; font-size: 12px; color: var(--accent); word-break: break-all; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 8px;
      border-radius: 5px;
      font-size: 11px;
      font-weight: 700;
    }
    .badge-live { background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.3); }

    /* Live Watchdog Activity Feed */
    .activity-feed {
      background: #070d19;
      border: 1px solid #1e293b;
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
      background: #0f172a;
      border-left: 3px solid var(--accent);
      color: #cbd5e1;
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
      background: #0f172a;
      border-right: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .sessions-header {
      padding: 16px;
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
      margin-bottom: 6px;
      background: transparent;
      border: 1px solid transparent;
      transition: all 0.2s;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .session-item:hover { background: #1e293b; }
    .session-item.active { background: #1e293b; border-color: var(--accent); }
    .session-title { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
      gap: 18px;
    }
    .chat-bubble {
      max-width: 82%;
      padding: 16px 20px;
      border-radius: 12px;
      font-size: 14px;
      line-height: 1.65;
      word-wrap: break-word;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: #0284c7;
      color: #fff;
      border-bottom-right-radius: 2px;
    }
    .chat-bubble.assistant {
      align-self: flex-start;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text);
      border-bottom-left-radius: 2px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
    }
    .chat-bubble code {
      background: #0b1120;
      padding: 2px 6px;
      border-radius: 4px;
      font-family: 'Fira Code', monospace;
      font-size: 12.5px;
      color: #38bdf8;
    }

    .chat-input-container {
      padding: 16px 24px;
      background: #0f172a;
      border-top: 1px solid var(--card-border);
      display: flex;
      gap: 10px;
      flex-shrink: 0;
    }
    .chat-input-container input { flex: 1; padding: 12px 16px; font-size: 14px; border-radius: 9px; }

    .citations-col {
      background: #0f172a;
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
      border-radius: 9px;
      padding: 12px;
      margin-bottom: 10px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .citation-card:hover { border-color: var(--accent); transform: translateY(-2px); }
    .citation-card .title { font-weight: 700; font-size: 13px; color: #fff; }
    .citation-card .meta { font-size: 11.5px; color: var(--text-muted); margin-top: 4px; font-family: 'Fira Code', monospace; }

    .citation-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 9px;
      border-radius: 6px;
      font-size: 12px;
      background: rgba(56, 189, 248, 0.12);
      color: var(--accent);
      margin-right: 6px;
      margin-top: 8px;
      cursor: pointer;
      border: 1px solid rgba(56, 189, 248, 0.3);
      transition: all 0.2s;
    }
    .citation-pill:hover { background: rgba(56, 189, 248, 0.25); }

    /* Evaluation Summary Card in Chat */
    .eval-summary-card {
      background: #0f172a;
      border: 1px solid #1e293b;
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
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .eval-title {
      font-weight: 800;
      font-size: 14px;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .eval-summary-text {
      font-size: 13.5px;
      line-height: 1.65;
      color: #cbd5e1;
      margin-bottom: 12px;
    }
    .btn-report {
      background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
      color: #fff;
      border: 1px solid rgba(56, 189, 248, 0.4);
      padding: 8px 15px;
      border-radius: 7px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
      box-shadow: 0 2px 8px rgba(2, 132, 199, 0.25);
    }
    .btn-report:hover {
      background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(56, 189, 248, 0.35);
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
      background: #1e293b;
      border: 1px solid #334155;
      color: #94a3b8;
      border-radius: 5px;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
      z-index: 2;
    }
    .code-copy-btn:hover {
      color: #fff;
      background: #334155;
      border-color: var(--accent);
    }

    /* Modal */
    .modal {
      display: none;
      position: fixed;
      z-index: 9999;
      left: 0; top: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(5px);
      align-items: center; justify-content: center;
    }
    .modal.show { display: flex; }
    .modal-content {
      background: #1e293b;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      width: 90%; max-width: 800px;
      max-height: 85vh;
      overflow-y: auto;
      padding: 24px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
    }
    .modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 14px; }
    .modal-close { background: none; border: none; font-size: 26px; color: var(--text-muted); cursor: pointer; }
    .modal-close:hover { color: #fff; }
    .gherkin-viewer {
      margin-top: 16px;
      background: #0b1120;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 16px;
      font-family: 'Fira Code', monospace;
      font-size: 13px;
      color: #38bdf8;
      white-space: pre-wrap;
      line-height: 1.65;
    }
  </style>
</head>
<body>

  <!-- Top Header -->
  <header>
    <div class="brand">
      <span class="brand-icon">⚡</span>
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
        <label>Active Repo:</label>
        <select id="globalRepoSelector" onchange="onGlobalRepoChange()"></select>
      </div>
    </div>
  </header>

  <!-- 2 Main Navigation Tabs -->
  <nav class="nav-tabs">
    <button class="tab-btn active" onclick="switchMainTab('indexerTab', this)">🗂️ Indexer</button>
    <button class="tab-btn" onclick="switchMainTab('ragBotTab', this)">💬 RAG Bot</button>
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
              <h3>📦 Automation Repositories</h3>
              <button class="btn btn-secondary" onclick="toggleAddRepoForm()">+ New Repo</button>
            </div>

            <!-- Add Repo Form -->
            <div id="addRepoForm" style="display: none; background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b;">
              <h4 style="font-size: 13px; margin-bottom: 10px; color: #fff;">Register New Automation Repository</h4>
              <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px;">
                <input type="text" id="newRepoName" placeholder="Repository Name (e.g. E-Commerce Core)" />
                <input type="text" id="newRepoId" placeholder="Unique Repo ID (e.g. ecommerce_core)" />
              </div>
              <div style="display: flex; justify-content: flex-end; gap: 8px;">
                <button class="btn btn-secondary" onclick="toggleAddRepoForm()">Cancel</button>
                <button class="btn" onclick="submitNewRepo()">Save Repository</button>
              </div>
            </div>

            <div id="reposList"></div>
          </div>

          <!-- Sub-Section B: Indexing Folders (Multi-Folder per Repo) -->
          <div class="panel-card">
            <div class="panel-header">
              <h3>📂 Indexing Folders (<span id="selectedRepoTitle">Selected Repo</span>)</h3>
              <button class="btn btn-secondary" onclick="reindexCurrentRepo()">🔄 Re-index All Folders</button>
            </div>
            
            <p style="font-size: 12.5px; color: var(--text-muted);">
              Add multiple feature directories for this repository. The in-process watchdog will monitor all registered folders in real-time.
            </p>

            <div style="display: flex; gap: 8px;">
              <input type="text" id="newFolderPath" style="flex: 1;" placeholder="Enter absolute or relative directory path (e.g. sample_data/feature_repos)" />
              <button class="btn" onclick="submitAddFolder()">+ Add Folder & Watch</button>
            </div>

            <div id="foldersList" style="margin-top: 4px;"></div>
          </div>

        </div>

        <!-- Right Column: Live Watchdog Activity & Telemetry -->
        <div class="panel-card">
          <div class="panel-header">
            <h3>⚡ Live Watchdog & Re-indexing Activity</h3>
            <span class="badge badge-live" id="watchdogLiveCount">Watching 0 Folders</span>
          </div>

          <p style="font-size: 12.5px; color: var(--text-muted);">
            Changes to <code>.feature</code> files in any registered folder are automatically detected and incrementally re-indexed.
          </p>

          <div class="activity-feed" id="activityFeed">
            <div class="event-entry">⚡ In-process watchdog engine running. Real-time file change logs will appear here.</div>
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
            <h3 style="font-size: 14px; font-weight: 700; color: #fff;">💬 Chat Sessions</h3>
            <div style="display: flex; gap: 4px;">
              <button class="btn btn-danger" style="padding: 3px 7px; font-size: 11px;" onclick="clearAllChatSessions()">Clear</button>
              <button class="btn" style="padding: 3px 8px; font-size: 11px;" onclick="createNewChatSession()">+ New</button>
            </div>
          </div>
          <div class="session-list" id="chatSessionsList"></div>
        </div>

        <!-- Center: Chat Window -->
        <div class="chat-window">
          <!-- Live Indexing Banner -->
          <div id="indexingBanner" style="display: none; background: rgba(234, 179, 8, 0.12); border-bottom: 1px solid rgba(234, 179, 8, 0.3); padding: 10px 20px; font-size: 13px; color: #fde047; align-items: center; justify-content: space-between; flex-shrink: 0;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span class="watchdog-pulse" style="background: #fde047; box-shadow: 0 0 8px #fde047;"></span>
              <span id="indexingBannerText">⚡ Indexing in progress... Analysis will be available as soon as indexing completes.</span>
            </div>
            <div style="width: 140px; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden;">
              <div id="indexingProgressBar" style="width: 50%; height: 100%; background: #fde047; transition: width 0.3s;"></div>
            </div>
          </div>

          <div class="chat-messages" id="chatMessages">
            <div class="chat-bubble assistant">
              👋 Welcome to RAG Bot! Ask any question regarding Gherkin scenario coverage, acceptance criteria, or test steps scoped to your selected repository.
            </div>
          </div>
          <div class="chat-input-container">
            <input type="text" id="chatInput" placeholder="Ask about requirement coverage, login scenarios, checkout tests..." onkeydown="if(event.key==='Enter') sendChatMessage()" />
            <button class="btn" id="chatSendBtn" onclick="sendChatMessage()">Send</button>
          </div>
        </div>

        <!-- Right: Scenario Citations Sidebar -->
        <div class="citations-col">
          <h3 style="font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 6px;">🎯 Scenario Citations</h3>
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
        <h3 id="modalTitle" style="font-size: 16px; color: #fff;">🎯 Scenario Details</h3>
        <button class="modal-close" onclick="closeScenarioModal()">&times;</button>
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
          <span style="font-size: 20px;">📊</span>
          <div>
            <h3 id="reportModalTitle" style="font-size: 16px; color: #fff;">Coverage Assessment & Gap Analysis</h3>
            <div id="reportModalSubtitle" style="font-size: 12px; color: var(--text-muted); margin-top: 2px;"></div>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
          <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11.5px;" onclick="copyReportModalContent()">📋 Copy Report</button>
          <button class="modal-close" onclick="closeReportModal()">&times;</button>
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
            <button class="code-copy-btn" onclick="copyCode(this)">📋 Copy Gherkin</button>
            <div class="gherkin-viewer">${escapeHtml(item.code)}</div>
          </div>
        `;
      });

      // Headers
      out = out.replace(/^### (.*?)$/gm, '<h3 style="font-size: 15px; font-weight: 700; color: #38bdf8; margin: 14px 0 8px 0;">$1</h3>');
      out = out.replace(/^## (.*?)$/gm, '<h2 style="font-size: 16px; font-weight: 800; color: #fff; margin: 16px 0 10px 0;">$1</h2>');
      out = out.replace(/^# (.*?)$/gm, '<h1 style="font-size: 18px; font-weight: 800; color: #fff; margin: 18px 0 12px 0;">$1</h1>');

      // Horizontal rules
      out = out.replace(/^---$/gm, '<hr style="border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 14px 0;" />');

      // Bold & Italic & Inline code
      out = out.replace(/\*\*(.*?)\*\*/g, '<strong style="color: #fff;">$1</strong>');
      out = out.replace(/\*(.*?)\*/g, '<em style="color: #e2e8f0;">$1</em>');
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

    function switchMainTab(tabId, btn) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      btn.classList.add('active');
      if (tabId === 'indexerTab') loadIndexerData();
      if (tabId === 'ragBotTab') loadChatSessions();
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
            <h4>${r.repo_name} ${isSelected ? '<span class="badge badge-live" style="margin-left: 6px;">Active</span>' : ''}</h4>
            <div class="meta">ID: <code>${r.repo_id}</code> | Scenarios: <b>${r.scenario_count || 0}</b> | Corpus: <b>v${r.corpus_version || 1}</b></div>
          </div>
          <div style="display: flex; gap: 6px;" onclick="event.stopPropagation()">
            <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="reindexRepo('${r.repo_id}')">🔄 Re-index</button>
            <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="deleteRepo('${r.repo_id}')">🗑️</button>
          </div>
        `;
        list.appendChild(card);
      });

      document.getElementById('selectedRepoTitle').textContent = activeRepoId;
      loadFoldersForActiveRepo();
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
    }

    async function submitNewRepo() {
      const name = document.getElementById('newRepoName').value.trim();
      const id = document.getElementById('newRepoId').value.trim();
      if (!name) { alert('Please enter repository name'); return; }

      const res = await fetch('/api/repos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_name: name, repo_id: id || undefined })
      });
      if (res.ok) {
        toggleAddRepoForm();
        document.getElementById('newRepoName').value = '';
        document.getElementById('newRepoId').value = '';
        await loadRepos();
      } else {
        const err = await res.json();
        alert('Error creating repo: ' + (err.detail || err));
      }
    }

    async function deleteRepo(repoId) {
      if (!confirm(`Delete repository '${repoId}' and all its registered folders?`)) return;
      await fetch(`/api/repos/${encodeURIComponent(repoId)}`, { method: 'DELETE' });
      await loadRepos();
    }

    async function reindexRepo(repoId) {
      const res = await fetch(`/api/repos/${encodeURIComponent(repoId)}/reindex`, { method: 'POST' });
      const data = await res.json();
      alert(`Re-indexed ${data.scenarios_indexed} scenarios for repository '${repoId}'!`);
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
            <div class="folder-path">${f.folder_path}</div>
            <div style="font-size: 11.5px; color: var(--text-muted); margin-top: 4px; display: flex; align-items: center; gap: 8px;">
              <span>Scenarios: <b>${f.scenario_count}</b></span>
              <span class="badge badge-live">● Watching (Live)</span>
            </div>
          </div>
          <div style="display: flex; gap: 6px;">
            <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="reindexFolder('${f.folder_id}')">🔄</button>
            <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="removeFolder('${f.folder_id}')">🗑️</button>
          </div>
        `;
        list.appendChild(div);
      });
    }

    async function submitAddFolder() {
      const input = document.getElementById('newFolderPath');
      const pathVal = input.value.trim();
      if (!pathVal) { alert('Please enter a folder path'); return; }

      const res = await fetch(`/api/repos/${encodeURIComponent(activeRepoId)}/folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: pathVal })
      });
      if (res.ok) {
        input.value = '';
        await loadRepos();
        await loadFoldersForActiveRepo();
        pollWatchdogStatus();
      } else {
        const err = await res.json();
        alert('Error adding folder: ' + (err.detail || err));
      }
    }

    async function removeFolder(folderId) {
      if (!confirm('Remove this folder from indexing?')) return;
      await fetch(`/api/folders/${encodeURIComponent(folderId)}`, { method: 'DELETE' });
      await loadRepos();
      await loadFoldersForActiveRepo();
    }

    async function reindexFolder(folderId) {
      const res = await fetch(`/api/folders/${encodeURIComponent(folderId)}/reindex`, { method: 'POST' });
      const data = await res.json();
      alert(`Re-indexed ${data.scenarios_indexed} scenarios for this folder!`);
      loadRepos();
    }

    // ==================== LIVE WATCHDOG TELEMETRY ====================

    async function pollWatchdogStatus() {
      try {
        const res = await fetch('/api/watcher/status');
        const data = await res.json();
        const w = data.watcher;

        document.getElementById('watchdogLiveCount').textContent = `Watching ${w.watched_count} Folders`;
        document.getElementById('watchdogStatusText').textContent = `Watchdog: Monitoring ${w.watched_count} Folder(s)`;

        const feed = document.getElementById('activityFeed');
        if (w.recent_events && w.recent_events.length > 0) {
          feed.innerHTML = w.recent_events.map(e => `
            <div class="event-entry ${e.event_type}">
              <b>[${e.time}]</b> ⚡ File <b>${e.event_type}</b>: <code>${e.file_name}</code> (Repo: <b>${e.repo_id}</b>, ${e.scenarios_count} scenarios re-indexed)
            </div>
          `).join('');
        }
      } catch (err) {}
    }

    function loadIndexerData() {
      loadRepos();
      pollWatchdogStatus();
    }

    // ==================== RAG BOT CHAT ====================

    async function loadChatSessions() {
      const res = await fetch('/api/chat-sessions?repo_id=' + encodeURIComponent(activeRepoId));
      const data = await res.json();
      const list = document.getElementById('chatSessionsList');
      list.innerHTML = '';
      const sessions = data.chat_sessions || [];

      if (sessions.length === 0) {
        list.innerHTML = '<p style="color: var(--text-muted); font-size: 12px; padding: 10px;">No sessions for this repo.</p>';
        if (!activeChatId) createNewChatSession();
        return;
      }

      sessions.forEach(s => {
        const div = document.createElement('div');
        div.className = 'session-item' + (s.chat_id === activeChatId ? ' active' : '');
        div.onclick = () => selectChatSession(s.chat_id);
        div.innerHTML = `
          <div style="overflow: hidden; flex: 1;">
            <div class="session-title">${s.title || 'Conversation'}</div>
            <div class="session-time">${String(s.updated_at || s.created_at).slice(0, 16)}</div>
          </div>
          <button class="btn btn-danger" style="padding: 2px 6px; font-size: 10px;" onclick="event.stopPropagation(); deleteChatSession('${s.chat_id}')">🗑️</button>
        `;
        list.appendChild(div);
      });

      if (!activeChatId && sessions.length > 0) {
        selectChatSession(sessions[0].chat_id);
      }
    }

    async function createNewChatSession() {
      const res = await fetch('/api/chat-sessions/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_id: activeRepoId, title: 'New Conversation' })
      });
      const data = await res.json();
      activeChatId = data.chat_id;
      document.getElementById('chatMessages').innerHTML = `
        <div class="chat-bubble assistant">
          Started new chat session (<code>${activeChatId}</code>) scoped to repository <b>${activeRepoId}</b>. What would you like to verify?
        </div>
      `;
      document.getElementById('citationsList').innerHTML = '';
      loadChatSessions();
    }

    async function selectChatSession(chatId) {
      activeChatId = chatId;
      loadChatSessions();
      const res = await fetch('/api/chat-history/' + encodeURIComponent(chatId));
      const data = await res.json();
      const box = document.getElementById('chatMessages');
      box.innerHTML = '';

      if (!data.messages || data.messages.length === 0) {
        box.innerHTML = '<div class="chat-bubble assistant">Empty conversation. Type your question below!</div>';
        return;
      }

      let lastCitations = [];
      data.messages.forEach(m => {
        if (m.role === 'user') {
          addUserMessage(m.content);
        } else {
          addAssistantMessage(m.content, m.citations);
          if (m.citations && m.citations.length > 0) lastCitations = m.citations;
        }
      });
      renderCitationsSidebar(lastCitations);
    }

    async function deleteChatSession(chatId) {
      await fetch('/api/chat-sessions/' + encodeURIComponent(chatId), { method: 'DELETE' });
      if (activeChatId === chatId) activeChatId = null;
      loadChatSessions();
    }

    async function clearAllChatSessions() {
      if (!confirm('Clear all chat sessions for this repository?')) return;
      await fetch('/api/chat-sessions/clear?repo_id=' + encodeURIComponent(activeRepoId), { method: 'DELETE' });
      activeChatId = null;
      createNewChatSession();
    }

    async function sendChatMessage() {
      const input = document.getElementById('chatInput');
      const msg = input.value.trim();
      if (!msg) return;

      addUserMessage(msg);
      input.value = '';

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: msg, repo_id: activeRepoId, chat_id: activeChatId })
        });
        const data = await res.json();
        activeChatId = data.chat_id;
        addAssistantMessage(data.reply, data.citations, data.cached);
        renderCitationsSidebar(data.citations);
        loadChatSessions();
      } catch (err) {
        addAssistantMessage('Error communicating with backend: ' + err);
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

    function addAssistantMessage(text, citations = [], isCached = false) {
      const box = document.getElementById('chatMessages');
      const div = document.createElement('div');
      div.className = 'chat-bubble assistant';

      const reportId = 'rep_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);
      window.reportStore[reportId] = { text, citations, isCached };

      let cacheHtml = '';
      if (isCached) {
        cacheHtml = '<div style="margin-bottom: 8px;"><span class="badge" style="background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.35); font-size: 11px; padding: 2px 7px;">⚡ Semantic Cache Hit</span></div>';
      }

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

        // Build Citation pills
        let citationPillsHtml = '';
        if (citations && citations.length > 0) {
          const pills = citations.slice(0, 4).map(c => {
            const fileName = getFileName(c.file_path);
            const pct = c.match_percentage !== undefined ? c.match_percentage : Math.round((c.score || 0) * 10);
            const pillColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)');
            return `<span class="citation-pill" title="Click to view Gherkin steps" onclick="openScenarioModal('${c.scenario_id}')">🎯 <strong>${escapeHtml(c.scenario_name)}</strong> <span style="opacity: 0.8">(${fileName}:${c.line_number})</span> <span class="badge" style="background: rgba(255,255,255,0.08); color: ${pillColor}; font-size: 10px; padding: 1px 5px; margin-left: 4px;">${pct}%</span></span>`;
          }).join('');
          citationPillsHtml = `<div style="margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px;">${pills}</div>`;
        }

        div.innerHTML = `
          ${cacheHtml}
          <div class="eval-summary-card">
            <div class="eval-header">
              <div class="eval-title">📊 Coverage Assessment</div>
              <span class="badge" style="background: rgba(255,255,255,0.08); color: ${badgeColor}; font-size: 12px; padding: 3px 9px;">${escapeHtml(statusText)}</span>
            </div>
            <div class="eval-summary-text">${formatMarkdown(summaryText)}</div>
            ${citationPillsHtml}
            <div style="margin-top: 14px; display: flex; justify-content: flex-end;">
              <button class="btn-report" onclick="openReportModal('${reportId}')">
                🔍 View Full Details & Generated Scenarios &rarr;
              </button>
            </div>
          </div>
        `;
      } else {
        // Standard conversational chat bubble
        div.innerHTML = cacheHtml + formatMarkdown(text);
        if (citations && citations.length > 0) {
          const p = document.createElement('div');
          p.style.marginTop = '12px';
          p.style.display = 'flex';
          p.style.flexWrap = 'wrap';
          p.style.gap = '6px';
          citations.slice(0, 4).forEach(c => {
            const pill = document.createElement('span');
            pill.className = 'citation-pill';
            const fileName = getFileName(c.file_path);
            const pct = c.match_percentage !== undefined ? c.match_percentage : 0;
            const badgeColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)');
            pill.innerHTML = `🎯 <strong>${c.scenario_name}</strong> <span style="opacity: 0.8">(${fileName}:${c.line_number})</span> <span class="badge" style="background: rgba(255,255,255,0.08); color: ${badgeColor}; font-size: 10px; margin-left: 4px;">${pct}%</span>`;
            pill.onclick = () => openScenarioModal(c.scenario_id);
            p.appendChild(pill);
          });
          div.appendChild(p);
        }
      }

      box.appendChild(div);
      box.scrollTop = box.scrollHeight;
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
        const badgeColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--yellow)' : 'var(--accent)');
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 6px;">
            <div class="title" style="flex: 1;">🎯 ${c.scenario_name}</div>
            <span class="badge" style="background: rgba(255,255,255,0.06); color: ${badgeColor}; font-size: 11px;">${pct}%</span>
          </div>
          <div class="meta" style="margin-top: 6px;">Feature: ${c.feature_title}</div>
          <div class="meta" style="color: var(--accent);">${fileName} : Line ${c.line_number}</div>
        `;
        list.appendChild(card);
      });
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
        document.getElementById('modalTitle').textContent = `🎯 ${sc.scenario_name}`;
        const fileName = getFileName(sc.file_path);
        const featName = sc.feature_name || sc.feature_title || 'Feature';
        document.getElementById('modalMeta').innerHTML = `
          <div><strong>Feature:</strong> ${featName}</div>
          <div style="margin-top: 4px;"><strong>Location:</strong> <code style="color: var(--accent);">${fileName} (Line ${sc.line_number})</code></div>
          <div style="margin-top: 4px; font-size: 12px; opacity: 0.8;">${sc.file_path}</div>
          ${sc.tags && sc.tags.length ? `<div style="margin-top: 6px;">${sc.tags.map(t => `<span class="badge" style="background: rgba(56,189,248,0.2); color: var(--accent); font-size: 11px; margin-right: 4px;">${t}</span>`).join('')}</div>` : ''}
        `;
        document.getElementById('modalGherkin').textContent = sc.raw_gherkin || sc.canonical_text;
        document.getElementById('scenarioModal').classList.add('show');
      } catch (e) {
        alert('Could not load scenario details: ' + e.message);
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
    }

    function closeReportModal() {
      document.getElementById('reportModal').classList.remove('show');
      window.activeReportId = null;
    }

    function copyReportModalContent() {
      if (!window.activeReportId || !window.reportStore[window.activeReportId]) return;
      const text = window.reportStore[window.activeReportId].text;
      navigator.clipboard.writeText(text).then(() => {
        alert('Full evaluation report copied to clipboard!');
      });
    }

    function copyCode(btn) {
      const wrapper = btn.closest('.code-block-wrapper');
      const code = wrapper.querySelector('.gherkin-viewer').textContent;
      navigator.clipboard.writeText(code).then(() => {
        const oldText = btn.textContent;
        btn.textContent = '✅ Copied!';
        setTimeout(() => { btn.textContent = oldText; }, 2000);
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
          bannerText.textContent = `⚡ Indexing in progress: ${st.current_indexing_file || 'Processing'} (${st.indexing_progress_pct}%). Analysis will unlock when completed.`;
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
          statusText.textContent = `● Up to date (${st.scenario_count} scenarios, v${st.corpus_version})`;

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
      loadRepos();
      pollWatchdogStatus();
      pollRepoStatus();
    };
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTML_DASHBOARD_TEMPLATE
