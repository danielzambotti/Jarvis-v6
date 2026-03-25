"""
skills/voice_handler.py — Whisper Voice Transcription Skill
============================================================
Handles Telegram voice messages (OGG/Opus) end-to-end:

  PIPELINE:
    1. Download the voice file from Telegram's servers
    2. Save to a temp file on disk (OGG format)
    3. Convert OGG → WAV using ffmpeg (Whisper needs PCM WAV)
    4. Transcribe WAV with local openai-whisper model
    5. Clean up temp files
    6. Return the transcribed text string

  DESIGN DECISIONS:
    - Uses LOCAL Whisper (no API key, no cost, runs on CPU or GPU)
    - Model is loaded ONCE as a module-level singleton (_whisper_model)
      to avoid reloading the model weights on every message (~1-2 GB RAM)
    - Falls back to "base" model if the configured one is unavailable
    - ffmpeg must be in PATH (install: choco install ffmpeg)

  INTEGRATION:
    Transcribed text is treated as a normal text message — it is passed
    back into the main pipeline (router → skill → response) so voice
    commands work identically to typed commands.

SETUP:
    pip install openai-whisper ffmpeg-python
    choco install ffmpeg          # Windows
    # or: winget install ffmpeg
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Whisper model singleton ───────────────────────────────────────────────────
# Loaded once on first voice message. Options: tiny, base, small, medium, large
# "base" is the sweet spot: ~142MB RAM, ~1x realtime on CPU, good accuracy.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")
_whisper_model = None  # Lazy-loaded on first call


def _get_whisper_model():
    """Load and cache the Whisper model (lazy singleton)."""
    global _whisper_model
    if _whisper_model is None:
        try:
            import whisper
            logger.info("[VOICE] Loading Whisper model '%s'...", WHISPER_MODEL_SIZE)
            _whisper_model = whisper.load_model(WHISPER_MODEL_SIZE)
            logger.info("[VOICE] Whisper model loaded.")
        except ImportError:
            logger.error("[VOICE] openai-whisper not installed. Run: pip install openai-whisper")
            return None
        except Exception as e:
            logger.error("[VOICE] Failed to load Whisper model: %s", e)
            return None
    return _whisper_model


def _convert_ogg_to_wav(ogg_path: str, wav_path: str) -> bool:
    """
    Convert OGG/Opus (Telegram voice format) to 16kHz mono WAV.
    Whisper requires PCM WAV input — it cannot read OGG directly.

    Uses ffmpeg via subprocess. Returns True on success.
    """
    cmd = [
        "ffmpeg",
        "-y",                    # Overwrite output without asking
        "-i", ogg_path,          # Input: Telegram OGG/Opus file
        "-ar", "16000",          # Sample rate: 16kHz (Whisper's native rate)
        "-ac", "1",              # Channels: mono
        "-c:a", "pcm_s16le",     # Codec: 16-bit PCM
        wav_path,                # Output: WAV file
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            text=True,
        )
        if result.returncode != 0:
            logger.error("[VOICE] ffmpeg error: %s", result.stderr[:300])
            return False
        return True
    except FileNotFoundError:
        logger.error(
            "[VOICE] ffmpeg not found in PATH. "
            "Install with: winget install ffmpeg  OR  choco install ffmpeg"
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error("[VOICE] ffmpeg conversion timed out.")
        return False


def _transcribe_wav(wav_path: str) -> str:
    """
    Transcribe a WAV file using the local Whisper model.
    Returns the transcribed text, or empty string on failure.
    """
    model = _get_whisper_model()
    if model is None:
        return ""
    try:
        # language=None → auto-detect (handles PT-BR and EN automatically)
        result = model.transcribe(wav_path, language=None, fp16=False)
        text = result.get("text", "").strip()
        logger.info("[VOICE] Transcribed %d chars: %s", len(text), text[:80])
        return text
    except Exception as e:
        logger.error("[VOICE] Whisper transcription error: %s", e)
        return ""


async def download_and_transcribe(bot, file_id: str) -> str:
    """
    Full pipeline: download Telegram voice file → convert → transcribe.

    Args:
        bot:     The telegram.Bot instance (from context.bot in handlers)
        file_id: The file_id from update.message.voice.file_id

    Returns:
        Transcribed text string, or an error message starting with "❌".
    """
    # ── Step 1: Get download URL from Telegram ────────────────────────────────
    try:
        tg_file = await bot.get_file(file_id)
    except Exception as e:
        logger.error("[VOICE] Failed to get file from Telegram: %s", e)
        return "❌ Não consegui baixar o áudio do Telegram."

    # ── Step 2: Create temp directory for OGG + WAV ───────────────────────────
    with tempfile.TemporaryDirectory(prefix="jarvis_voice_") as tmpdir:
        ogg_path = os.path.join(tmpdir, "voice.ogg")
        wav_path = os.path.join(tmpdir, "voice.wav")

        # ── Step 3: Download OGG to disk ──────────────────────────────────────
        try:
            await tg_file.download_to_drive(ogg_path)
            logger.info("[VOICE] Downloaded OGG: %d bytes",
                        os.path.getsize(ogg_path))
        except Exception as e:
            logger.error("[VOICE] Download failed: %s", e)
            return "❌ Falha ao baixar o arquivo de voz."

        # ── Step 4: Convert OGG → WAV ─────────────────────────────────────────
        ok = _convert_ogg_to_wav(ogg_path, wav_path)
        if not ok:
            return (
                "❌ Não consegui converter o áudio.\n"
                "Verifique se o ffmpeg está instalado:\n"
                "`winget install ffmpeg`"
            )

        # ── Step 5: Transcribe (blocking → run in thread pool) ────────────────
        # Whisper is CPU-intensive. asyncio.to_thread() prevents blocking
        # the Telegram event loop during transcription.
        text = await asyncio.to_thread(_transcribe_wav, wav_path)

        if not text:
            return "❌ Não consegui transcrever o áudio. Tente novamente."

        return text
        # temp files are automatically deleted when TemporaryDirectory exits


def format_voice_response(transcription: str, skill_result: str) -> str:
    """
    Format the final Telegram reply for a voice message.
    Shows what was heard + the result of processing that command.
    """
    return (
        f"🎤 **Entendi:**\n_{transcription}_\n\n"
        f"{skill_result}"
    )
