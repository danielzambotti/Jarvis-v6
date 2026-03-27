"""
core/memory/retriever.py — RAG Retrieval Engine

Generates embeddings via Ollama, caches in Redis, retrieves semantically
relevant memories from ChromaDB, and assembles context for prompt injection.
"""
import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_OLLAMA_HOST    = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
_EMBED_URL      = f"{_OLLAMA_HOST}/api/embed"
_EMBED_MODEL    = os.environ.get("EMBEDDING_MODEL", os.environ.get("OLLAMA_EMBED_MODEL", "mxbai-embed-large"))
_REDIS_URL      = os.environ.get("REDIS_URL", "redis://redis:6379/0")
_REDIS_EMBED_TTL = 86400   # 24h
_TOP_K          = 5
_RECENCY_WINDOW = 30 * 86400   # 30 days — after this, recency_factor → 0

# Triggers that suggest the user is referencing past memory
_MEMORY_KEYWORDS = re.compile(
    r'\b(past|previous|remember|history|before|earlier|recall|last\s+time|you\s+said|told\s+you)\b',
    re.IGNORECASE,
)

# ── Redis helper ───────────────────────────────────────────────────────────────

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as _redis_lib
        _redis_client = _redis_lib.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=3)
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.debug("[RETRIEVER] Redis unavailable for embedding cache: %s", exc)
        return None


# ── Embedding Generation ───────────────────────────────────────────────────────

def _classify_failure(exc: Exception) -> str:
    """Map an exception to a Prometheus reason label (timeout / contract / api)."""
    msg = str(exc).lower()
    cls = type(exc).__name__.lower()
    if "timeout" in cls or "timeout" in msg:
        return "timeout"
    if "contract" in msg:
        return "contract"
    return "api"


def generate_embedding(text: str) -> Optional[list]:
    """
    Generate an embedding vector for text via Ollama /api/embed (2026 API).
    Results are cached in Redis for _REDIS_EMBED_TTL seconds.
    2 attempts total with a 2s delay between them.
    Returns list[float] or None on terminal failure.
    """
    import requests as _req

    cache_key = f"embed:{hashlib.sha256(text.encode()).hexdigest()[:16]}"

    # Cache read
    r = _get_redis()
    if r is not None:
        try:
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    vector = None
    for attempt in range(2):
        start = time.time()
        try:
            resp = _req.post(
                _EMBED_URL,
                json={"model": _EMBED_MODEL, "input": text},
                timeout=30,
            )
            resp.raise_for_status()

            # Defensive parsing: /api/embed → "embeddings"[0]; legacy → "embedding"
            body = resp.json()
            raw = body.get("embedding")
            if raw is None:
                emb_list = body.get("embeddings")
                if emb_list:
                    raw = emb_list[0]
            if raw is None:
                raise RuntimeError(f"Ollama contract violation: {resp.text[:200]}")

            # Strict type validation
            if not isinstance(raw, list):
                raise RuntimeError(f"Ollama returned non-list vector: {type(raw).__name__}")
            if not raw:
                raise RuntimeError("Ollama returned empty embedding vector")
            if not all(isinstance(x, float) for x in raw):
                raise RuntimeError(
                    f"Ollama vector contains non-float values: {type(raw[0]).__name__}"
                )

            vector = raw
            duration = time.time() - start

            if _jarvis_embedding_latency_seconds is not None:
                _jarvis_embedding_latency_seconds.observe(duration)

            logger.info(
                "[RETRIEVER-TRACE] Vector generated | model=%s | dims=%d | latency=%.3fs",
                _EMBED_MODEL, len(vector), duration,
            )
            break

        except Exception as exc:
            reason = _classify_failure(exc)
            logger.warning(
                "[RETRIEVER] Embedding attempt %d/2 failed [reason=%s]: %s",
                attempt + 1, reason, exc,
            )
            if _jarvis_embedding_failures_total is not None:
                try:
                    _jarvis_embedding_failures_total.labels(reason=reason).inc()
                except Exception:
                    pass
            if attempt == 0:
                time.sleep(2)
            else:
                logger.error(
                    "[RETRIEVER-ERROR] Terminal failure in embedding pipeline",
                    exc_info=True,
                )

    if vector is None:
        return None

    # Cache write
    if r is not None:
        try:
            r.setex(cache_key, _REDIS_EMBED_TTL, json.dumps(vector))
        except Exception:
            pass

    return vector


