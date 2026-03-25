"""
skills/fs_manager.py — Filesystem Manager Skill v2
====================================================
FIXES v2:
  [BUG-1] _parse_create_dir capturava 'C:\\d' de palavras como "disco"
    Fix: regex de path absoluto exige >=2 chars após barra + evita preposições
  [BUG-2] "sim" era roteado para CONVERSATION pelo Ollama antes de chegar aqui
    Fix: a verificação de confirmação pendente foi movida para main.py
         (antes do router). fs_manager.execute() ainda funciona com "sim"
         mas main.py intercepta primeiro via has_pending_confirmation().

SECURITY MODEL:
  - TODA operação destrutiva/criativa requer confirmação explícita
  - Paths validados e normalizados antes de qualquer execução
  - Blocklist de diretórios do sistema
  - Zero PowerShell — tudo via Python pathlib/os
"""

import logging
import os
import re
import time
from pathlib import Path

from core.security.doc_processor import get_doc_processor
from core.security.dlp import get_dlp_engine

logger = logging.getLogger(__name__)
_doc_proc = get_doc_processor()
_dlp = get_dlp_engine()

# ── Diretórios do sistema bloqueados ─────────────────────────────────────────
_BLOCKED_PATHS = {
    "c:\\windows", "c:\\windows\\system32", "c:\\program files\\windows",
    "c:\\users\\default", "c:\\$recycle.bin", "c:\\programdata\\microsoft",
    "c:\\system volume information",
}

# ── Estado de confirmações pendentes {chat_id -> {action, path, expires}} ────
_pending_confirmations: dict = {}
_GLOBAL_KEY = "__global__"
_CONFIRM_TTL = 120   # segundos antes de expirar


def _store_pending(key: str, action: str, path: Path) -> None:
    _pending_confirmations[key] = {
        "action":  action,
        "path":    path,
        "expires": time.time() + _CONFIRM_TTL,
    }


def _pop_pending(key: str) -> dict | None:
    pending = _pending_confirmations.pop(key, None)
    if pending is None:
        return None
    if time.time() > pending["expires"]:
        logger.info("[FS] Confirmation for key=%s expired.", key)
        return None
    return pending


def has_pending_confirmation(key: str) -> bool:
    """
    Verifica se há uma confirmação pendente para este chat_id.
    Chamado pelo main.py ANTES do router para interceptar 'sim'/'não'.
    """
    pending = _pending_confirmations.get(key)
    if not pending:
        return False
    if time.time() > pending["expires"]:
        _pending_confirmations.pop(key, None)
        return False
    return True


def is_blocked_path(path: Path) -> bool:
    path_lower = str(path).lower().rstrip("\\")
    for blocked in _BLOCKED_PATHS:
        if path_lower == blocked or path_lower.startswith(blocked + "\\"):
            return True
    return False


def _resolve_path(raw: str, base: str = "C:\\") -> Path | None:
    """Normaliza e resolve um path. Retorna None se inválido."""
    raw = raw.strip().strip("'\"")
    if not raw:
        return None
    if not re.match(r'^[A-Za-z]:', raw):
        raw = os.path.join(base, raw)
    raw = raw.replace("/", "\\")
    try:
        return Path(raw).resolve()
    except Exception:
        return None


# Preposições e palavras a ignorar ao extrair nome de pasta
_NOISE_WORDS = re.compile(
    r'\b(cri[ae]r?|criar?|make|mkdir|uma?|um\b|a\b|o\b|de\b|pasta|folder|'
    r'diret[oó]rio|directory|chamad[ao]|called|named|nomeada?|'
    r'no\b|na\b|em\b|in\b|at\b|meu|minha|disco|local|'
    r'Jarvis|jarvis|C\b|por\s+favor|favor|please)\b',
    re.IGNORECASE,
)

