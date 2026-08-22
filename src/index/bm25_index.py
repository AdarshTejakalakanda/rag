"""Sparse BM25 Index for lexical retrieval of Gherkin scenarios with multi-repo support."""

import re
import math
from typing import List, Tuple, Dict, Optional
from collections import Counter
from src.parsers.gherkin_parser import ScenarioChunk


class BM25Index:
    """BM25 Okapi lexical index with positive smoothed IDF for scenario retrieval."""

    STOPWORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he",
        "in", "is", "it", "its", "of", "on", "that", "the", "to", "was", "were", "will", "with"
    }

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.scenarios: List[ScenarioChunk] = []
        self.doc_len: List[int] = []
        self.avgdl: float = 0.0
        self.doc_freqs: List[Counter] = []
        self.idf: Dict[str, float] = {}

    @classmethod
    def tokenize(cls, text: str) -> List[str]:
        """Tokenize and normalize text."""
        # Convert camelCase / snake_case into separate words
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = text.replace("_", " ").replace("-", " ")
        words = re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())
        return [w for w in words if len(w) > 1 and w not in cls.STOPWORDS]

    def index_scenarios(self, scenarios: List[ScenarioChunk]) -> None:
        """Builds BM25 index from a list of ScenarioChunk objects."""
        self.scenarios = list(scenarios)
        if not self.scenarios:
            self.doc_len = []
            self.avgdl = 0.0
            self.doc_freqs = []
            self.idf = {}
            return

        corpus = [self.tokenize(s.full_text) for s in self.scenarios]
        self.doc_len = [len(doc) for doc in corpus]
        total_len = sum(self.doc_len)
        self.avgdl = (total_len / len(corpus)) if corpus else 1.0

        self.doc_freqs = [Counter(doc) for doc in corpus]
        df: Dict[str, int] = {}
        for doc in corpus:
            for word in set(doc):
                df[word] = df.get(word, 0) + 1

        n_docs = len(corpus)
        self.idf = {}
        for word, freq in df.items():
            # Lucene-style positive smoothed IDF
            self.idf[word] = math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))

    def search(self, query: str, top_k: int = 30, repo_id: Optional[str] = None) -> List[Tuple[ScenarioChunk, float, int]]:
        """
        Searches index for matching scenarios, optionally filtered by repo_id.
        Returns list of (ScenarioChunk, score, rank) tuples, 1-indexed rank.
        """
        if not self.scenarios:
            return []

        tokens = self.tokenize(query)
        if not tokens:
            return []

        scored_pairs = []
        for idx, (scenario, doc_freq) in enumerate(zip(self.scenarios, self.doc_freqs)):
            if repo_id and scenario.repo_id != repo_id:
                continue

            score = 0.0
            d_len = self.doc_len[idx]
            for token in tokens:
                if token in doc_freq:
                    f = doc_freq[token]
                    idf = self.idf.get(token, 0.0)
                    denom = f + self.k1 * (1.0 - self.b + self.b * (d_len / (self.avgdl or 1.0)))
                    score += idf * ((f * (self.k1 + 1.0)) / (denom or 1.0))
            scored_pairs.append((scenario, score))

        scored_pairs.sort(key=lambda x: x[1], reverse=True)

        results: List[Tuple[ScenarioChunk, float, int]] = []
        for rank, (scenario, score) in enumerate(scored_pairs[:top_k], start=1):
            results.append((scenario, float(score), rank))

        return results

    def remove_by_file(self, file_path: str) -> None:
        """Removes scenarios belonging to a specific file and rebuilds index."""
        self.scenarios = [s for s in self.scenarios if s.file_path != file_path]
        self.index_scenarios(self.scenarios)