# ── Smart Memory Trigger ───────────────────────────────────────────────────────

def should_use_memory(prompt: str) -> bool:
    """Return True if the prompt appears to reference past conversation/context."""
    return bool(_MEMORY_KEYWORDS.search(prompt))


# ── Context Assembly ───────────────────────────────────────────────────────────

def get_relevant_context(prompt: str, top_k: int = _TOP_K) -> str:
    """
    Full RAG pipeline:
      1. Generate query embedding
      2. Similarity search in ChromaDB
      3. Re-rank by composite score (similarity, importance, recency)
      4. Boost retrieved memories
      5. Assemble labeled context string

    Returns a context string to inject into the prompt, or "" on failure/no results.
    """
    from core.memory.vector_store import get_vector_store

    retrieval_start = time.time()

    embedding = generate_embedding(prompt)
    if embedding is None:
        _record_retrieval_miss()
        return ""

    vs      = get_vector_store()
    results = vs.search_memories(embedding, top_k=top_k)

    if not results:
        _record_retrieval_miss()
        return ""

    now = time.time()

    # Re-rank: score = (similarity × 0.6) + (importance × 0.3) + (recency × 0.1)
    for item in results:
        age            = now - item.get("timestamp", now)
        recency_factor = max(0.0, 1.0 - age / _RECENCY_WINDOW)
        item["_rank"]  = (
            item["similarity"]       * 0.6
            + item["importance_score"] * 0.3
            + recency_factor           * 0.1
        )

    ranked = sorted(results, key=lambda x: x["_rank"], reverse=True)[:top_k]

    # Boost each retrieved memory (async-safe: called in thread via asyncio.to_thread)
    for item in ranked:
        try:
            vs.boost_memory(item["id"])
        except Exception:
            pass

    # Assemble context
    parts = []
    for i, item in enumerate(ranked, 1):
        ts_str = time.strftime("%Y-%m-%d", time.localtime(item.get("timestamp", 0)))
        parts.append(f"[{i}] ({ts_str}) {item['content']}")

    context = "[RELEVANT MEMORIES]\n" + "\n---\n".join(parts)

    latency = time.time() - retrieval_start
    if _jarvis_memory_retrieval_latency_seconds is not None:
        _jarvis_memory_retrieval_latency_seconds.observe(latency)
    if _jarvis_memory_retrieval_hits_total is not None:
        _jarvis_memory_retrieval_hits_total.labels(hit="hit").inc()

    return context


# ── Helpers ────────────────────────────────────────────────────────────────────

def _record_retrieval_miss() -> None:
    if _jarvis_memory_retrieval_hits_total is not None:
        try:
            _jarvis_memory_retrieval_hits_total.labels(hit="miss").inc()
        except Exception:
            pass


# ── Lazy metrics import (avoids circular: core.metrics → core.memory → core.metrics) ──

_jarvis_embedding_latency_seconds        = None
_jarvis_embedding_failures_total         = None
_jarvis_embedding_generation_seconds     = None   # kept for compat
_jarvis_memory_retrieval_latency_seconds = None
_jarvis_memory_retrieval_hits_total      = None

try:
    from core.metrics import (
        jarvis_embedding_latency_seconds        as _jarvis_embedding_latency_seconds,
        jarvis_embedding_failures_total         as _jarvis_embedding_failures_total,
        jarvis_embedding_generation_seconds     as _jarvis_embedding_generation_seconds,
        jarvis_memory_retrieval_latency_seconds as _jarvis_memory_retrieval_latency_seconds,
        jarvis_memory_retrieval_hits_total      as _jarvis_memory_retrieval_hits_total,
    )
except Exception:
    pass
