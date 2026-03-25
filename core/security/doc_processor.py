"""
core/security/doc_processor.py — Secure Document Ingestion Pipeline
=====================================================================
All files ingested by Jarvis MUST pass through this module before their
content reaches any LLM or is written to the workspace.

SECURITY CHAIN
--------------
  Incoming file
      │
      ▼  validate_file()   — MIME type allowlist + size gate
      │
      ▼  malware_scan_hook() — AV signature check (ClamAV-ready hook)
      │
      ▼  strip_metadata()  — Remove author/creator/date metadata
      │
      ▼  safe_parse()      — Read text; no macro/script execution
      │
      ▼  Clean text → LLM / workspace

SUPPORTED FORMATS: PDF, TXT, MD, CSV  (max 5 MB)

Governed by: ADR-009-Document-Security
Depends on: ADR-004-DLP-Data-Protection (caller should DLP-sanitise output)
"""

from __future__ import annotations

import io
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_MAX_FILE_BYTES: int = 5 * 1024 * 1024  # 5 MB hard ceiling

_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md", ".csv"})

_ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/csv",
    "text/x-csv",
    "application/octet-stream",  # fallback when mimetypes can't resolve — checked against magic bytes
})

# File-header magic bytes → confirmed MIME type
_MAGIC_MAP: dict[bytes, str] = {
    b"%PDF":    "application/pdf",
    b"\xef\xbb\xbf": "text/plain",   # UTF-8 BOM
}

# PDF metadata dictionary entries to redact (binary regex)
_PDF_META_RE = re.compile(
    rb"/(?:Author|Creator|Producer|Title|Subject|Keywords|"
    rb"CreationDate|ModDate)\s*\([^)]*\)",
    re.IGNORECASE,
)

# ── ClamAV simulation: known malicious byte signatures ────────────────────────
# In production, replace _run_av_scan() body with a real ClamAV socket call.
_MALWARE_SIGNATURES: tuple[bytes, ...] = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR",  # EICAR standard test file
    b"JARVIS_MALWARE_TEST_SIGNATURE",          # Internal CI test hook
)


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class DocProcessResult:
    """
    Returned by safe_parse().  Callers check .ok before using .text.
    """
    ok: bool
    text: str = ""
    rejected_reason: str = ""
    metadata_stripped: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if self.ok:
            stripped = f" [{', '.join(self.metadata_stripped)} stripped]" if self.metadata_stripped else ""
            return f"OK — {len(self.text)} chars{stripped}"
        return f"REJECTED — {self.rejected_reason}"


# ── Core class ─────────────────────────────────────────────────────────────────

