"""
router.py — Jarvis Intent Router (The Brain)
"""
import os
import json
import logging
import re
import threading
import requests
from config import OLLAMA_MODEL
from core.metrics import (
    jarvis_router_latency_seconds,
    jarvis_router_fallbacks_total,
    jarvis_router_tier1_hits_total,
)

logger = logging.getLogger(__name__)

# Lê da variável de ambiente injetada pelo Docker, ou cai pro localhost se estiver rodando por fora
_OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_URL   = _OLLAMA_BASE + "/api/generate"

# Mini-LLM for routing — fast 1B model, overridable via env
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "llama3.2:1b")


def _ensure_router_model_bg() -> None:
    """Pull ROUTER_MODEL in a background daemon thread so it never blocks startup."""
    def _pull() -> None:
        try:
            tags   = requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=5).json()
            models = [m["name"] for m in tags.get("models", [])]
            if ROUTER_MODEL not in models:
                logger.info("[ROUTER] Pulling mini-LLM %s in background...", ROUTER_MODEL)
                requests.post(f"{_OLLAMA_BASE}/api/pull", json={"name": ROUTER_MODEL}, timeout=300)
                logger.info("[ROUTER] Mini-LLM %s ready.", ROUTER_MODEL)
        except Exception as exc:
            logger.warning("[ROUTER] Background pull failed (silent): %s", exc)

    threading.Thread(target=_pull, daemon=True).start()


_ensure_router_model_bg()  # fire-and-forget at module load

# ── Deterministic CREATOR pre-check (runs BEFORE Ollama) ─────────────────────
_CREATOR_KEYWORDS = re.compile(
    r'\b(cri[ae]r?|escreve[r]?|gere?r?|gen[ea]ra?r?|create|write|generate|make|build)\b',
    re.IGNORECASE,
)
_FILE_SIGNAL = re.compile(
    r'(\.[a-z]{2,5}\b|\b(arquivo|script|file|document[o]?)\b)',
    re.IGNORECASE,
)

def _is_creator_intent(text: str) -> bool:
    return bool(_CREATOR_KEYWORDS.search(text) and _FILE_SIGNAL.search(text))

VALID_SKILLS = {"OS_COMMAND", "WEB_SEARCH", "CREATOR", "FS_MANAGER", "BACKUP",
                "INSPECTOR", "INSTALL", "GITHUB", "UI_ACTION", "REFACTOR",
                "JAVA_GITOPS", "FACTORY", "SYSTEM_STATUS", "JARVIS_HEALTH",
                "CONVERSATION", "MEMORY_VAULT"}

# ── Hard intent contracts — single source of truth for routing string literals ──
# Import this constant in main.py to prevent silent key mismatches in SKILL_MAP.
INTENT_MEMORY_VAULT = "MEMORY_VAULT"

# Debug routing flag — set DEBUG_ROUTING=true env var for full decision tree logs
_DEBUG_ROUTING = os.environ.get("DEBUG_ROUTING", "").lower() in ("1", "true", "yes")

# ── Unified Memory Guard (Tier-1) ──────────────────────────────────────────
_MEMORY_SAVE_PATTERN = re.compile(
    r'\b(guard[ae]|salv[ae]|memoriz[ae]|lembre[-\s]se|anot[ae]).{0,40}(mem[oó]ria|senha|chave|token|credencia[is]|isso)\b',
    re.IGNORECASE
)
_MEMORY_RETRIEVE_PATTERN = re.compile(
    r'\b(what\s+do\s+you\s+remember|what\s+do\s+you\s+know\s+about|recall|'
    r'o\s+que\s+voc[êe]\s+(?:se\s+)?lembra|oq\s+vc\s+lembra|o\s+que\s+sabe\s+sobre|'
    r'voc[êe]\s+(?:se\s+)?lembra|vc\s+(?:se\s+)?lembra|'
    r'lembra\s+(?:da?|do|de)\b|se\s+lembra\s+(?:da?|do|de))\b',
    re.IGNORECASE
)
# Intentionally bare — aligns exactly with memory_vault.py _REVEAL_PATTERN.
# Credential keyword NOT required: "REVEAL github" is a valid command the skill accepts.
_MEMORY_REVEAL_PATTERN = re.compile(r'\bREVEAL\b', re.IGNORECASE)

