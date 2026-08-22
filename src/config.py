"""Configuration management for the Local Agentic RAG Test Coverage Analyzer.

Supports YAML configuration and environment variable overrides via .env.
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
import os
import yaml
import logging
import warnings
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Suppress Hugging Face Hub unauthenticated requests and warnings
warnings.filterwarnings("ignore", message=".*unauthenticated requests to the HF Hub.*")
warnings.filterwarnings("ignore", module="huggingface_hub.*")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)

# Automatically load .env file from project root or parent directories
load_dotenv()


class RepoEntryConfig(BaseModel):
    id: str
    name: str
    path: Path
    branch: str = "main"


class PathsConfig(BaseModel):
    business_docs_dir: Path = Field(default=Path("data/business_docs"))
    feature_repos_dir: Path = Field(default=Path("data/feature_repos"))
    reports_dir: Path = Field(default=Path("reports"))
    milvus_db_path: Path = Field(default=Path("data/milvus_rag.db"))
    cache_dir: Path = Field(default=Path(".cache"))


class ModelsConfig(BaseModel):
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dimension: int = 384
    embedding_device: str = "cpu"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_device: str = "cpu"


class RetrievalConfig(BaseModel):
    bm25_top_k: int = 20
    dense_top_k: int = 20
    rrf_k: int = 60
    rrf_top_k: int = 20
    reranker_top_k: int = 10


class BM25Config(BaseModel):
    k1: float = 1.5
    b: float = 0.75


class GeminiConfig(BaseModel):
    model: str = "gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"


class OpenAIConfig(BaseModel):
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "llama3.2"


class JudgeConfig(BaseModel):
    provider: str = "gemini"  # 'gemini', 'openai', 'ollama', 'anthropic', 'mock'
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    temperature: float = 0.1
    max_retries: int = 3


class MilvusConfig(BaseModel):
    mode: str = "lite"  # 'lite' or 'standalone'
    host: str = "127.0.0.1"
    port: int = 19530
    collection_name: str = "gherkin_scenarios"


class WatcherConfig(BaseModel):
    debounce_seconds: float = 1.5
    file_patterns: List[str] = Field(default_factory=lambda: ["*.feature"])


class AppConfig(BaseModel):
    repositories: List[RepoEntryConfig] = Field(default_factory=list)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    bm25: BM25Config = Field(default_factory=BM25Config)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    milvus: MilvusConfig = Field(default_factory=MilvusConfig)
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)


def load_config(config_path: Optional[str or Path] = None) -> AppConfig:
    """Loads configuration from YAML file and applies .env environment variable overrides."""
    load_dotenv(override=True)

    if config_path is None:
        default_paths = [
            Path("configs/config.yaml"),
            Path("config.yaml"),
            Path(__file__).parent.parent / "configs" / "config.yaml",
        ]
        for p in default_paths:
            if p.exists():
                config_path = p
                break

    data = {}
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    config = AppConfig(**data)

    # .env Overrides
    if os.getenv("LLM_PROVIDER"):
        config.judge.provider = os.getenv("LLM_PROVIDER").lower()

    if os.getenv("GEMINI_MODEL"):
        config.judge.gemini.model = os.getenv("GEMINI_MODEL")

    if os.getenv("OPENAI_MODEL"):
        config.judge.openai.model = os.getenv("OPENAI_MODEL")

    if os.getenv("OPENAI_BASE_URL"):
        config.judge.openai.base_url = os.getenv("OPENAI_BASE_URL")

    if os.getenv("OLLAMA_BASE_URL"):
        config.judge.ollama.base_url = os.getenv("OLLAMA_BASE_URL")

    if os.getenv("OLLAMA_MODEL"):
        config.judge.ollama.model = os.getenv("OLLAMA_MODEL")

    if os.getenv("EMBEDDING_DEVICE"):
        config.models.embedding_device = os.getenv("EMBEDDING_DEVICE")

    if os.getenv("RERANKER_DEVICE"):
        config.models.reranker_device = os.getenv("RERANKER_DEVICE")

    return config