def _parse_create_dir(user_input: str) -> tuple[Path | None, str]:
    """
    Extrai o path do diretório a criar.

    PIPELINE DE PARSING (3 níveis, ordem de prioridade):
      1. Path absoluto explícito com >=2 chars após barra (ex: C:\\Jarvis 2.0)
      2. Keyword 'chamado'/'called' seguido de nome + localização opcional
      3. Extração por remoção de ruído → slug do que sobrar

    FIXES v2:
      - Nível 1: exige pelo menos 2 chars após '\\' (evita 'C:\\d')
      - Nível 1: ignora matches onde o path é uma única letra ('C:\\d', 'C:\\C')
      - Nível 3: filtra tokens com 1 char e preposições isoladas
    """
    text = user_input.strip()

    # ── Nível 1: path absoluto com conteúdo real ──────────────────────────────
    # Exige [A-Za-z]: seguido de \\ e pelo menos 2 chars não-espaço
    abs_matches = re.findall(
        r'["\']?([A-Za-z]:[\\\/][^"\'<>|?*\n]{2,})["\']?',
        text
    )
    for raw in abs_matches:
        raw = raw.strip().rstrip("\\/")
        # Rejeitar paths que terminam em letra/dígito único (ex: C:\d, C:\C)
        if re.search(r'[\\\/][A-Za-z]$', raw):
            continue
        p = _resolve_path(raw)
        if p and len(p.name) > 1:
            return p, raw

    # ── Nível 2: "chamado [de] X [em C:\\Y]" ─────────────────────────────────
    m = re.search(
        r'(?:chamad[ao]|called|named|nomeada?)\s+(?:de\s+)?["\']?([^"\']+?)["\']?'
        r'(?:\s+(?:em|in|no|na|at)\s+["\']?([A-Za-z]:[\\\/][^"\'<>\n]*)["\']?)?'
        r'(?:\s*[.!?]?\s*$)',
        text, re.IGNORECASE
    )
    if m:
        folder_name = m.group(1).strip().rstrip(".").strip()
        base        = (m.group(2) or "C:\\").strip()
        p = _resolve_path(os.path.join(base, folder_name))
        if p and len(p.name) > 1:
            return p, folder_name

    # ── Nível 2b: "pasta X em C:\\Y" sem keyword chamado ─────────────────────
    m2 = re.search(
        r'\bpasta\s+["\']?([^"\'<>|?*\n]+?)["\']?\s+em\s+["\']?([A-Za-z]:[\\\/][^"\'<>\n.!?]*)["\']?',
        text, re.IGNORECASE
    )
    if m2:
        folder_name = m2.group(1).strip()
        base        = m2.group(2).strip()
        p = _resolve_path(os.path.join(base, folder_name))
        if p and len(p.name) > 1:
            return p, folder_name

    # ── Nível 3: remover ruído e usar o que sobrar ────────────────────────────
    cleaned = _NOISE_WORDS.sub(" ", text)
    # Remover pontuação final
    cleaned = re.sub(r'[.!?,;]+$', '', cleaned).strip()
    # Tokenizar — incluir tokens com espaços (ex: "Jarvis 2.0")
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]

    if tokens:
        # Juntar até 4 tokens para formar o nome da pasta
        folder_name = " ".join(tokens[:4]).strip()
        if len(folder_name) > 1:
            p = _resolve_path(os.path.join("C:\\", folder_name))
            if p:
                return p, folder_name

    return None, ""


def _do_create_dir(path: Path) -> str:
    if path.exists():
        try:
            n = len(list(path.iterdir()))
        except PermissionError:
            n = "?"
        return f"A pasta ja existe:\n`{path}`\nConteudo atual: {n} itens"
    try:
        path.mkdir(parents=True, exist_ok=True)
        logger.info("[FS] Created: %s", path)
        return (
            f"**Pasta criada com sucesso!**\n"
            f"Caminho: `{path}`\n"
            f"Local: `{path.parent}`"
        )
    except PermissionError:
        return (
            f"Sem permissao para criar em `{path.parent}`.\n"
            f"Tente executar o terminal como Administrador."
        )
    except OSError as e:
        return f"Erro ao criar pasta: {e}"


def _do_list_dir(path: Path) -> str:
    if not path.exists():
        return f"O caminho nao existe: `{path}`"
    if not path.is_dir():
        return f"`{path}` e um arquivo, nao uma pasta."
    try:
        items = sorted(path.iterdir())
        if not items:
            return f"`{path}` esta vazia."
        dirs  = [f"[DIR]  {i.name}" for i in items if i.is_dir()]
        files = [f"[FILE] {i.name}" for i in items if i.is_file()]
        lines = dirs + files
        preview = "\n".join(lines[:30])
        suffix  = f"\n...e mais {len(lines)-30} itens" if len(lines) > 30 else ""
        return f"Conteudo de `{path}` ({len(items)} itens):\n\n```\n{preview}{suffix}\n```"
    except PermissionError:
        return f"Sem permissao para listar `{path}`."
    except OSError as e:
        return f"Erro ao listar: {e}"


def _do_read_file(path: Path) -> str:
    """
    Securely read a file via the full ingestion pipeline:
    validate → AV scan → strip metadata → safe parse → DLP sanitise.
    Only PDF, TXT, MD, and CSV are permitted (max 5 MB).
    """
    result = _doc_proc.safe_parse(path)
    if not result.ok:
        return f"Leitura bloqueada por seguranca: `{result.rejected_reason}`"

    text, findings = _dlp.sanitize_text(result.text)
    dlp_note = ""
    if findings:
        labels = ", ".join(f.label for f in findings)
        dlp_note = f"\n\n_[DLP: {sum(f.count for f in findings)} item(s) redacted — {labels}]_"

    meta_note = ""
    if result.metadata_stripped:
        meta_note = f"\n_[Metadata stripped: {', '.join(result.metadata_stripped)}]_"

    preview = text[:3000]
    truncated = "\n\n…[truncado]" if len(text) > 3000 else ""
    return (
        f"**Arquivo lido com seguranca:** `{path.name}`\n"
        f"{meta_note}"
        f"\n```\n{preview}{truncated}\n```"
        f"{dlp_note}"
    )


