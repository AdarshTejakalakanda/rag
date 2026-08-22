"""Multi-Repository Manager for managing feature repositories and scopes."""

from pathlib import Path
from typing import List, Optional, Dict, Any
from src.storage.state_db import StateDatabase
from src.parsers.gherkin_parser import GherkinParser, ScenarioChunk


class RepositoryManager:
    """Manages multi-repository registry, scans, and indexing scopes."""

    def __init__(self, state_db: StateDatabase):
        self.state_db = state_db

    def add_repository(
        self,
        repo_name: str,
        repo_path: str or Path = "",
        repo_id: Optional[str] = None,
        branch: str = "main",
    ) -> dict:
        """Registers a local repository."""
        p_str = str(Path(repo_path).resolve()) if repo_path else ""
        repo_info = self.state_db.register_repo(
            repo_name=repo_name,
            repo_path=p_str,
            repo_id=repo_id,
            branch=branch,
        )
        # If a valid repo_path directory was provided, also register it in repo_folders
        if p_str and Path(p_str).exists() and Path(p_str).is_dir():
            self.state_db.add_repo_folder(repo_id=repo_info["repo_id"], folder_path=p_str)
        return repo_info

    def list_repositories(self) -> List[dict]:
        """Lists all registered repositories."""
        return self.state_db.list_repos()

    def get_repository(self, repo_id: str) -> Optional[dict]:
        """Gets repository metadata."""
        return self.state_db.get_repo(repo_id)

    def delete_repository(self, repo_id: str):
        """Removes repository and its folders from registry."""
        self.state_db.delete_repo(repo_id)

    def add_folder_to_repo(self, repo_id: str, folder_path: str or Path) -> dict:
        """Adds a feature folder path to a repository."""
        repo = self.get_repository(repo_id)
        if not repo:
            raise ValueError(f"Repository '{repo_id}' not found.")
        p = Path(folder_path).resolve()
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(f"Folder directory does not exist: {p}")
        return self.state_db.add_repo_folder(repo_id=repo_id, folder_path=str(p))

    def list_folders_for_repo(self, repo_id: Optional[str] = None) -> List[dict]:
        """Lists all configured indexing folders for a repository."""
        return self.state_db.list_repo_folders(repo_id=repo_id)

    def remove_folder(self, folder_id: str) -> Optional[dict]:
        """Removes a folder from indexing registry."""
        return self.state_db.delete_repo_folder(folder_id)

    def scan_repository_scenarios(self, repo_id: str) -> List[ScenarioChunk]:
        """Parses all Gherkin scenarios belonging to the specified repository across all configured folders."""
        repo = self.get_repository(repo_id)
        if not repo:
            raise ValueError(f"Repository '{repo_id}' not found.")

        folders = self.state_db.list_repo_folders(repo_id)
        scenarios_by_id = {}

        # Scan each configured folder
        for f in folders:
            f_path = Path(f["folder_path"])
            if f_path.exists() and f_path.is_dir():
                f_scenarios = GherkinParser.parse_directory(f_path, repo_id=repo_id)
                self.state_db.update_folder_scenario_count(f["folder_id"], len(f_scenarios))
                for sc in f_scenarios:
                    scenarios_by_id[sc.scenario_id] = sc

        # Fallback to repo_path if no folders registered
        if not scenarios_by_id and repo.get("repo_path"):
            r_path = Path(repo["repo_path"])
            if r_path.exists() and r_path.is_dir():
                f_scenarios = GherkinParser.parse_directory(r_path, repo_id=repo_id)
                for sc in f_scenarios:
                    scenarios_by_id[sc.scenario_id] = sc

        return list(scenarios_by_id.values())
