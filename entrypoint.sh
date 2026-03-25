#!/bin/bash
# ========================================================================
# Jarvis v6.0 — Container Entrypoint Script
# Responsibilities: Health checks, initialization, graceful shutdown
# ========================================================================

set -e  # Exit on error

# Colors for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[JARVIS-INIT]${NC} $(date '+%Y-%m-%d %H:%M:%S') | INFO  | $1"
}

log_warn() {
    echo -e "${YELLOW}[JARVIS-INIT]${NC} $(date '+%Y-%m-%d %H:%M:%S') | WARN  | $1"
}

log_error() {
    echo -e "${RED}[JARVIS-INIT]${NC} $(date '+%Y-%m-%d %H:%M:%S') | ERROR | $1"
}

# ── Phase 1: Environment Validation ─────────────────────────────────────
log_info "Starting Jarvis v6.0 initialization..."

# Check required environment variables
REQUIRED_VARS=("TELEGRAM_TOKEN" "ALLOWED_CHAT_ID" "OLLAMA_HOST")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        log_error "Required environment variable $var is not set"
        exit 1
    fi
done

log_info "Environment validation passed ✓"

# ── Phase 2: Dependency Health Checks ───────────────────────────────────
log_info "Checking service dependencies..."

# Wait for Ollama
MAX_RETRIES=30
RETRY_COUNT=0
OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"

log_info "Waiting for Ollama at $OLLAMA_HOST..."
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -sf "${OLLAMA_HOST}/api/version" > /dev/null 2>&1; then
        log_info "Ollama is ready ✓"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    log_warn "Ollama not ready yet (attempt $RETRY_COUNT/$MAX_RETRIES)..."
    sleep 2
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    log_error "Ollama failed to start after $MAX_RETRIES attempts"
    exit 1
fi

# Wait for Redis (if configured)
if [ -n "$REDIS_URL" ]; then
    log_info "Waiting for Redis..."
    REDIS_HOST=$(echo $REDIS_URL | sed -n 's/.*:\/\/\([^:]*\).*/\1/p')
    REDIS_PORT=$(echo $REDIS_URL | sed -n 's/.*:\([0-9]*\).*/\1/p')
    
    RETRY_COUNT=0
    while [ $RETRY_COUNT -lt 15 ]; do
        if timeout 2 bash -c "echo > /dev/tcp/${REDIS_HOST}/${REDIS_PORT}" 2>/dev/null; then
            log_info "Redis is ready ✓"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        sleep 1
    done
fi

# Wait for PostgreSQL (if configured)
if [ -n "$DATABASE_URL" ]; then
    log_info "Waiting for PostgreSQL..."
    RETRY_COUNT=0
    while [ $RETRY_COUNT -lt 15 ]; do
        if python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')" 2>/dev/null; then
            log_info "PostgreSQL is ready ✓"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        sleep 1
    done
fi

# ── Phase 3: Directory Structure Initialization ────────────────────────
log_info "Initializing directory structure..."

DIRECTORIES=(
    "/app/workspace"
    "/app/backups"
    "/app/logs"
    "/app/memory"
    "/app/skills"
)

for dir in "${DIRECTORIES[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        log_info "Created directory: $dir"
    fi
done

log_info "Directory structure initialized ✓"

# ── Phase 4: Virtual Display (Xvfb) for UI Automation ──────────────────
log_info "Starting virtual display (Xvfb)..."

# Start Xvfb in the background
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to start
sleep 2

if ps -p $XVFB_PID > /dev/null; then
    log_info "Virtual display started (PID: $XVFB_PID) ✓"
else
    log_error "Failed to start virtual display"
    exit 1
fi

# ── Phase 5: Application Startup ────────────────────────────────────────
log_info "Starting Jarvis application..."
log_info "Dashboard will be available at http://localhost:8765"
log_info "API documentation at http://localhost:8765/docs"

# Trap SIGTERM and SIGINT for graceful shutdown
cleanup() {
    log_info "Received shutdown signal, cleaning up..."
    
    # Kill Xvfb
    if [ -n "$XVFB_PID" ]; then
        kill $XVFB_PID 2>/dev/null || true
        log_info "Xvfb stopped"
    fi
    
    log_info "Jarvis v6.0 shutdown complete"
    exit 0
}

trap cleanup SIGTERM SIGINT

# Execute the CMD passed to the container
log_info "Executing: $@"
exec "$@"
