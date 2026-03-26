"""
router.py — Jarvis Intent Router (The Brain)
"""
import os
import json
import logging
import re
import requests
from config import OLLAMA_MODEL

logger = logging.getLogger(__name__)

# Lê da variável de ambiente injetada pelo Docker, ou cai pro localhost se estiver rodando por fora
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/generate"

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
                "JAVA_GITOPS", "FACTORY", "SYSTEM_STATUS", "CONVERSATION"}

_UI_KEYWORDS = re.compile(
    r'\b(click|clique?|screenshot|capturar\s+tela|digitar?|type\s+text|'
    r'press\s+key|hotkey|atalho|focus\s+window|janela|'
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

_SYSTEM_STATUS_KEYWORDS = re.compile(
    r'\b(cpu|ram|mem[oó]ria|memory|disk|disco|diagnostic[o]?|diagnóstico|'
    r'system\s*status|status\s*do\s*sistema|saúde\s*do\s*sistema|system\s*health|'
    r'docker\s*status|containers?\s*status|how\s*is\s*(my\s*)?system|'
    r'uso\s*do\s*sistema|recursos\s*do\s*sistema|system\s*resources|'
    r'monitor\s*do\s*sistema|what.{0,20}running|o\s*que\s*est[aá]\s*rodando|'
    r'processos|processes|desempenho|performance)\b',
    re.IGNORECASE,
)
def _is_system_status_intent(text: str) -> bool:
    return bool(_SYSTEM_STATUS_KEYWORDS.search(text))

_ROUTER_SYSTEM_PROMPT = """You are a strict intent classifier. Output a single JSON object: {"skill": "<SKILL>"}.

SKILLS:
- "OS_COMMAND"   : Open/launch apps, run programs, check system info (CPU/RAM/disk/processes).
- "WEB_SEARCH"   : Search the internet, find current news, weather, prices, real-time facts.
- "CREATOR"      : CREATE or WRITE a NEW file (.py, .txt, .json, etc.) with content.
- "FS_MANAGER"   : Create DIRECTORIES/FOLDERS, list folder contents, check if path exists.
- "INSPECTOR"    : Analyse, review, or read Jarvis's own source code files.
- "FACTORY"      : The user explicitly asks to build a complete project, feature, or uses words like "linha de montagem", "fábrica", "arquitetura completa".
- "CONVERSATION" : Everything else — questions, explanations, analysis, help, chitchat.

CRITICAL — FILE vs FOLDER:
  "crie uma pasta chamada X"       → FS_MANAGER   (pasta = folder/directory)
  "crie um arquivo chamado X.py"   → CREATOR      (arquivo = file with content)
  "crie um script que faz Y"       → CREATOR      (script = file with code)
  "liste o conteúdo de C:\\"       → FS_MANAGER
  "verifique se a pasta existe"    → FS_MANAGER

EXAMPLES:
  "abra o epic games"              → OS_COMMAND
  "what is bitcoin price"          → WEB_SEARCH
  "crie a pasta Jarvis 2.0 em C:"  → FS_MANAGER
  "crie um arquivo test.py"        → CREATOR
  "how do you work"                → INSPECTOR

RULES:
1. Output ONLY valid JSON. No explanation. No markdown.
2. When in doubt → CONVERSATION."""

def route(user_input: str) -> str:
    if _is_system_status_intent(user_input): return "SYSTEM_STATUS"
    if _is_factory_intent(user_input): return "FACTORY"
    if _is_ui_intent(user_input): return "UI_ACTION"
    if _is_java_gitops_intent(user_input): return "JAVA_GITOPS"
    if _is_refactor_intent(user_input): return "REFACTOR"
    if _is_install_intent(user_input): return "INSTALL"
    if _is_github_intent(user_input): return "GITHUB"
    if _is_fs_intent(user_input): return "FS_MANAGER"
    if _is_backup_intent(user_input): return "BACKUP"
    if _is_creator_intent(user_input): return "CREATOR"
    if _is_inspector_intent(user_input): return "INSPECTOR"

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Classify this message:\n\n{user_input}",
        "system": _ROUTER_SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 30,
        }
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=15)
        response.raise_for_status()
        raw_text = response.json().get("response", "").strip()
        parsed = json.loads(raw_text)
        skill = parsed.get("skill", "CONVERSATION").upper().strip()

        if skill not in VALID_SKILLS:
            return "CONVERSATION"
        return skill

    except requests.exceptions.ConnectionError:
        logger.error("[ROUTER] Ollama is not running at %s", OLLAMA_URL)
        return "CONVERSATION"
    except (json.JSONDecodeError, KeyError) as e:
        return "CONVERSATION"
    except Exception as e:
        logger.error("[ROUTER] Unexpected error: %s", e)
        return "CONVERSATION"