def _is_memory_intent(text: str) -> bool:
    if _MEMORY_SAVE_PATTERN.search(text):
        logger.debug("[ROUTER] Tier-1 MEMORY_VAULT match: SAVE pattern")
        return True
    if _MEMORY_RETRIEVE_PATTERN.search(text):
        logger.debug("[ROUTER] Tier-1 MEMORY_VAULT match: RETRIEVE pattern")
        return True
    if _MEMORY_REVEAL_PATTERN.search(text):
        logger.debug("[ROUTER] Tier-1 MEMORY_VAULT match: REVEAL pattern")
        return True
    return False

_UI_KEYWORDS = re.compile(
    r'\b(click|clique?|screenshot|printscreen|capturar\s+tela|'
    r'tire\s+(?:um\s+)?print|tirar\s+(?:um\s+)?print|capture\s+screen|'
    r'digitar?|type\s+text|press\s+key|hotkey|atalho|focus\s+window|janela|'
    r'abrir?\s+(?:o\s+)?(?:app|aplicativo|programa)|ui\s+action)\b',
    re.IGNORECASE,
)
def _is_ui_intent(text: str) -> bool:
    return bool(_UI_KEYWORDS.search(text))

_JAVA_KEYWORDS = re.compile(
    r'\b(java|spring\s*boot|spring|kotlin|maven|gradle|kafka|liquibase|hibernate|jpa|junit|mockito|'
    r'microservice|microsservi[cç]o|pull\s*request|abrir?\s*pr|criar?\s*pr|open\s*pr|'
    r'endpoint\s*(?:java|spring|rest)|api\s*(?:java|spring|rest\s*api)|controller|repository\s*java|service\s*layer)\b',
    re.IGNORECASE,
)
def _is_java_gitops_intent(text: str) -> bool:
    return bool(_JAVA_KEYWORDS.search(text))

_REFACTOR_KEYWORDS = re.compile(
    r'\b(refator[ae]r?|refactor|reescrev[ae]r?|rewrite|apliqu[ae]|aplica[r]?\s+(?:as\s+)?melhorias|apply\s+(?:the\s+)?(?:fixes|improvements)|'
    r'corrij[ae]|corrig[ai]r?|fix\s+the\s+issues|otimiz[ae]r?|optimiz[ae]r?)\b',
    re.IGNORECASE,
)
_REFACTOR_TARGET = re.compile(
    r'\b([a-z_]+\.py|router|main|security|inspector|conversational|os.?controller|'
    r'web.?search|creator|reasoner|perceptor|fs.?manager|dev.?architect|github.?search|ui.?automation)\b',
    re.IGNORECASE,
)
def _is_refactor_intent(text: str) -> bool:
    return bool(_REFACTOR_KEYWORDS.search(text) and _REFACTOR_TARGET.search(text))

_GITHUB_KEYWORDS = re.compile(r'\b(github|git hub|reposit[oó]rio|repo[s]?|best practices?)\b', re.IGNORECASE)
def _is_github_intent(text: str) -> bool:
    return bool(_GITHUB_KEYWORDS.search(text))

_INSTALL_KEYWORDS = re.compile(r'\b(instal[ae]r?|install|setup|configurar?|config)\b', re.IGNORECASE)
_INSTALL_SIGNAL = re.compile(r'\b(procure|search|internet|web|execute|execut[ae]r?|run|rode|baixe?|download)\b', re.IGNORECASE)
def _is_install_intent(text: str) -> bool:
    return bool(_INSTALL_KEYWORDS.search(text) and _INSTALL_SIGNAL.search(text))

_BACKUP_KEYWORDS = re.compile(r'\b(backup|back.?up|crie? um backup|fazer backup|salvar projeto|snapshot)\b', re.IGNORECASE)
def _is_backup_intent(text: str) -> bool:
    return bool(_BACKUP_KEYWORDS.search(text))

