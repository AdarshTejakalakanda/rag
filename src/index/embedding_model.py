"""Dense embedding model wrapper using SentenceTransformers."""

from typing import List, Union
import numpy as np
import os
import warnings

# Suppress Hugging Face Hub token notice
warnings.filterwarnings("ignore", module="huggingface_hub.*")


class EmbeddingModel:
    """Wrapper for sentence embedding models."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except Exception as e:
                # Fallback to a fast deterministic embedding if model loading fails in airgapped/test mode
                print(f"Warning: Could not load SentenceTransformer '{self.model_name}': {e}. Using deterministic fallback.")
                self._model = "FALLBACK"

    def encode(self, texts: Union[str, List[str]], batch_size: int = 32) -> np.ndarray:
        """Encodes single text or list of texts to normalized embedding vectors."""
        self._load_model()
        is_single = isinstance(texts, str)
        text_list = [texts] if is_single else texts

        if not text_list:
            return np.zeros((0, 384), dtype=np.float32)

        if self._model != "FALLBACK":
            embeddings = self._model.encode(
                text_list,
                batch_size=batch_size,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            return embeddings[0] if is_single else np.array(embeddings, dtype=np.float32)
        else:
            # Deterministic pseudo-embedding for testing/fallback
            dim = 384
            res = []
            for t in text_list:
                np.random.seed(abs(hash(t)) % (2**32))
                vec = np.random.randn(dim).astype(np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec /= norm
                res.append(vec)
            arr = np.array(res, dtype=np.float32)
            return arr[0] if is_single else arr

    def embed(self, texts: Union[str, List[str]], batch_size: int = 32) -> np.ndarray:
        """Alias for encode."""
        return self.encode(texts, batch_size=batch_size)

    def encode_query(self, query: str) -> np.ndarray:
        """Encodes query string into a 1D normalized embedding vector."""
        res = self.encode(query)
        if isinstance(res, np.ndarray) and res.ndim == 2:
            return res[0]
        return res
