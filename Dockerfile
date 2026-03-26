# ========================================================================
# Jarvis v6.0 — Production-Grade Multi-Stage Dockerfile
# Zero-Trust Ready | Security Hardened | Optimized Caching
# ========================================================================

# ── Stage 1: Base Builder (Dependencies) ────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Security: Create non-root user early
RUN groupadd -r jarvis --gid=1000 && \
    useradd -r -g jarvis --uid=1000 --home-dir=/app --shell=/bin/bash jarvis

# System dependencies (minimal attack surface)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    xvfb \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Python build tools
RUN pip install --no-cache-dir --upgrade pip "setuptools<70.0.0" wheel

# Copy only requirements first (Docker layer caching optimization)
COPY requirements.txt .

# Install Python dependencies in isolated layer
RUN pip install --no-cache-dir --no-build-isolation --prefix=/install -r requirements.txt

# ── Stage 2: Runtime (Production) ───────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Security: Create non-root user
RUN groupadd -r jarvis --gid=1000 && \
    useradd -r -g jarvis --uid=1000 --home-dir=/app --shell=/bin/bash jarvis && \
    mkdir -p /app/workspace /app/backups /app/logs /app/memory /app/ollama_data && \
    chown -R jarvis:jarvis /app

# Runtime-only dependencies (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    xvfb \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=jarvis:jarvis . .

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99 \
    OLLAMA_HOST=http://ollama:11434 \
    TZ=America/Sao_Paulo

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

# Switch to non-root user
USER jarvis

# Expose ports
EXPOSE 8765

# Entrypoint script
COPY --chown=jarvis:jarvis entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-u", "main.py"]