_FS_KEYWORDS = re.compile(r'\b(pasta|folder|diret[oó]rio|directory|mkdir|liste?|list|listar|mostrar?|exibir?|verifiqu[ae]|verificar|check|existe[s]?)\b', re.IGNORECASE)
_FS_ACTION = re.compile(r'\b(cri[ae]r?|make|new|nova?|novo?|criar?|show|ver |list[ae]?|verifiqu[ae])\b', re.IGNORECASE)
def _is_fs_intent(text: str) -> bool:
    return bool(_FS_KEYWORDS.search(text) and _FS_ACTION.search(text))

_WEB_SEARCH_KEYWORDS = re.compile(
    r'\b(pesquise|procure|busque|na\s+internet|search|look\s+up|google|check\s+price)\b',
    re.IGNORECASE,
)
def _is_web_search_intent(text: str) -> bool:
    return bool(_WEB_SEARCH_KEYWORDS.search(text))

# NOTE: Generic PT question words (qual/como/quem/onde/quando) are intentionally
# NOT in Tier-1 — they are too ambiguous standalone and cause cross-intent collisions
# (JARVIS_HEALTH, INSPECTOR, SYSTEM_STATUS). They are handled correctly by Tier-2 LLM.

_CONVERSATIONAL_KEYWORDS = re.compile(
    r'^(ol[aá]|oi|bom\s+dia|boa\s+tarde|boa\s+noite)\b|'
    r'\b(quem\s+[eé]\s+voc[eê]|o\s+que\s+voc[eê]\s+pode\s+fazer|me\s+ajuda|tudo\s+bem|como\s+vai)\b',
    re.IGNORECASE,
)
def _is_conversational_intent(text: str) -> bool:
    return bool(_CONVERSATIONAL_KEYWORDS.search(text))

_INSPECTOR_KEYWORDS = re.compile(
    r'\b(analise?|analisa|analyse?|inspect|inspecion[ae]|review|revise?|read your|leia o|leia seu|ler o|ler seu|'
    r'how (do|does) (you|jarvis) work|como (você|voce) funciona|show (me )?your code|mostre? (seu|o) c[oó]digo|'
    r'seu c[oó]digo|your source|código fonte|source code|improvement|melhorias|suggest|sugest)\b',
    re.IGNORECASE,
)
def _is_inspector_intent(text: str) -> bool:
    return bool(_INSPECTOR_KEYWORDS.search(text))

_FACTORY_KEYWORDS = re.compile(
    r'\b(f[aá]brica|linha de montagem|pipeline|esteira|software factory)\b',
    re.IGNORECASE,
)
def _is_factory_intent(text: str) -> bool:
    return bool(_FACTORY_KEYWORDS.search(text))

_JARVIS_HEALTH_KEYWORDS = re.compile(
    r'\b(jarvis\s*health|health\s*check|sa[uú]de\s*do\s*jarvis|diagn[oó]stico\s*do\s*jarvis|'
    r'jarvis\s*status|jarvis\s*ok|is\s*jarvis\s*(ok|running|up)|'
    r'infrastructure\s*health|infra\s*status|check\s*services|'
    r'redis\s*(ok|status|up)|postgres\s*(ok|status|up)|ollama\s*(ok|status|up)|'
    # Bilingual additions
    r'ver\s+sa[uú]de|sa[uú]de\s+do\s+sistema|integridade\s+do\s+sistema|'
    r'system\s+status|uptime|check\s+status)\b',
    re.IGNORECASE,
)
def _is_jarvis_health_intent(text: str) -> bool:
    return bool(_JARVIS_HEALTH_KEYWORDS.search(text))

