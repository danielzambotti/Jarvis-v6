"""
core/metrics.py — Prometheus Instrumentation for Jarvis v6.0
=============================================================
Exposes metrics on :8000/metrics for Prometheus scraping.

Core metrics:
  - jarvis_requests_total          Counter   (label: skill)
  - jarvis_security_blocks_total   Counter   (label: layer)
  - jarvis_dlp_redactions_total    Counter   (label: label)
  - jarvis_response_time_seconds   Histogram (label: skill)

Memory / RAG metrics:
  - jarvis_memory_insertions_total            Counter
  - jarvis_memory_retrieval_latency_seconds   Histogram
  - jarvis_embedding_generation_seconds       Histogram
  - jarvis_memory_retrieval_hits_total        Counter   (label: hit)
  - jarvis_chromadb_errors_total              Counter   (label: operation)

Usage (in main.py):
    from core.metrics import start_metrics_server
    start_metrics_server()          # call once at startup

Usage (instrumenting a skill call):
    from core.metrics import (
        jarvis_requests_total,
        jarvis_response_time_seconds,
    )
    with jarvis_response_time_seconds.labels(skill="FACTORY").time():
        result = software_factory.execute(user_input)
    jarvis_requests_total.labels(skill="FACTORY").inc()
"""

import logging
from prometheus_client import Counter, Histogram, start_http_server

logger = logging.getLogger(__name__)

# ── Counters ───────────────────────────────────────────────────────────────────

jarvis_requests_total = Counter(
    "jarvis_requests_total",
    "Total requests processed by Jarvis, partitioned by skill.",
    ["skill"],
)

jarvis_security_blocks_total = Counter(
    "jarvis_security_blocks_total",
    "Total requests blocked by security layers (guardrail / prompt_injection / rate_limit).",
    ["layer"],
)

jarvis_dlp_redactions_total = Counter(
    "jarvis_dlp_redactions_total",
    "Total items redacted by the DLP engine, partitioned by pattern label.",
    ["label"],
)

# ── Histograms ─────────────────────────────────────────────────────────────────

jarvis_response_time_seconds = Histogram(
    "jarvis_response_time_seconds",
    "End-to-end skill execution latency in seconds.",
    ["skill"],
    # Fine-grained at sub-second; coarse above 10 s for LLM calls
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 180.0],
)


# ── Memory / Vector Store Metrics ─────────────────────────────────────────────

jarvis_memory_insertions_total = Counter(
    "jarvis_memory_insertions_total",
    "Total memory entries inserted into the ChromaDB vector store.",
)

jarvis_memory_retrieval_latency_seconds = Histogram(
    "jarvis_memory_retrieval_latency_seconds",
    "End-to-end memory retrieval latency (embedding + similarity search + rerank).",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

jarvis_embedding_generation_seconds = Histogram(
    "jarvis_embedding_generation_seconds",
    "Latency of individual embedding generation calls to Ollama.",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

jarvis_memory_retrieval_hits_total = Counter(
    "jarvis_memory_retrieval_hits_total",
    "Memory retrieval outcomes partitioned by result (hit / miss).",
    ["hit"],
)

jarvis_chromadb_errors_total = Counter(
    "jarvis_chromadb_errors_total",
    "Total errors communicating with ChromaDB, partitioned by operation.",
    ["operation"],
)

jarvis_embedding_latency_seconds = Histogram(
    "jarvis_embedding_latency_seconds",
    "Latency of individual embedding calls to Ollama /api/embed.",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

jarvis_embedding_failures_total = Counter(
    "jarvis_embedding_failures_total",
    "Total embedding generation failures, partitioned by failure reason (timeout/contract/api).",
    ["reason"],
)

# ── Router Metrics ─────────────────────────────────────────────────────────────

jarvis_router_latency_seconds = Histogram(
    "jarvis_router_latency_seconds",
    "LLM classification latency for the intent router, partitioned by model and outcome.",
    ["model", "status"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0],
)

jarvis_router_fallbacks_total = Counter(
    "jarvis_router_fallbacks_total",
    "Total LLM router fallbacks to CONVERSATION, partitioned by reason (timeout / error).",
    ["reason"],
)


# ── Server bootstrap ───────────────────────────────────────────────────────────

def start_metrics_server(port: int = 8000) -> None:
    """
    Start the Prometheus HTTP endpoint.
    Call exactly once at application startup (in main.py _run_all).
    Prometheus scrapes http://jarvis-core:8000/metrics every 15 s.
    """
    try:
        start_http_server(port)
        logger.info("[METRICS] Prometheus endpoint live on :%d/metrics", port)
    except OSError as exc:
        # Port already bound — harmless in hot-reload scenarios
        logger.warning("[METRICS] Could not bind :%d — %s (already running?)", port, exc)
    except Exception as exc:
        logger.error("[METRICS] Failed to start metrics server: %s", exc)
