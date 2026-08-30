"""Tests for FastAPI Web Application & Endpoints."""

import pytest
from fastapi.testclient import TestClient
from src.web.app import app


def test_api_endpoints():
    client = TestClient(app)

    # 1. Test GET / (HTML Dashboard)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Local RAG BDD Automation Agent" in resp.text
    assert "Indexer" in resp.text
    assert "RAG Bot" in resp.text

    # 2. Test GET /api/repos
    resp_repos = client.get("/api/repos")
    assert resp_repos.status_code == 200
    data = resp_repos.json()
    assert "repositories" in data

    # 3. Test GET /api/watcher/status
    resp_watch = client.get("/api/watcher/status")
    assert resp_watch.status_code == 200
    w_data = resp_watch.json()
    assert "watcher" in w_data
    assert "running" in w_data["watcher"]

    # 4. Test Multi-Folder management
    resp_folders = client.get("/api/repos/repo_1/folders")
    assert resp_folders.status_code == 200

    # 5. Test POST /api/chat
    chat_payload = {
        "message": "User login authentication tests",
        "repo_id": "repo_1"
    }
    resp_chat = client.post("/api/chat", json=chat_payload)
    assert resp_chat.status_code == 200
    chat_data = resp_chat.json()
    assert "reply" in chat_data
    assert "chat_id" in chat_data
    citations = chat_data.get("citations", [])

    # 6. Test GET /api/scenario/{scenario_id}
    if citations:
        sc_id = citations[0]["scenario_id"]
        resp_sc = client.get(f"/api/scenario/{sc_id}")
        assert resp_sc.status_code == 200
        sc_data = resp_sc.json()
        assert "scenario" in sc_data
        assert sc_data["scenario"]["scenario_id"] == sc_id