_ROUTER_SYSTEM_PROMPT = """You are a strict intent classifier for an AI assistant named Jarvis.
Output ONLY a single JSON object: {"skill": "<SKILL>"}. No explanation. No markdown. No extra keys.

## SKILLS

### SYSTEM_STATUS
Hardware and host OS monitoring ONLY.
- CPU usage, RAM/memory usage (the physical machine), disk space, running processes, Docker container status.
- Keywords: cpu, ram, disk, uptime, processes, docker ps, system load, server performance.
- "how much RAM is free?" → SYSTEM_STATUS
- "check CPU usage" → SYSTEM_STATUS
- "what is memory usage?" → SYSTEM_STATUS   ← physical RAM, NOT Jarvis memory

### MEMORY_VAULT
Jarvis's COGNITIVE / LONG-TERM MEMORY SYSTEM — storing, retrieving, or erasing facts that Jarvis should remember.
- Saving notes, passwords, preferences, facts, conversation history inside Jarvis's brain.
- Keywords: remember, recall, forget, store in memory, what do you remember, save this fact.
- "remember that my API key is XYZ" → MEMORY_VAULT
- "what do you remember about me?" → MEMORY_VAULT
- "forget what I told you about passwords" → MEMORY_VAULT
- "save this to your memory" → MEMORY_VAULT

### OS_COMMAND
Open/launch applications, run terminal commands, control the OS.
- "abra o epic games", "open Notepad", "kill process X", "run this command".

### WEB_SEARCH
Search the internet for current or real-time information.
- News, prices, weather, documentation lookups, anything that requires fetching live data.

### CREATOR
Create or write a NEW FILE with content (.py, .txt, .json, .md, .yaml, etc.).
- "create a file called X.py", "write a script that does Y", "generate a config file".

### FS_MANAGER
Manage the FILESYSTEM: create directories, list folder contents, check if paths exist.
- "create a folder called X", "list files in C:\\Projects", "does this directory exist?".

### BACKUP
Create a backup or snapshot of the project/workspace.
- "backup my project", "make a snapshot", "salvar backup".

### INSPECTOR
Analyse, review, or read Jarvis's own source code files.
- "how do you work?", "show me your code", "review router.py".

### GITHUB
Search GitHub repositories or find coding best practices on GitHub.
- "find a Python repo for X", "what are best practices for Y on GitHub".

### JAVA_GITOPS
Java/Spring Boot development tasks, Maven/Gradle builds, microservices, pull requests.
- "create a Spring Boot endpoint", "open a PR", "build the Java service".

### REFACTOR
Rewrite or improve an EXISTING Jarvis source file.
- "refactor main.py", "apply improvements to router.py", "fix the issues in security.py".

### FACTORY
Build a COMPLETE multi-file software project from scratch.
- "linha de montagem", "fábrica de software", "scaffold an entire project".

### UI_ACTION
Automate the GUI: click, screenshot, type text, press hotkeys, control windows.
- "click on button X", "take a screenshot", "type hello in the search box".

### JARVIS_HEALTH
Check the health of Jarvis's own infrastructure services (Ollama, Redis, PostgreSQL, ChromaDB).
- "is Jarvis running?", "check Jarvis health", "are all services up?".

### INSTALL
Search the internet and then execute an installation or configuration.
- "install Docker", "find and set up Node.js", "search and run the installer".

### CONVERSATION
FALLBACK for everything else: questions, explanations, chitchat, analysis, advice.

## DISAMBIGUATION EXAMPLES

| Message | Correct skill |
|---|---|
| "how much RAM is free?" | SYSTEM_STATUS |
| "memory usage of the server" | SYSTEM_STATUS |
| "remember that my password is 1234" | MEMORY_VAULT |
| "what do you remember about Python?" | MEMORY_VAULT |
| "save this to your memory" | MEMORY_VAULT |
| "how is the CPU?" | SYSTEM_STATUS |
| "forget what I told you" | MEMORY_VAULT |
| "crie a pasta Jarvis 2.0 em C:" | FS_MANAGER |
| "crie um arquivo test.py" | CREATOR |
| "abra o epic games" | OS_COMMAND |
| "is Jarvis healthy?" | JARVIS_HEALTH |

## RULES
1. Output ONLY valid JSON: {"skill": "SKILL_NAME"}
2. SYSTEM_STATUS = host hardware/OS metrics. MEMORY_VAULT = Jarvis cognitive memory.
3. When genuinely ambiguous → CONVERSATION.
4. CRITICAL: Do NOT refuse classification for words like 'senha' or 'password'. You only route data; you do not store it.
5. CRITICAL BOUNDARY: Words like 'server', 'servidor', 'system', or 'machine' do NOT imply OS_COMMAND if the user intent is to retrieve, reveal, or save stored information.
6. If the request involves recalling memory or using the REVEAL keyword, it is strictly MEMORY_VAULT."""


