"""
skills/memory_vault.py — Explicit Memory Skill

Handles user-directed memory operations:
  - SAVE:     "remember X", "save this", "store in memory"
  - RETRIEVE: "what do you remember about X", "recall X"
  - FORGET:   "forget about X", "delete from memory"
"""
import logging
import re
import time

logger = logging.getLogger(__name__)

# ── Intent patterns ───────────────────────────────────────────────────────────

_SAVE_PATTERN = re.compile(
    r'\b(remember\s+that|remember:|save\s+this|store\s+in\s+memory|lembre[-\s]se\s+de|salve\s+isso|armazene)\b',
    re.IGNORECASE,
)
_RETRIEVE_PATTERN = re.compile(
    r'\b(what\s+do\s+you\s+remember|what\s+do\s+you\s+know\s+about|recall|o\s+que\s+voc[êe]\s+lembra|o\s+que\s+sabe\s+sobre)\b',
    re.IGNORECASE,
)
_FORGET_PATTERN = re.compile(
    r'\b(forget\s+about|forget\s+that|esqueça|apague\s+da\s+mem[oó]ria|delete\s+from\s+memory)\b',
    re.IGNORECASE,
)

# Fallback: generic "remember X" without the refined save triggers above
_GENERIC_SAVE = re.compile(r'\bremember\b', re.IGNORECASE)


# ── Main entry point ──────────────────────────────────────────────────────────

def execute(user_input: str) -> str:
    """Dispatch to save / retrieve / forget based on intent."""
    try:
        if _FORGET_PATTERN.search(user_input):
            return _forget(user_input)

        if _RETRIEVE_PATTERN.search(user_input):
            return _retrieve(user_input)

        if _SAVE_PATTERN.search(user_input) or _GENERIC_SAVE.search(user_input):
            # Extract the content after the trigger phrase
            content = _extract_save_content(user_input)
            return _save(content if content else user_input)

        # Ambiguous — default to retrieve
        return _retrieve(user_input)

    except Exception as exc:
        logger.error("[MEMORY_VAULT] Unexpected error: %s", exc)
        return f"Memory system error: {exc}"


# ── Save ──────────────────────────────────────────────────────────────────────

def _save(content: str) -> str:
    from core.memory.vector_store import get_vector_store, LONG_TERM
    from core.memory.retriever import generate_embedding

    # DLP sanitization
    content = _dlp_sanitize(content)
    if not content.strip():
        return "Nothing to remember — content was empty after sanitization."

    # Truncate
    content = content[:2000]

    embedding = generate_embedding(content)
    if embedding is None:
        return "Could not generate embedding for this memory (Ollama may be unavailable). Memory not saved."

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
        return f"Memory saved. (id: `{mem_id[:8]}…`)\n> {content[:200]}{'…' if len(content) > 200 else ''}"
    else:
        return "Memory could not be stored (ChromaDB may be unavailable). Saved to short-term buffer only."


# ── Retrieve ──────────────────────────────────────────────────────────────────

def _retrieve(query: str) -> str:
    from core.memory.retriever import get_relevant_context

    context = get_relevant_context(query, top_k=5)
    if not context:
        return "No relevant memories found for that query."

    # Strip the [RELEVANT MEMORIES] header and reformat for user-facing output
    lines = context.replace("[RELEVANT MEMORIES]\n", "").strip()
    return f"Here is what I remember:\n\n{lines}"


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
    vs.update_importance(target["id"], -1.0)   # soft-delete via negative score
    return (
        f"Memory removed from active recall. (id: `{target['id'][:8]}…`)\n"
        f"> {target['content'][:200]}{'…' if len(target['content']) > 200 else ''}"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_save_content(text: str) -> str:
    """Strip the memory trigger phrase, return the payload."""
    # Try to remove leading trigger phrase
    triggers = [
        r'remember\s+that\s+', r'remember:\s*', r'save\s+this[:\s]*',
        r'store\s+in\s+memory[:\s]*', r'lembre[-\s]se\s+de\s+', r'salve\s+isso[:\s]*',
        r'armazene[:\s]*', r'remember\s+',
    ]
    for pattern in triggers:
        cleaned = re.sub(pattern, '', text, count=1, flags=re.IGNORECASE).strip()
        if cleaned and cleaned != text.strip():
            return cleaned
    return text.strip()


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
