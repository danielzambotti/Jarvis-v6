"""
skills/memory_vault.py — Explicit Memory Skill with Secure Reveal Pattern

Handles user-directed memory operations:
  - SAVE:     "remember X", "save this", "store in memory"
  - RETRIEVE: "what do you remember about X", "recall X"
  - FORGET:   "forget about X", "delete from memory"
  - REVEAL:   "REVEAL [description]" — decrypt a stored credential

Credentials (senha/password/token/key) are encrypted at rest using AES-128
(Fernet / cryptography library) before being written to ChromaDB.  Plain-text
is NEVER stored.  Retrieval returns a masked stub; the user must issue an
explicit REVEAL command to decrypt.
"""
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Intent patterns ───────────────────────────────────────────────────────────

_SAVE_PATTERN = re.compile(
    r'\b(remember\s+that|remember:|save\s+this|store\s+in\s+memory|'
    r'lembre[-\s]se\s+de|salve\s+isso|armazene|guard[ae]\s+(?:isso|minha?|o\s+meu?))\b',
    re.IGNORECASE,
)
_RETRIEVE_PATTERN = re.compile(
    r'\b(what\s+do\s+you\s+remember|what\s+do\s+you\s+know\s+about|recall|'
    r'o\s+que\s+voc[êe]\s+(?:se\s+)?lembra|oq\s+vc\s+lembra|o\s+que\s+sabe\s+sobre|'
    r'voc[êe]\s+(?:se\s+)?lembra|vc\s+(?:se\s+)?lembra|'
    r'lembra\s+(?:da?|do|de)\b|se\s+lembra\s+(?:da?|do|de))\b',
    re.IGNORECASE,
)
_FORGET_PATTERN = re.compile(
    r'\b(forget\s+about|forget\s+that|esqueça|apague\s+da\s+mem[oó]ria|delete\s+from\s+memory)\b',
    re.IGNORECASE,
)
_REVEAL_PATTERN = re.compile(r'\bREVEAL\b', re.IGNORECASE)

# Fallback: generic "remember X" without the refined save triggers above
_GENERIC_SAVE = re.compile(r'\bremember\b', re.IGNORECASE)

# Credential detection — triggers AES encryption path
_CREDENTIAL_PATTERN = re.compile(
    r'\b(senha|password|pass|token|api[_\s]?key|api[_\s]?secret|secret[_\s]?key|'
    r'chave|credencial|private[_\s]?key|access[_\s]?key|bearer|auth[_\s]?token|jwt)\b',
    re.IGNORECASE,
)


# ── Encryption key management ─────────────────────────────────────────────────

_fernet_instance = None


