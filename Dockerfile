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

# System dependencies — includes full X11/GUI stack for pyautogui + headless Xvfb
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-tk \
    python3-dev \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libx11-6 \
    libxtst6 \
    libxi6 \
    libxrandr2 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    ffmpeg \
    xvfb \
    x11-utils \
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

# Runtime dependencies — full headless GUI stack for pyautogui stability
# NOTE: docker.io CLI uses DOCKER_HOST=tcp://dockerproxy:2375 (set in compose).
# If direct socket access is ever needed, the jarvis user must be added to the
# docker group (gid matching the host) or the socket mounted with group write.
RUN echo "Cache bust 2026-03-30-v3" && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3-tk \
    scrot \
    x11-utils \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libxrandr2 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    ffmpeg \
    xvfb \
    curl \
    procps \
    git \
    docker.io \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Build-time verification: fail fast if docker binary is missing
RUN docker --version || (echo "Docker CLI missing!" && exit 1)

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=jarvis:jarvis . .

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99 \
    OLLAMA_HOST=http://ollama:11434 \
    TZ=America/Sao_Paulo \
    PATH="/usr/bin:/usr/local/bin:${PATH}"

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

# Add jarvis to docker group so docker CLI can reach the mounted socket
RUN usermod -aG docker jarvis || true

# Switch to non-root user
USER jarvis

# Expose ports
EXPOSE 8765

# Entrypoint script
COPY --chown=jarvis:jarvis entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-u", "main.py"]
