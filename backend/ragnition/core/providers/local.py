"""Local ONNX models via fastembed: embeddings and cross-encoder reranking on CPU.

Two concurrency rules matter here:

1. Model inference is CPU-bound, synchronous code. Calling it directly inside an
   `async def` would freeze the event loop: every other request in the server
   would stall until it finished. `asyncio.to_thread` runs it in a worker thread
   while the event loop keeps serving.
2. Running many inferences in parallel does not make a CPU faster; it just makes
   threads fight over cores (ONNX Runtime already uses several threads per call).
   A semaphore lets one inference run at a time.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from ragnition.core.providers.types import ProviderError

logger = logging.getLogger(__name__)

Task = Literal["embedding", "rerank"]
Loader = Callable[[str, str], Any]  # (model_name, cache_dir) -> model object

_system_certs_injected = False


def _inject_system_certs_once() -> None:
    """Make ALL of Python's TLS trust the OS certificate store.

    Model downloads happen inside fastembed / huggingface_hub, which create their own
    HTTP clients; we cannot hand them an SSLContext like we do for our own httpx
    client. So, only when USE_SYSTEM_CERTS=true, we patch the ssl module globally.
    """
    global _system_certs_injected
    if not _system_certs_injected:
        import truststore

        truststore.inject_into_ssl()
        _system_certs_injected = True


def _load_text_embedding(model_name: str, cache_dir: str) -> Any:
    from fastembed import TextEmbedding  # lazy: heavy import, only when actually used

    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


def _load_cross_encoder(model_name: str, cache_dir: str) -> Any:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)


def _as_floats(vector: Any) -> list[float]:
    # fastembed yields numpy arrays; .tolist() converts to plain Python floats
    return vector.tolist() if hasattr(vector, "tolist") else [float(x) for x in vector]


class LocalModels:
    """Loads each local model once (lazily) and runs inference off the event loop."""

    def __init__(
        self,
        cache_dir: Path,
        use_system_certs: bool = False,
        *,
        loaders: dict[Task, Loader] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.use_system_certs = use_system_certs
        self._loaders: dict[Task, Loader] = loaders or {
            "embedding": _load_text_embedding,
            "rerank": _load_cross_encoder,
        }
        self._instances: dict[tuple[Task, str], Any] = {}
        # threading.Lock (not asyncio.Lock): loading happens inside worker threads.
        self._load_lock = threading.Lock()
        self._inference_slot = asyncio.Semaphore(1)

    def _get(self, task: Task, model_name: str) -> Any:
        key = (task, model_name)
        with self._load_lock:  # two first-calls at once must not download twice
            if key not in self._instances:
                if self.use_system_certs:
                    _inject_system_certs_once()
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info(
                    "loading local model (first use downloads it)",
                    extra={"task": task, "model": model_name, "cache_dir": str(self.cache_dir)},
                )
                self._instances[key] = self._loaders[task](model_name, str(self.cache_dir))
            return self._instances[key]

    async def _run(self, work: Callable[[], Any]) -> Any:
        async with self._inference_slot:
            try:
                return await asyncio.to_thread(work)
            except ProviderError:
                raise
            except Exception as exc:  # library errors become our typed error
                raise ProviderError(
                    f"local model failed: {type(exc).__name__}: {exc}", kind="bad_response", retryable=False
                ) from exc

    async def embed(
        self, model_name: str, texts: list[str], input_type: Literal["query", "document"], batch_size: int
    ) -> list[list[float]]:
        def work() -> list[list[float]]:
            model = self._get("embedding", model_name)
            # Separate entry points: retrieval models may treat queries and passages
            # differently (e.g. BGE's query instruction prefix).
            vectors: Iterable[Any] = (
                model.query_embed(texts) if input_type == "query" else model.passage_embed(texts, batch_size=batch_size)
            )
            return [_as_floats(v) for v in vectors]

        return await self._run(work)

    async def rerank(self, model_name: str, query: str, documents: list[str]) -> list[float]:
        """One relevance score per document, in the SAME order as `documents`."""

        def work() -> list[float]:
            model = self._get("rerank", model_name)
            return [float(score) for score in model.rerank(query, documents)]

        return await self._run(work)
