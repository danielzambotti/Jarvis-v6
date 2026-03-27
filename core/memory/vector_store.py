"""
core/memory/vector_store.py — ChromaDB Vector Memory Store

Fourth memory layer: semantic, long-term, self-improving.
No internal Jarvis imports (prevents circular imports at startup).
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_CHROMADB_URL    = os.environ.get("CHROMADB_URL", "http://chromadb:8000")
_COLLECTION_NAME = "jarvis_memories"
_COSINE_DEDUP_THRESHOLD  = 0.95   # skip store if top-1 similarity >= this
_DECAY_FACTOR            = 0.95   # importance multiplier per decay cycle
_DECAY_DAYS_THRESHOLD    = 7      # days of no access before decay kicks in
_IMPORTANCE_SOFT_DELETE  = 0.05   # score threshold below which entry is soft-deleted
_BOOST_DELTA             = 0.05   # importance increase per retrieval hit
_MAX_CONTENT_LENGTH      = 2000   # chars; enforced before embedding generation

# Memory persistence tiers
SHORT_TERM = "short_term"
LONG_TERM  = "long_term"
CRITICAL   = "critical"   # never decayed, never soft-deleted


# ── Memory Entry Schema ───────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    id: str                          # uuid4 string
    content: str                     # max _MAX_CONTENT_LENGTH chars
    embedding: list                  # float list from Ollama
    type: str                        # "conversation" | "insight" | "system_event"
    importance_score: float          # 0.0–1.0
    timestamp: float                 # unix epoch
    tags: list                       # serialized as json.dumps() in ChromaDB
    access_count: int   = 0
    last_accessed: float = 0.0


# ── Vector Store ──────────────────────────────────────────────────────────────

class VectorStore:
    """ChromaDB-backed semantic memory store with graceful fallback."""

    def __init__(self) -> None:
        self._client     = None
        self._collection = None
        self._fallback: dict[str, MemoryEntry] = {}   # in-memory when Chroma is down
        self._init_chromadb()

    def _init_chromadb(self) -> None:
        try:
            import chromadb
            host = _CHROMADB_URL.replace("http://", "").replace("https://", "").split(":")[0]
            port = int(_CHROMADB_URL.rsplit(":", 1)[-1]) if ":" in _CHROMADB_URL.rsplit("/", 1)[-1] else 8000
            self._client = chromadb.HttpClient(host=host, port=port)
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("[VECTOR_STORE] Connected to ChromaDB at %s — collection '%s'", _CHROMADB_URL, _COLLECTION_NAME)
        except Exception as exc:
            logger.warning("[VECTOR_STORE] ChromaDB unavailable (%s) — running in fallback mode", exc)
            self._client     = None
            self._collection = None
            try:
                from core.metrics import jarvis_chromadb_errors_total
                jarvis_chromadb_errors_total.labels(operation="connect").inc()
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    def store_memory(
        self,
        content: str,
        embedding: list,
        memory_type: str = LONG_TERM,
        importance: float = 0.7,
        tags: list | None = None,
    ) -> Optional[str]:
        """Store a memory. Returns the entry id, or None on failure."""
        if tags is None:
            tags = []

        # Truncate content
        content = content[:_MAX_CONTENT_LENGTH]

        # Deduplication: skip if near-duplicate already exists
        if embedding and self._collection is not None:
            try:
                results = self._collection.query(
                    query_embeddings=[embedding],
                    n_results=1,
                    include=["distances"],
                )
                distances = results.get("distances", [[]])[0]
                if distances and (1.0 - distances[0]) >= _COSINE_DEDUP_THRESHOLD:
                    existing_id = results["ids"][0][0]
                    logger.debug("[VECTOR_STORE] Skipping duplicate (sim=%.3f), id=%s", 1.0 - distances[0], existing_id)
                    return existing_id
            except Exception as exc:
                logger.warning("[VECTOR_STORE] Dedup check failed: %s", exc)
                self._record_error("dedup")

        entry = MemoryEntry(
            id              = str(uuid.uuid4()),
            content         = content,
            embedding       = embedding,
            type            = memory_type,
            importance_score = importance,
            timestamp       = time.time(),
            tags            = tags,
            access_count    = 0,
            last_accessed   = time.time(),
        )

        if self._collection is not None:
            try:
                self._collection.add(
                    ids        = [entry.id],
                    embeddings = [embedding] if embedding else None,
                    documents  = [content],
                    metadatas  = [self._entry_to_metadata(entry)],
                )
                logger.debug("[VECTOR_STORE] Stored memory id=%s type=%s", entry.id, memory_type)
                return entry.id
            except Exception as exc:
                logger.error("[VECTOR_STORE] Failed to store in ChromaDB: %s", exc)
                self._record_error("store")
                # Fall through to in-memory fallback

        # In-memory fallback
        self._fallback[entry.id] = entry
        logger.warning("[VECTOR_STORE] Memory stored in fallback (in-memory), id=%s", entry.id)
        return entry.id

    def search_memories(self, query_embedding: list, top_k: int = 5) -> list[dict]:
        """Return top-k semantically similar memories as list of dicts."""
        if not query_embedding:
            return []

        if self._collection is None:
            logger.debug("[VECTOR_STORE] search_memories called in fallback mode — returning empty")
            return []

        try:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, max(1, self._collection.count())),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.error("[VECTOR_STORE] search_memories failed: %s", exc)
            self._record_error("search")
            return []

        items = []
        ids        = results.get("ids", [[]])[0]
        documents  = results.get("documents", [[]])[0]
        metadatas  = results.get("metadatas", [[]])[0]
        distances  = results.get("distances", [[]])[0]

        for mem_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            # Filter soft-deleted entries
            if meta.get("is_deleted", False):
                continue
            similarity = max(0.0, 1.0 - dist)   # ChromaDB cosine dist in [0,2]
            items.append({
                "id":               mem_id,
                "content":          doc,
                "similarity":       similarity,
                "importance_score": float(meta.get("importance_score", 0.5)),
                "timestamp":        float(meta.get("timestamp", 0.0)),
                "type":             meta.get("type", LONG_TERM),
                "tags":             json.loads(meta.get("tags", "[]")),
                "access_count":     int(meta.get("access_count", 0)),
            })

        return items

    def update_importance(self, memory_id: str, new_score: float) -> None:
        """Update importance_score for a memory entry."""
        if self._collection is None:
            return
        new_score = max(-1.0, min(1.0, new_score))
        try:
            result = self._collection.get(ids=[memory_id], include=["metadatas"])
            if not result["ids"]:
                return
            meta = result["metadatas"][0].copy()
            meta["importance_score"] = new_score
            if new_score < 0:
                meta["is_deleted"] = True
            self._collection.update(ids=[memory_id], metadatas=[meta])
        except Exception as exc:
            logger.error("[VECTOR_STORE] update_importance failed for %s: %s", memory_id, exc)
            self._record_error("update")

    def boost_memory(self, memory_id: str) -> None:
        """Boost importance score and record access on retrieval."""
        if self._collection is None:
            return
        try:
            result = self._collection.get(ids=[memory_id], include=["metadatas"])
            if not result["ids"]:
                return
            meta = result["metadatas"][0].copy()
            meta["importance_score"] = min(1.0, float(meta.get("importance_score", 0.5)) + _BOOST_DELTA)
            meta["access_count"]  = int(meta.get("access_count", 0)) + 1
            meta["last_accessed"] = time.time()
            self._collection.update(ids=[memory_id], metadatas=[meta])
        except Exception as exc:
            logger.debug("[VECTOR_STORE] boost_memory failed for %s: %s", memory_id, exc)

    def decay_memories(self) -> int:
        """
        Decay importance_score for memories not accessed in _DECAY_DAYS_THRESHOLD days.
        CRITICAL-type memories are never decayed.
        Entries below _IMPORTANCE_SOFT_DELETE are soft-deleted.
        Returns count of updated entries.
        """
        if self._collection is None:
            return 0

        now = time.time()
        cutoff = now - (_DECAY_DAYS_THRESHOLD * 86400)
        updated = 0

        try:
            total = self._collection.count()
            if total == 0:
                return 0

            # Paginate to handle large collections
            batch_size = 500
            offset = 0
            to_update_ids   = []
            to_update_metas = []

            while offset < total:
                result = self._collection.get(
                    limit=batch_size,
                    offset=offset,
                    include=["metadatas"],
                )
                offset += batch_size

                for mem_id, meta in zip(result["ids"], result["metadatas"]):
                    if meta.get("is_deleted", False):
                        continue
                    if meta.get("type") == CRITICAL:
                        continue

                    last_accessed = float(meta.get("last_accessed", 0.0))
                    if last_accessed > cutoff:
                        continue   # accessed recently — skip decay

                    new_score = float(meta.get("importance_score", 0.5)) * _DECAY_FACTOR
                    new_meta  = meta.copy()
                    new_meta["importance_score"] = new_score
                    if new_score < _IMPORTANCE_SOFT_DELETE:
                        new_meta["is_deleted"] = True
                        logger.debug("[VECTOR_STORE] Soft-deleting memory %s (score=%.4f)", mem_id, new_score)

                    to_update_ids.append(mem_id)
                    to_update_metas.append(new_meta)
                    updated += 1

            if to_update_ids:
                self._collection.update(ids=to_update_ids, metadatas=to_update_metas)

        except Exception as exc:
            logger.error("[VECTOR_STORE] decay_memories failed: %s", exc)
            self._record_error("decay")

        return updated

    def merge_similar(self, threshold: float = 0.97) -> int:
        """
        Soft-delete near-duplicate entries (keep higher importance_score).
        Returns count of merges performed.
        """
        if self._collection is None:
            return 0

        merged = 0
        try:
            result = self._collection.get(include=["embeddings", "metadatas"])
            ids        = result["ids"]
            embeddings = result.get("embeddings") or []
            metadatas  = result["metadatas"]

            if len(ids) < 2 or not embeddings:
                return 0

            to_delete = set()
            for i in range(len(ids)):
                if ids[i] in to_delete or metadatas[i].get("is_deleted"):
                    continue
                for j in range(i + 1, len(ids)):
                    if ids[j] in to_delete or metadatas[j].get("is_deleted"):
                        continue
                    # Cosine similarity
                    a, b = embeddings[i], embeddings[j]
                    dot  = sum(x * y for x, y in zip(a, b))
                    na   = sum(x * x for x in a) ** 0.5
                    nb   = sum(x * x for x in b) ** 0.5
                    sim  = dot / (na * nb + 1e-10)
                    if sim >= threshold:
                        # Keep the one with higher importance_score
                        score_i = float(metadatas[i].get("importance_score", 0.5))
                        score_j = float(metadatas[j].get("importance_score", 0.5))
                        loser   = ids[j] if score_i >= score_j else ids[i]
                        to_delete.add(loser)
                        merged += 1

            if to_delete:
                for dead_id in to_delete:
                    idx = ids.index(dead_id)
                    meta = metadatas[idx].copy()
                    meta["is_deleted"] = True
                    self._collection.update(ids=[dead_id], metadatas=[meta])
        except Exception as exc:
            logger.error("[VECTOR_STORE] merge_similar failed: %s", exc)

        return merged

    def get_collection_count(self) -> int:
        """Return total active (non-deleted) memory count, or -1 on error."""
        if self._collection is None:
            return -1
        try:
            return self._collection.count()
        except Exception:
            return -1

    # ── Internal Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _entry_to_metadata(entry: MemoryEntry) -> dict:
        return {
            "type":             entry.type,
            "importance_score": entry.importance_score,
            "timestamp":        entry.timestamp,
            "tags":             json.dumps(entry.tags),
            "access_count":     entry.access_count,
            "last_accessed":    entry.last_accessed,
            "is_deleted":       False,
        }

    def _record_error(self, operation: str) -> None:
        try:
            from core.metrics import jarvis_chromadb_errors_total
            jarvis_chromadb_errors_total.labels(operation=operation).inc()
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────

_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
