"""
tests/test_router.py — Unit tests for router.py intent classification.

Covers only the deterministic pre-check layer (regex-based) — no Ollama calls.
Uses monkeypatching to short-circuit the LLM fallback so tests are fast and offline.
"""
import sys
from pathlib import Path
import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _route_no_llm(text: str, monkeypatch) -> str:
    """
    Call router.route() with the Ollama fallback disabled.
    If no regex pre-check fires, returns 'CONVERSATION' (the LLM default).
    """
    import router
    monkeypatch.setattr(router, "_llm_classify", lambda x: "CONVERSATION", raising=False)
    # Patch requests.post so the LLM path never actually calls Ollama
    import unittest.mock as mock
    with mock.patch("router.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {"response": '{"skill":"CONVERSATION"}'}
        mock_post.return_value.raise_for_status = lambda: None
        return router.route(text)


# ── CREATOR tests ─────────────────────────────────────────────────────────────

class TestCreatorIntent:
    def test_create_python_file(self, monkeypatch):
        result = _route_no_llm("crie um arquivo chamado analise.py", monkeypatch)
        assert result == "CREATOR"

    def test_write_json_file(self, monkeypatch):
        result = _route_no_llm("write a config.json with default settings", monkeypatch)
        assert result == "CREATOR"

    def test_generate_script(self, monkeypatch):
        result = _route_no_llm("generate a script called processor.py", monkeypatch)
        assert result == "CREATOR"

    def test_not_creator_without_file_signal(self, monkeypatch):
        # "create" alone without a file extension should NOT go to CREATOR
        result = _route_no_llm("create a new folder called test", monkeypatch)
        assert result != "CREATOR"


# ── SYSTEM_STATUS tests ───────────────────────────────────────────────────────

class TestSystemStatusIntent:
    def test_cpu_query(self, monkeypatch):
        result = _route_no_llm("what is my cpu usage?", monkeypatch)
        assert result == "SYSTEM_STATUS"

    def test_ram_query(self, monkeypatch):
        result = _route_no_llm("how much ram is being used", monkeypatch)
        assert result == "SYSTEM_STATUS"

    def test_docker_status(self, monkeypatch):
        result = _route_no_llm("docker status", monkeypatch)
        assert result == "SYSTEM_STATUS"

    def test_diagnostic(self, monkeypatch):
        result = _route_no_llm("run a diagnostic", monkeypatch)
        assert result == "SYSTEM_STATUS"

    def test_pt_br_memory(self, monkeypatch):
        result = _route_no_llm("quanto de memória está sendo usado", monkeypatch)
        assert result == "SYSTEM_STATUS"


# ── JARVIS_HEALTH tests ───────────────────────────────────────────────────────

class TestJarvisHealthIntent:
    def test_jarvis_health(self, monkeypatch):
        result = _route_no_llm("jarvis health", monkeypatch)
        assert result == "JARVIS_HEALTH"

    def test_is_jarvis_ok(self, monkeypatch):
        result = _route_no_llm("is jarvis ok?", monkeypatch)
        assert result == "JARVIS_HEALTH"

    def test_pt_br_diagnostico(self, monkeypatch):
        result = _route_no_llm("diagnóstico do jarvis", monkeypatch)
        assert result == "JARVIS_HEALTH"

    def test_redis_status(self, monkeypatch):
        result = _route_no_llm("redis status", monkeypatch)
        assert result == "JARVIS_HEALTH"

    def test_ollama_up(self, monkeypatch):
        result = _route_no_llm("ollama up?", monkeypatch)
        assert result == "JARVIS_HEALTH"

    def test_jarvis_health_beats_system_status(self, monkeypatch):
        # JARVIS_HEALTH pre-check fires before SYSTEM_STATUS
        result = _route_no_llm("jarvis health check", monkeypatch)
        assert result == "JARVIS_HEALTH"


# ── BACKUP tests ──────────────────────────────────────────────────────────────

class TestBackupIntent:
    def test_backup_keyword(self, monkeypatch):
        result = _route_no_llm("fazer backup do projeto", monkeypatch)
        assert result == "BACKUP"

    def test_snapshot(self, monkeypatch):
        result = _route_no_llm("create a snapshot now", monkeypatch)
        assert result == "BACKUP"


# ── CONVERSATION fallback ─────────────────────────────────────────────────────

class TestConversationFallback:
    def test_general_question_falls_to_conversation(self, monkeypatch):
        result = _route_no_llm("what is the meaning of life?", monkeypatch)
        assert result == "CONVERSATION"

    def test_greeting_falls_to_conversation(self, monkeypatch):
        result = _route_no_llm("hello jarvis, how are you?", monkeypatch)
        assert result == "CONVERSATION"
