"""
config.py — Jarvis Configuration Loader
========================================
All secrets are fetched through core.security.vault.SecretsManager.
No direct os.environ calls exist here — swap the backend in vault.py
(EnvBackend -> HashiCorpVaultBackend / AWSSecretsManagerBackend) and
this file requires zero changes.
"""

import sys
from pathlib import Path

# Ensure core package is importable when running from project root
sys.path.insert(0, str(Path(__file__).parent))

from core.security.vault import get_secrets_manager

_vault = get_secrets_manager()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = _vault.require_secret("TELEGRAM_TOKEN")
ALLOWED_CHAT_ID: int = _vault.get_int("ALLOWED_CHAT_ID")

# ── Ollama (Local AI Engine) ───────────────────────────────────────────────────
OLLAMA_URL: str       = _vault.get_secret("OLLAMA_URL",      default="http://localhost:11434/api/generate")
OLLAMA_CHAT_URL: str  = _vault.get_secret("OLLAMA_CHAT_URL", default="http://localhost:11434/api/chat")
OLLAMA_MODEL: str     = _vault.get_secret("OLLAMA_MODEL",    default="llama3")

# ── Notion (Memory / Logging) ─────────────────────────────────────────────────
NOTION_TOKEN: str       = _vault.require_secret("NOTION_TOKEN")
NOTION_DATABASE_ID: str = _vault.require_secret("NOTION_DATABASE_ID")