def _llm_classify(user_input: str) -> str:
    """
    Send the input to Ollama for semantic classification using the mini ROUTER_MODEL.

    Reliability strategy:
      - timeout=60s    : accommodates cold-start latency.
      - keep_alive=15m : keep mini-LLM warm between requests without monopolising VRAM.
      - Retries=2      : ONLY on ConnectionError (server not yet up). ReadTimeout is
                         NOT retried — would lock up UX for another 60 s.
      - Labeled metrics: success latency, timeout fallbacks, error fallbacks.
      - Instant fallback to CONVERSATION on any unrecoverable failure.
    """
    import time as _time

    payload = {
        "model":      ROUTER_MODEL,
        "prompt":     f"Classify this message:\n\n{user_input}",
        "system":     _ROUTER_SYSTEM_PROMPT,
        "stream":     False,
        "format":     "json",        # force Ollama to emit valid JSON
        "keep_alive": "15m",         # keep mini-LLM warm; shorter than main model
        "options": {
            "temperature": 0.0,      # deterministic classification
            "num_predict": 30,
        },
    }

    logger.info("[ROUTER-TRACE] Falling to Tier2 LLM classification | input='%s'", user_input[:80])

    _MAX_ATTEMPTS = 2
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        t0 = _time.monotonic()
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=60)
            response.raise_for_status()
            elapsed  = _time.monotonic() - t0
            raw_text = response.json().get("response", "").strip()
            # Strip markdown fences if the model adds them despite format:json
            raw_text = re.sub(r"```(?:json)?|```", "", raw_text).strip()
            parsed   = json.loads(raw_text)
            skill    = parsed.get("skill", "CONVERSATION").upper().strip()
            jarvis_router_latency_seconds.labels(model=ROUTER_MODEL, status="success").observe(elapsed)
            logger.info("[ROUTER-TRACE] LLM classified intent=%s (%.2fs, attempt %d)", skill, elapsed, attempt)
            logger.debug("[ROUTER] LLM classified '%s…' → %s (%.2fs, attempt %d)",
                         user_input[:40], skill, elapsed, attempt)
            return skill if skill in VALID_SKILLS else "CONVERSATION"

        except requests.exceptions.ReadTimeout:
            elapsed = _time.monotonic() - t0
            jarvis_router_fallbacks_total.labels(reason="timeout").inc()
            logger.error("[ROUTER] LLM ReadTimeout after %.1fs — falling back to CONVERSATION", elapsed)
            logger.warning("[ROUTER-TRACE] LLM fallback triggered → CONVERSATION | reason=timeout after %.1fs", elapsed)
            return "CONVERSATION"

        except requests.exceptions.ConnectionError as exc:
            elapsed = _time.monotonic() - t0
            if attempt < _MAX_ATTEMPTS:
                logger.warning("[ROUTER] ConnectionError (attempt %d/%d, %.1fs) — retrying in 2s: %s",
                               attempt, _MAX_ATTEMPTS, elapsed, exc)
                _time.sleep(2)
            else:
                jarvis_router_fallbacks_total.labels(reason="error").inc()
                logger.error("[ROUTER] Ollama unreachable after %d attempts — falling back", _MAX_ATTEMPTS)
                logger.warning("[ROUTER-TRACE] LLM fallback triggered → CONVERSATION | reason=connection_error")
                return "CONVERSATION"

        except (json.JSONDecodeError, KeyError) as exc:
            jarvis_router_fallbacks_total.labels(reason="error").inc()
            logger.warning("[ROUTER] LLM returned unparseable response: %s", exc)
            logger.warning("[ROUTER-TRACE] LLM fallback triggered → CONVERSATION | reason=parse_error: %s", exc)
            return "CONVERSATION"

        except Exception as exc:
            jarvis_router_fallbacks_total.labels(reason="error").inc()
            logger.error("[ROUTER] Unexpected error during LLM classification: %s", exc)
            logger.warning("[ROUTER-TRACE] LLM fallback triggered → CONVERSATION | reason=unexpected: %s", exc)
            return "CONVERSATION"

    return "CONVERSATION"  # unreachable; satisfies type checkers


