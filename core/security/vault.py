"""
core/security/vault.py — Secrets Vault Abstraction
====================================================
Provides a provider-agnostic SecretsManager so every module fetches
credentials through one interface regardless of the backend.

CURRENT BACKEND: EnvBackend (reads os.environ / python-dotenv .env file)

TO SWAP BACKEND — change one line in get_secrets_manager():
    # HashiCorp Vault
    return SecretsManager(HashiCorpVaultBackend(url=..., token=...))

    # AWS Secrets Manager
    return SecretsManager(AWSSecretsManagerBackend(region=...))

The rest of the codebase is never touched.

Usage:
    from core.security.vault import get_secrets_manager

    vault = get_secrets_manager()
    token = vault.require_secret("TELEGRAM_TOKEN")
    model = vault.get_secret("OLLAMA_MODEL", default="llama3")
"""

import abc
import logging
import os
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# ── Abstract Backend Interface ────────────────────────────────────────────────

class AbstractSecretBackend(abc.ABC):
    """
    Contract that every secret backend must satisfy.
    Implement this to add HashiCorp Vault, AWS Secrets Manager,
    Azure Key Vault, GCP Secret Manager, or any other provider.
    """

    @abc.abstractmethod
    def get(self, key: str) -> Optional[str]:
        """
        Fetch a secret by key name.

        Returns the secret value as a string, or None if not found.
        Implementations MUST NOT raise on missing keys — return None instead.
        Callers decide whether a missing key is fatal (see SecretsManager).
        """

    @abc.abstractmethod
    def backend_name(self) -> str:
        """Human-readable backend identifier for logging."""


# ── Backend: Environment Variables / .env File ────────────────────────────────

class EnvBackend(AbstractSecretBackend):
    """
    Reads secrets from os.environ (populated by python-dotenv from .env).
    This is the default backend for local and Docker deployments.
    """

    def __init__(self, env_file: Optional[str] = None) -> None:
        load_dotenv(env_file)  # no-op if already loaded; safe to call multiple times

    def get(self, key: str) -> Optional[str]:
        return os.environ.get(key)

    def backend_name(self) -> str:
        return "EnvBackend(.env)"


# ── Backend Stub: HashiCorp Vault ─────────────────────────────────────────────

class HashiCorpVaultBackend(AbstractSecretBackend):
    """
    Stub for HashiCorp Vault (KV v2) integration.

    To implement:
        pip install hvac
        Fill in _client.secrets.kv.v2.read_secret_version(...)
    """

    def __init__(self, url: str, token: str, mount_point: str = "secret") -> None:
        self._url = url
        self._token = token
        self._mount = mount_point
        # self._client = hvac.Client(url=url, token=token)

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError(
            "HashiCorpVaultBackend.get() is a stub. "
            "Install hvac and implement the KV v2 read call."
        )

    def backend_name(self) -> str:
        return f"HashiCorpVault({self._url})"


# ── Backend Stub: AWS Secrets Manager ─────────────────────────────────────────

class AWSSecretsManagerBackend(AbstractSecretBackend):
    """
    Stub for AWS Secrets Manager integration.

    To implement:
        pip install boto3
        Fill in _client.get_secret_value(SecretId=key)
    """

    def __init__(self, region: str, secret_prefix: str = "") -> None:
        self._region = region
        self._prefix = secret_prefix
        # self._client = boto3.client("secretsmanager", region_name=region)

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError(
            "AWSSecretsManagerBackend.get() is a stub. "
            "Install boto3 and implement the get_secret_value call."
        )

    def backend_name(self) -> str:
        return f"AWSSecretsManager(region={self._region})"


# ── SecretsManager: The Public Interface ─────────────────────────────────────

class SecretsManager:
    """
    Provider-agnostic secrets accessor.

    All application code calls this class — never a backend directly.
    Swapping the backend (EnvBackend → HashiCorpVaultBackend) requires
    only changing the argument passed to __init__, not the callers.
    """

    def __init__(self, backend: AbstractSecretBackend) -> None:
        self._backend = backend
        logger.info("[VAULT] Initialized with backend: %s", backend.backend_name())

    # ── Public API ────────────────────────────────────────────────────────────

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Fetch an optional secret. Returns `default` if not found.

        Use for non-critical config (e.g., OLLAMA_MODEL with a fallback).
        """
        value = self._backend.get(key)
        if value is None:
            logger.debug("[VAULT] Key '%s' not found, using default.", key)
            return default
        return value

    def require_secret(self, key: str) -> str:
        """
        Fetch a required secret. Raises EnvironmentError if missing.

        Use for credentials whose absence must halt startup
        (e.g., TELEGRAM_TOKEN, NOTION_TOKEN).
        """
        value = self._backend.get(key)
        if not value:
            raise EnvironmentError(
                f"[VAULT] Required secret '{key}' is missing from backend "
                f"'{self._backend.backend_name()}'. "
                f"Add it to your .env file or secret store."
            )
        return value

    def get_int(self, key: str, default: int = 0) -> int:
        """Convenience: fetch a secret and cast to int."""
        raw = self.get_secret(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            logger.error("[VAULT] Key '%s' value '%s' is not a valid int.", key, raw)
            return default

    @property
    def backend(self) -> AbstractSecretBackend:
        return self._backend


# ── Module-Level Singleton ────────────────────────────────────────────────────

_instance: Optional[SecretsManager] = None


def get_secrets_manager() -> SecretsManager:
    """
    Returns the module-level SecretsManager singleton.

    To swap the backend, modify this function:
        global _instance
        _instance = SecretsManager(HashiCorpVaultBackend(...))
    """
    global _instance
    if _instance is None:
        _instance = SecretsManager(EnvBackend())
    return _instance