def _get_fernet():
    """
    Load or generate a Fernet (AES-128-CBC + HMAC-SHA256) encryption key.

    Priority order:
      1. JARVIS_SECRET_KEY env var (base64-url-encoded 32-byte key — set this
         in .env for production; it survives container restarts).
      2. {WORKSPACE_PATH}/.jarvis.key file (auto-generated on first run, chmod 600).

    Returns a `Fernet` instance, or raises RuntimeError if the library is absent.
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package not installed. "
            "Add 'cryptography>=41.0.0' to requirements.txt and rebuild."
        ) from exc

    key_b64 = os.environ.get("JARVIS_SECRET_KEY", "").strip()
    if key_b64:
        key = key_b64.encode()
        logger.debug("[MEMORY_VAULT] Encryption key loaded from JARVIS_SECRET_KEY env var")
    else:
        workspace = os.environ.get("WORKSPACE_PATH", "/app/workspace")
        key_file  = Path(workspace) / ".jarvis.key"
        if key_file.exists():
            key = key_file.read_bytes().strip()
            logger.debug("[MEMORY_VAULT] Encryption key loaded from %s", key_file)
        else:
            key = Fernet.generate_key()
            try:
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(key)
                key_file.chmod(0o600)   # owner read/write only
                logger.info("[MEMORY_VAULT] Generated new AES encryption key → %s (chmod 600)", key_file)
            except Exception as exc:
                logger.warning(
                    "[MEMORY_VAULT] Could not persist key file (%s) — "
                    "key is session-only; previously encrypted memories will be unreadable after restart",
                    exc,
                )

    _fernet_instance = Fernet(key)
    return _fernet_instance


# ── Main entry point ──────────────────────────────────────────────────────────

def execute(user_input: str) -> str:
    """Dispatch to reveal / save / retrieve / forget based on intent."""
    logger.info("[VAULT-TRACE] ENTRY | input='%s'", user_input[:80])
    try:
        # REVEAL must come before RETRIEVE — "REVEAL my password" would
        # otherwise be matched by the generic retrieve pattern.
        logger.debug("[VAULT-TRACE] Checking REVEAL pattern...")
        if _REVEAL_PATTERN.search(user_input):
            return _reveal(user_input)

        logger.debug("[VAULT-TRACE] Checking FORGET pattern...")
        if _FORGET_PATTERN.search(user_input):
            return _forget(user_input)

        logger.debug("[VAULT-TRACE] Checking RETRIEVE pattern...")
        if _RETRIEVE_PATTERN.search(user_input):
            return _retrieve(user_input)

        logger.debug("[VAULT-TRACE] Checking SAVE pattern...")
        if _SAVE_PATTERN.search(user_input) or _GENERIC_SAVE.search(user_input):
            content = _extract_save_content(user_input)
            return _save(content if content else user_input)

        # No pattern matched — router Tier-1 routed here but sub-intent is unrecognised.
        # This should never happen if router patterns and vault patterns are in sync.
        logger.error("[VAULT-TRACE] NO MATCH → returning explicit error | input='%s'", user_input[:80])
        return "ERRO INTERNO: Vault acionado mas sem correspondência de intenção."

    except Exception as exc:
        logger.error("[MEMORY_VAULT] Unexpected error: %s", exc)
        return f"Memory system error: {exc}"


# ── Save (with optional AES encryption) ──────────────────────────────────────

def _save(content: str) -> str:
    from core.memory.vector_store import get_vector_store, LONG_TERM
    from core.memory.retriever import generate_embedding

    content = content.strip()
    if not content:
        return "Nothing to remember — content was empty."

    is_credential = bool(_CREDENTIAL_PATTERN.search(content))

    if is_credential:
        return _save_credential(content)

    # ── Plain memory path ──────────────────────────────────────────────────
    content = _dlp_sanitize(content)
    if not content.strip():
        return "Nothing to remember — content was empty after sanitization."

    content = content[:2000]
    embedding = generate_embedding(content)
    if embedding is None:
        return "Could not generate embedding (Ollama may be unavailable). Memory not saved."

    vs = get_vector_store()
    mem_id = vs.store_memory(
        content     = content,
        embedding   = embedding,
        memory_type = LONG_TERM,
        importance  = 0.7,
        tags        = ["user_saved"],
    )

    if mem_id:
        _increment_insertions()
        return (
            f"Memory saved. (id: `{mem_id[:8]}…`)\n"
            f"> {content[:200]}{'…' if len(content) > 200 else ''}"
        )
    return "Memory could not be stored (ChromaDB may be unavailable)."


def _save_credential(content: str) -> str:
    """Encrypt the content and store only the ciphertext + a safe searchable label."""
    from core.memory.vector_store import get_vector_store, LONG_TERM
    from core.memory.retriever import generate_embedding

    # Encrypt the full plaintext — DO NOT run DLP (user is intentionally storing it)
    try:
        fernet = _get_fernet()
    except RuntimeError as exc:
        return f"Cannot store credential securely: {exc}"

    ciphertext_b64 = fernet.encrypt(content.encode()).decode()

    # Build a searchable safe label without the actual secret value
    safe_label = _make_credential_label(content)

    # Embed the safe label (not the secret)
    embedding = generate_embedding(safe_label)
    if embedding is None:
        return "Could not generate embedding (Ollama may be unavailable). Credential not saved."

    vs = get_vector_store()
    mem_id = vs.store_memory(
        content     = safe_label,
        embedding   = embedding,
        memory_type = LONG_TERM,
        importance  = 0.9,          # credentials are high-importance
        tags        = ["user_saved", "credential"],
    )
    if not mem_id:
        return "Credential could not be stored (ChromaDB may be unavailable)."

    # Attach ciphertext to the ChromaDB metadata (primitive string value)
    if vs._collection is not None:
        try:
            raw  = vs._collection.get(ids=[mem_id], include=["metadatas"])
            meta = raw["metadatas"][0].copy()
            meta["ciphertext"] = ciphertext_b64
            vs._collection.update(ids=[mem_id], metadatas=[meta])
        except Exception as exc:
            logger.error("[MEMORY_VAULT] Failed to attach ciphertext to metadata: %s", exc)
            vs.update_importance(mem_id, -1.0)  # soft-delete incomplete record
            return "Credential storage failed (metadata update error). Please try again."

    _increment_insertions()
    return (
        f"🔒 Credential stored encrypted. (id: `{mem_id[:8]}…`)\n"
        f"> Label: {safe_label}\n\n"
        f"Reply **REVEAL {safe_label.split(':')[0].strip()}** to decrypt it later."
    )


# ── Retrieve ──────────────────────────────────────────────────────────────────

def _retrieve(query: str) -> str:
    from core.memory.retriever import generate_embedding
    from core.memory.vector_store import get_vector_store

    embedding = generate_embedding(query)
    if embedding is None:
        return "Could not search memory (embedding generation failed)."

    vs      = get_vector_store()
    results = vs.search_memories(embedding, top_k=5)

    if not results:
        return "No relevant memories found for that query."

    lines            = []
    has_credentials  = False

    for i, item in enumerate(results, 1):
        ts_str = time.strftime("%Y-%m-%d", time.localtime(item.get("timestamp", 0)))
        if "credential" in item.get("tags", []):
            lines.append(
                f"[{i}] ({ts_str}) 🔒 {item['content']} — "
                f"Reply **REVEAL {item['content'].split(':')[0].strip()}** to decrypt."
            )
            has_credentials = True
        else:
            lines.append(f"[{i}] ({ts_str}) {item['content']}")

    response = "Here is what I remember:\n\n" + "\n---\n".join(lines)
    if has_credentials:
        response += (
            "\n\n⚠️ Credentials are AES-encrypted at rest. "
            "Use **REVEAL [description]** to decrypt a specific entry."
        )
    return response


# ── Reveal ────────────────────────────────────────────────────────────────────

def _reveal(query: str) -> str:
    """
    Decrypt and return a stored credential.

    The user must explicitly type REVEAL [description].  The description is
    used to find the closest credential memory; if similarity < 0.6 or no
    credential tag is found we refuse to reveal.
    """
    from core.memory.retriever import generate_embedding
    from core.memory.vector_store import get_vector_store

    # Strip the REVEAL keyword to get the search query
    search_query = re.sub(r'\bREVEAL\b', '', query, flags=re.IGNORECASE).strip()
    if not search_query:
        return "Please specify what to reveal. Example: **REVEAL github token**"

    embedding = generate_embedding(search_query)
    if embedding is None:
        return "Could not search memory (embedding generation failed)."

    vs      = get_vector_store()
    results = vs.search_memories(embedding, top_k=3)

    # Find the best-matching credential entry
    target: Optional[dict] = None
    for item in results:
        if "credential" in item.get("tags", []) and item["similarity"] >= 0.6:
            target = item
            break

    if target is None:
        return (
            "No matching credential found for that description.\n"
            "Try: **REVEAL [exact label]** — use the label shown when you saved it."
        )

    # Fetch ciphertext from ChromaDB metadata
    if vs._collection is None:
        return "ChromaDB is unavailable — cannot decrypt credential."

    try:
        raw      = vs._collection.get(ids=[target["id"]], include=["metadatas"])
        ciphertext_b64 = raw["metadatas"][0].get("ciphertext", "")
    except Exception as exc:
        logger.error("[MEMORY_VAULT] Failed to fetch ciphertext: %s", exc)
        return "Failed to retrieve encrypted data. ChromaDB error."

    if not ciphertext_b64:
        return "No ciphertext found for this memory entry. The credential may have been stored incorrectly."

    try:
        fernet    = _get_fernet()
        plaintext = fernet.decrypt(ciphertext_b64.encode()).decode()
    except Exception as exc:
        logger.error("[MEMORY_VAULT] Decryption failed: %s", exc)
        return (
            "Decryption failed. This usually means the encryption key has changed "
            "(e.g., container was restarted without JARVIS_SECRET_KEY set)."
        )

    return (
        f"🔓 **Credential revealed** (id: `{target['id'][:8]}…`):\n"
        f"```\n{plaintext}\n```\n"
        "⚠️ Sensitive data — do not share this message."
    )


# ── Forget ────────────────────────────────────────────────────────────────────

def _forget(query: str) -> str:
    from core.memory.vector_store import get_vector_store
    from core.memory.retriever import generate_embedding

    embedding = generate_embedding(query)
    if embedding is None:
        return "Could not process the forget request (embedding generation failed)."

    vs      = get_vector_store()
    results = vs.search_memories(embedding, top_k=1)

    if not results or results[0]["similarity"] < 0.7:
        return "No matching memory found to forget."

    target = results[0]
    vs.update_importance(target["id"], -1.0)

    label = "🔒 [credential]" if "credential" in target.get("tags", []) else target["content"][:100]
    return (
        f"Memory removed from active recall. (id: `{target['id'][:8]}…`)\n"
        f"> {label}{'…' if len(target.get('content', '')) > 100 else ''}"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_save_content(text: str) -> str:
    """Strip the memory trigger phrase, return the payload."""
    triggers = [
        r'remember\s+that\s+', r'remember:\s*', r'save\s+this[:\s]*',
        r'store\s+in\s+memory[:\s]*', r'lembre[-\s]se\s+de\s+', r'salve\s+isso[:\s]*',
        r'armazene[:\s]*', r'guard[ae]\s+(?:isso|minha?|o\s+meu?)[:\s]*', r'remember\s+',
    ]
    for pattern in triggers:
        cleaned = re.sub(pattern, '', text, count=1, flags=re.IGNORECASE).strip()
        if cleaned and cleaned != text.strip():
            return cleaned
    return text.strip()


def _make_credential_label(content: str) -> str:
    """
    Build a safe, searchable label from the content without exposing the value.
    E.g.: "guarde minha senha do GitHub ghp_abc123" → "senha do GitHub: [ENCRYPTED VALUE]"
    """
    match = _CREDENTIAL_PATTERN.search(content)
    if match:
        # Keep content up to and including the keyword + a few chars of context
        prefix = content[:match.end() + 40].strip()
        # Trim at the last space to avoid cutting mid-word
        if len(prefix) > match.end() + 5:
            prefix = prefix.rsplit(' ', 1)[0]
        return f"{prefix.strip()}: [ENCRYPTED VALUE]"
    return "[CREDENTIAL: ENCRYPTED]"


def _dlp_sanitize(content: str) -> str:
    try:
        from core.security.dlp import get_dlp_engine
        dlp = get_dlp_engine()
        clean, findings = dlp.sanitize_text(content)
        if findings:
            logger.warning("[MEMORY_VAULT] DLP stripped %d item(s) from memory content", len(findings))
        return clean
    except Exception as exc:
        logger.debug("[MEMORY_VAULT] DLP unavailable (%s) — storing unsanitized", exc)
        return content


def _increment_insertions() -> None:
    try:
        from core.metrics import jarvis_memory_insertions_total
        jarvis_memory_insertions_total.inc()
    except Exception:
        pass