def route(user_input: str) -> str:
    """
    Two-tier routing strategy:

    Tier 1 — Deterministic regex guards for intents that have CLEAR syntactic
              signals and NO collision risk.  Fast, zero-latency, offline-safe.

    Tier 2 — LLM semantic classifier (Ollama, temperature=0, format=json) for
              ALL ambiguous intents.  This includes SYSTEM_STATUS vs MEMORY_VAULT,
              GITHUB, INSPECTOR, CONVERSATION, and anything not caught by Tier 1.

    Removed from Tier 1 (now handled by Tier 2 LLM):
      - _is_system_status_intent  (collided with _is_memory_vault_intent on "memory")

    Unified in Tier 1 (single foolproof memory guard):
      - _is_memory_intent   (covers save, retrieval, and REVEAL — offline-safe)
    """

    # ── Tier 1: Unambiguous syntactic guards (0ms latency, no LLM) ──────────────
    if _is_memory_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="MEMORY_VAULT").inc()
        logger.info("[ROUTER-TRACE] Tier1 MATCH → MEMORY_VAULT | input='%s'", user_input[:80])
        return INTENT_MEMORY_VAULT
    if _is_jarvis_health_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="JARVIS_HEALTH").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → JARVIS_HEALTH | input='%s'", user_input[:80])
        return "JARVIS_HEALTH"
    if _is_factory_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="FACTORY").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → FACTORY | input='%s'", user_input[:80])
        return "FACTORY"
    if _is_ui_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="UI_ACTION").inc()
        logger.info("[ROUTER-TRACE] Tier1 UI_ACTION matched! input='%s'", user_input[:80])
        return "UI_ACTION"
    if _is_java_gitops_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="JAVA_GITOPS").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → JAVA_GITOPS | input='%s'", user_input[:80])
        return "JAVA_GITOPS"
    if _is_refactor_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="REFACTOR").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → REFACTOR | input='%s'", user_input[:80])
        return "REFACTOR"
    if _is_install_intent(user_input):
        # NOTE: checked before WEB_SEARCH — install is more specific (needs both keywords)
        jarvis_router_tier1_hits_total.labels(intent="INSTALL").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → INSTALL | input='%s'", user_input[:80])
        return "INSTALL"
    if _is_backup_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="BACKUP").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → BACKUP | input='%s'", user_input[:80])
        return "BACKUP"
    if _is_fs_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="FS_MANAGER").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → FS_MANAGER | input='%s'", user_input[:80])
        return "FS_MANAGER"
    if _is_creator_intent(user_input):
        jarvis_router_tier1_hits_total.labels(intent="CREATOR").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → CREATOR | input='%s'", user_input[:80])
        return "CREATOR"
    if _is_web_search_intent(user_input):
        # Placed last in Tier-1 — broad terms (search, procure) only fire after
        # more-specific intents (INSTALL, FS, CREATOR) have been ruled out.
        jarvis_router_tier1_hits_total.labels(intent="WEB_SEARCH").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → WEB_SEARCH | input='%s'", user_input[:80])
        return "WEB_SEARCH"
    if _is_conversational_intent(user_input):
        # Greetings and introductory phrases — zero overlap with skills.
        # Placed last so all actionable intents take priority.
        jarvis_router_tier1_hits_total.labels(intent="CONVERSATION").inc()
        if _DEBUG_ROUTING: logger.debug("[ROUTER-TRACE] Tier1 MATCH → CONVERSATION | input='%s'", user_input[:80])
        return "CONVERSATION"

    # ── Tier 2: LLM semantic classifier ──────────────────────────────────────
    # Handles: SYSTEM_STATUS, OS_COMMAND, GITHUB, INSPECTOR, JAVA_GITOPS (edge
    # cases), CONVERSATION, and anything not caught by Tier-1 regex guards.
    return _llm_classify(user_input)