def _do_check_exists(path: Path) -> str:
    if not path.exists():
        return f"Nao encontrado: `{path}` nao existe neste computador."
    kind = "pasta" if path.is_dir() else "arquivo"
    extra = ""
    if path.is_file():
        extra = f"\nTamanho: {path.stat().st_size:,} bytes"
    return f"Encontrado: `{path}` e um {kind} que existe.{extra}"


def execute(user_input: str, confirm_key: str = _GLOBAL_KEY) -> str:
    """
    Main entry point. Chamado pelo main.py com confirm_key=str(chat_id).

    O main.py intercepta 'sim'/'nao' ANTES do router via has_pending_confirmation().
    Se houver confirmacao pendente, o main.py chama esta funcao diretamente
    com o input original. Esta funcao tambem trata 'sim'/'nao' como fallback.
    """
    text_lower = user_input.lower().strip()

    # ── Confirmacao / Negacao (fallback caso chegue aqui) ────────────────────
    is_yes = bool(re.match(r'^(sim|yes|confirma|confirmo|ok|pode|s\b|y\b)\s*[.!]?\s*$', text_lower))
    is_no  = bool(re.match(r'^(n[aã]o|nao|no|nope|cancelar?|abort|cancel)\s*[.!]?\s*$', text_lower))

    if is_yes:
        pending = _pop_pending(confirm_key)
        if pending:
            if pending["action"] == "mkdir":
                return _do_create_dir(pending["path"])
            return f"Acao '{pending['action']}' nao reconhecida."
        return "Nenhuma acao pendente de confirmacao."

    if is_no:
        _pop_pending(confirm_key)
        return "Operacao cancelada."

    # ── Detectar tipo de operacao ─────────────────────────────────────────────
    is_check = bool(re.search(
        r'\b(verifiqu[ae]|verificar|check|existe[s]?|exists|foi criada|was created)\b',
        text_lower
    ))
    is_list = bool(re.search(
        r'\b(liste?|listar|mostrar?|show|exibir?|conte[uú]do|conteudo)\b',
        text_lower
    ))

    # ── READ: seguro via SecureDocumentProcessor ─────────────────────────────
    is_read = bool(re.search(
        r'\b(ler?|leia|read|abrir?|open|mostrar?\s+conteudo|show\s+content|'
        r'exibir?\s+arquivo|carregar?|load|parse)\b',
        text_lower
    ))
    if is_read:
        m = re.search(r'([A-Za-z]:[\\\/][^"\'<>|?*\n]+)', user_input)
        if m:
            p = _resolve_path(m.group(1))
            if p:
                return _do_read_file(p)
        return "Nao consegui identificar o arquivo a ler. Informe o caminho completo."

    # ── CHECK: nao-destrutivo, execucao direta ────────────────────────────────
    if is_check:
        m = re.search(r'([A-Za-z]:[\\\/][^"\'<>|?*\n,]+)', user_input)
        if m:
            p = _resolve_path(m.group(1))
            if p:
                return _do_check_exists(p)
        m2 = re.search(r'(?:pasta|folder)\s+["\']?([^"\'<>|?*\n,?]+)["\']?', user_input, re.IGNORECASE)
        if m2:
            name = m2.group(1).strip().rstrip("?.")
            p = _resolve_path(os.path.join("C:\\", name))
            if p:
                return _do_check_exists(p)
        return "Nao consegui identificar o caminho a verificar."

    # ── LIST: nao-destrutivo, execucao direta ─────────────────────────────────
    if is_list:
        m = re.search(r'([A-Za-z]:[\\\/][^"\'<>|?*\n]+)', user_input)
        if m:
            p = _resolve_path(m.group(1))
            if p:
                return _do_list_dir(p)
        return _do_list_dir(Path("C:\\"))

    # ── MKDIR: requer confirmacao ─────────────────────────────────────────────
    target_path, display_name = _parse_create_dir(user_input)

    if target_path is None:
        return (
            "Nao consegui identificar onde criar a pasta.\n"
            "Exemplo: _'crie a pasta MeuProjeto em C:\\\\Users\\\\Daniel'_"
        )

    if is_blocked_path(target_path):
        return (
            f"Operacao negada por seguranca.\n"
            f"Nao e permitido modificar `{target_path.parent}` — diretorio protegido."
        )

    _store_pending(confirm_key, "mkdir", target_path)

    return (
        f"**Acao requer permissao:**\n\n"
        f"Acao: Criar pasta no sistema\n"
        f"Caminho: `{target_path}`\n\n"
        f"Voce aprova? Responda **sim** para confirmar ou **nao** para cancelar."
    )