class SecureDocumentProcessor:
    """
    Stateless ingestion pipeline.  Each method can be called individually
    for testing, or call safe_parse() which runs the full chain.
    """

    # ── 1. Validation ──────────────────────────────────────────────────────

    def validate_file(self, path: Path) -> tuple[bool, str]:
        """
        Gate 1: Extension + MIME type allowlist + size limit.

        Returns (True, "") on pass; (False, reason) on reject.
        """
        if not path.exists():
            return False, f"File not found: {path}"
        if not path.is_file():
            return False, f"Path is not a file: {path}"

        # ── Size check ────────────────────────────────────────────────────
        size = path.stat().st_size
        if size == 0:
            return False, "File is empty."
        if size > _MAX_FILE_BYTES:
            mb = size / (1024 * 1024)
            return False, f"File too large: {mb:.1f} MB (limit: 5 MB)."

        # ── Extension allowlist ───────────────────────────────────────────
        ext = path.suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            return False, (
                f"Extension '{ext}' is not permitted. "
                f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
            )

        # ── MIME type via stdlib + magic-byte fallback ────────────────────
        mime, _ = mimetypes.guess_type(str(path))
        if mime is None:
            # Read first 8 bytes for magic detection
            try:
                header = path.read_bytes()[:8]
                mime = next(
                    (v for k, v in _MAGIC_MAP.items() if header.startswith(k)),
                    "application/octet-stream",
                )
            except OSError as exc:
                return False, f"Cannot read file header: {exc}"

        if mime not in _ALLOWED_MIME_TYPES:
            return False, f"MIME type '{mime}' is not permitted."

        logger.debug("[DOC] validate_file PASS: %s (%s, %d bytes)", path.name, mime, size)
        return True, ""

    # ── 2. Malware scan hook ───────────────────────────────────────────────

    def malware_scan_hook(self, raw_bytes: bytes) -> tuple[bool, str]:
        """
        Gate 2: ClamAV-ready signature scanner.

        Current implementation: byte-pattern simulation (EICAR + internal hooks).
        Production upgrade path:
            Replace _run_av_scan() body with:
                import clamd
                cd = clamd.ClamdUnixSocket()
                result = cd.instream(io.BytesIO(raw_bytes))
                return result["stream"][0] == "OK", result["stream"][1]

        Returns (True, "") if clean; (False, signature_name) if flagged.
        """
        return self._run_av_scan(raw_bytes)

    def _run_av_scan(self, data: bytes) -> tuple[bool, str]:
        for sig in _MALWARE_SIGNATURES:
            if sig in data:
                logger.warning("[DOC] Malware signature detected: %r", sig[:30])
                return False, f"Blocked — malware signature matched: {sig[:20]!r}..."
        logger.debug("[DOC] AV scan: clean (%d bytes)", len(data))
        return True, ""

    # ── 3. Metadata stripping ─────────────────────────────────────────────

    def strip_metadata(self, raw_bytes: bytes, extension: str) -> tuple[bytes, list[str]]:
        """
        Gate 3: Remove author/creator/date metadata before content reaches the LLM.

        - PDF:  strips /Author /Creator /Producer /Title /Subject
                /Keywords /CreationDate /ModDate from the PDF object stream.
        - TXT/MD/CSV: no binary metadata to strip; returns unchanged.
          (EXIF metadata only exists in image formats, which are not in the
          current allowlist — add PIL/Piexif handling here if images are added.)

        Returns (cleaned_bytes, list_of_stripped_field_names).
        """
        ext = extension.lower()
        stripped_fields: list[str] = []

        if ext == ".pdf":
            def _redact_match(m: re.Match) -> bytes:
                key = m.group(0).split(b"/")[1].split(b" ")[0].split(b"\t")[0]
                stripped_fields.append(key.decode("ascii", errors="replace"))
                return b"/" + key + b" ()"
            cleaned = _PDF_META_RE.sub(_redact_match, raw_bytes)
            if stripped_fields:
                logger.info("[DOC] PDF metadata stripped: %s", stripped_fields)
            return cleaned, stripped_fields

        # TXT / MD / CSV — plaintext, no binary metadata
        return raw_bytes, []

    # ── 4. Safe text extraction ────────────────────────────────────────────

    def _extract_text(self, raw_bytes: bytes, extension: str) -> str:
        """
        Extract plaintext without executing any embedded content.

        PDF strategy (in priority order):
          1. pdfplumber (if installed)  — most accurate
          2. pypdf (if installed)       — common fallback
          3. Regex heuristic            — stdlib-only last resort
             Extracts text between BT...ET PDF operators.
             NOT suitable for production PDF parsing — only emergency fallback.

        TXT / MD / CSV: UTF-8 decode with replacement for bad bytes.
        """
        ext = extension.lower()

        if ext == ".pdf":
            # Attempt 1: pdfplumber (pip install pdfplumber)
            try:
                import pdfplumber  # type: ignore
                with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                    pages = [p.extract_text() or "" for p in pdf.pages]
                text = "\n\n".join(pages).strip()
                logger.debug("[DOC] PDF parsed via pdfplumber (%d chars)", len(text))
                return text
            except ImportError:
                pass
            except Exception as exc:
                logger.warning("[DOC] pdfplumber failed: %s — trying pypdf", exc)

            # Attempt 2: pypdf (pip install pypdf)
            try:
                import pypdf  # type: ignore
                reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                pages = [page.extract_text() or "" for page in reader.pages]
                text = "\n\n".join(pages).strip()
                logger.debug("[DOC] PDF parsed via pypdf (%d chars)", len(text))
                return text
            except ImportError:
                pass
            except Exception as exc:
                logger.warning("[DOC] pypdf failed: %s — using regex fallback", exc)

            # Attempt 3: Regex heuristic (stdlib only — limited accuracy)
            # Extracts text between BT (Begin Text) ... ET (End Text) operators
            # and decodes parenthesised string literals.
            raw_str = raw_bytes.decode("latin-1", errors="replace")
            bt_blocks = re.findall(r"BT\s*(.*?)\s*ET", raw_str, re.DOTALL)
            strings: list[str] = []
            for block in bt_blocks:
                for s in re.findall(r"\(([^)\\]*(?:\\.[^)\\]*)*)\)", block):
                    strings.append(s.replace("\\n", "\n").replace("\\r", ""))
            text = " ".join(strings).strip()
            logger.debug("[DOC] PDF parsed via regex fallback (%d chars)", len(text))
            return text if text else "[PDF text extraction unavailable — install pdfplumber]"

        # TXT / MD / CSV
        try:
            return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.error("[DOC] Text decode failed: %s", exc)
            return ""

    # ── Full pipeline ──────────────────────────────────────────────────────

    def safe_parse(self, path: Path) -> DocProcessResult:
        """
        Run the complete ingestion chain:
          validate_file → malware_scan_hook → strip_metadata → _extract_text

        Returns DocProcessResult.  Callers MUST check .ok before using .text.
        Callers SHOULD pass .text through core.security.dlp before LLM injection.
        """
        # Gate 1: Validate
        ok, reason = self.validate_file(path)
        if not ok:
            logger.warning("[DOC] Validation failed for %s: %s", path.name, reason)
            return DocProcessResult(ok=False, rejected_reason=reason)

        # Read raw bytes once
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            return DocProcessResult(ok=False, rejected_reason=f"Read error: {exc}")

        # Gate 2: AV scan
        clean, av_reason = self.malware_scan_hook(raw_bytes)
        if not clean:
            logger.error("[DOC] AV block for %s: %s", path.name, av_reason)
            return DocProcessResult(ok=False, rejected_reason=av_reason)

        # Gate 3: Strip metadata
        sanitised_bytes, stripped = self.strip_metadata(raw_bytes, path.suffix)

        # Gate 4: Extract text (no macro/script execution)
        text = self._extract_text(sanitised_bytes, path.suffix)

        logger.info(
            "[DOC] safe_parse OK: %s — %d chars, metadata stripped: %s",
            path.name, len(text), stripped or "none",
        )
        return DocProcessResult(ok=True, text=text, metadata_stripped=stripped)


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[SecureDocumentProcessor] = None


def get_doc_processor() -> SecureDocumentProcessor:
    global _instance
    if _instance is None:
        _instance = SecureDocumentProcessor()
    return _instance
