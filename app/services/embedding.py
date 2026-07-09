from __future__ import annotations

import hashlib
import math
from typing import Protocol

import httpx

from app.core.config import Settings


class EmbeddingBackend(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalEmbeddingService:
    """Deterministic fallback embeddings for offline development and tests."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token for token in text.lower().replace("\n", " ").split(" ") if token]
        for token in tokens or [text[:32]]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [round(value / norm, 6) for value in vector]


class OpenAICompatibleEmbeddingService:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.embedding_model
        self.timeout = settings.llm_timeout_seconds

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is required when EMBEDDING_PROVIDER=openai-compatible")
        response = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data or not isinstance(data[0].get("embedding"), list):
            raise RuntimeError("Embedding API did not return data[0].embedding")
        return [float(value) for value in data[0]["embedding"]]


def create_embedding_service(settings: Settings) -> EmbeddingBackend:
    if settings.embedding_provider == "openai-compatible":
        return OpenAICompatibleEmbeddingService(settings)
    return LocalEmbeddingService(dimensions=settings.local_embedding_dimensions)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[i] * right[i] for i in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size])) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right[:size])) or 1.0
    return dot / (left_norm * right_norm)
