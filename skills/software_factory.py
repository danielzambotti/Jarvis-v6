"""
skills/software_factory.py — The Multi-Agent Software Factory (Jeff Allan Style)
================================================================================
Implementa uma esteira de produção com 4 agentes especializados:
1. Architect -> 2. Developer -> 3. Test Master -> 4. Guardian (Reviewer)

A Gerente (execute) passa o bastão de um agente para o outro.
"""

import os
import logging
import requests
from config import OLLAMA_MODEL

from pathlib import Path
import uuid

from core.security.sandbox import run_in_sandbox
from core.security.dlp import get_dlp_engine
from core.security.guardrails import get_input_validator, SecurityException
from core.gitops.bridge import get_gitops_bridge
from core.metrics import jarvis_security_blocks_total, jarvis_dlp_redactions_total
from core.security.sdlc import get_sdlc_engine

logger = logging.getLogger(__name__)
_dlp = get_dlp_engine()
_guardrail = get_input_validator()
_sdlc = get_sdlc_engine()

OLLAMA_CHAT_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"

# ── 1. Prompts dos Agentes (As 4 Personas) ────────────────────────────────────

ARCHITECT_PROMPT = """You are the Principal Solutions Architect. 
Your job is to read the user's request and design the software architecture.
Define the file structure, the design patterns to use, and the core dependencies.
DO NOT write the full code. Write only the blueprint, architecture plan, and step-by-step logic."""

DEVELOPER_PROMPT = """You are an Elite Full-Stack Developer.
Your job is to read the Architect's Blueprint and write the ACTUAL CODE.
Follow the plan strictly. Write clean, modular, and production-ready code.
Use strict type hinting and comments. Output the raw code files."""

TEST_MASTER_PROMPT = """You are the QA Test Master.
Your job is to read the Developer's code and write comprehensive Unit Tests for it.
Think about edge cases, null values, and security flaws.
Output ONLY the test code (e.g., using pytest for Python or JUnit for Java)."""

GUARDIAN_PROMPT = """You are the Guardian (Senior Code Reviewer).
Your job is to review the Developer's code and the Test Master's tests.
1. Point out any security flaws, bugs, or bad practices.
2. If the code is good, give your "GUARDIAN SEAL OF APPROVAL".
3. Provide the FINAL, polished version of the code and tests combined in a clear Markdown format for the user."""

# ── 2. O Motor de Inferência (A Linha de Montagem) ────────────────────────────

def _call_agent(role_name: str, system_prompt: str, user_content: str) -> str:
    """Função base para acionar um LLM com uma persona específica."""
    logger.info(f"[FACTORY] ⚙️ Acionando agente: {role_name}...")
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": user_content}],
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,   # Baixa temperatura para manter o foco
            "num_predict": 2048,  # Espaço suficiente para códigos grandes
        }
    }
    
    try:
        # Timeout longo (3 minutos) porque códigos grandes demoram
        response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=180)
        response.raise_for_status()
        reply = response.json().get("message", {}).get("content", "").strip()
        logger.info(f"[FACTORY] ✅ {role_name} concluiu o trabalho ({len(reply)} chars).")
        return reply
    except Exception as e:
        logger.error(f"[FACTORY] ❌ Erro no {role_name}: {e}")
        return f"Erro no {role_name}: {e}"

# ── 3. A Gerente (Orquestradora) ──────────────────────────────────────────────

def execute(user_input: str) -> str:
    """
    Orquestra a pipeline multi-agente e salva os arquivos no workspace. [cite: 11, 12]
    """
    logger.info("[FACTORY] Iniciando a Fábrica de Software Multi-Agente...")

    # Guardrail gate — validate before any token reaches the LLM
    try:
        _guardrail.check_prompt(user_input)
    except SecurityException as exc:
        logger.error("[FACTORY] Input blocked by guardrail: %s", exc.rule)
        jarvis_security_blocks_total.labels(layer="guardrail").inc()
        return f"Input rejeitado pelo sistema de segurança (regra: `{exc.rule}`). Reformule a solicitação."

    # Estágio 1: O Arquiteto faz a planta
    architect_plan = _call_agent("Architect", ARCHITECT_PROMPT, user_input)
    
    # Estágio 2: O Desenvolvedor escreve o código (usando regras Violetio) [cite: 5, 13]
    dev_input = f"USER REQUEST: {user_input}\n\nARCHITECT PLAN:\n{architect_plan}"
    dev_code = _call_agent("Developer", DEVELOPER_PROMPT, dev_input)
    
    # Estágio 3: O QA escreve os testes [cite: 14]
    qa_input = f"DEVELOPER CODE:\n{dev_code}\n\nWrite tests for this."
    qa_tests = _call_agent("Test Master", TEST_MASTER_PROMPT, qa_input)

    # Estágio 3.5: Execução dos testes em sandbox isolado (Zero-Trust)
    # The generated code + tests are concatenated and run inside an ephemeral
    # Docker container with no network, read-only fs, and a 10-second kill timer.
    # The host process is never exposed to the LLM-generated code directly.
    sandbox_code = f"{dev_code}\n\n{qa_tests}"
    sandbox_result = run_in_sandbox(code=sandbox_code, timeout=10, extra_packages=["pytest"])
    # DLP gate 1: scrub sandbox stdout/stderr before logging
    raw_report = sandbox_result.summary()
    sandbox_report, sb_findings = _dlp.sanitize_text(raw_report)
    if sb_findings:
        logger.warning("[FACTORY] DLP removed %d item(s) from sandbox output.", len(sb_findings))
    logger.info("[FACTORY] Sandbox execution complete. Success=%s", sandbox_result.success)

    # DLP gate 2: scrub generated code before disk write
    dev_code_safe, _ = _dlp.sanitize_text(dev_code)
    qa_tests_safe, _ = _dlp.sanitize_text(qa_tests)

    # ── SALVAMENTO AUTOMÁTICO NO WORKSPACE ──
    # Files must be on disk BEFORE SDLC scans them.
    workspace = Path("/app/workspace")
    workspace.mkdir(exist_ok=True)
    try:
        (workspace / "projeto_calculadora.py").write_text(dev_code_safe, encoding="utf-8")
        (workspace / "testes_calculadora.py").write_text(qa_tests_safe, encoding="utf-8")
        logger.info("[FACTORY] Arquivos salvos em /app/workspace")
    except Exception as e:
        logger.error(f"[FACTORY] Erro ao salvar arquivos: {e}")

    # Estágio 3.7: SDLC Hard Gate (SAST + CVE + SBOM + Test Enforcement)
    # Runs AFTER sandbox + disk write, BEFORE Guardian review and GitOps.
    # Any HIGH-severity SAST finding, known CVE, or test failure aborts the pipeline.
    sdlc_report = _sdlc.run_all(workspace)
    sdlc_summary = sdlc_report.summary()
    sdlc_status = "PASS" if sdlc_report.passed else "FAIL"
    if not sdlc_report.passed:
        failed = sdlc_report.failed_gates()
        jarvis_security_blocks_total.labels(layer="sdlc").inc()
        failed_detail = "\n".join(
            f"**{r.gate}**: {r.detail}\n```\n{r.report[:400]}\n```"
            for r in failed
        )
        logger.warning("[FACTORY] SDLC gate BLOCKED: %s", sdlc_summary)
        return (
            "🚫 **SDLC Hard Gate FAILED — GitOps handover aborted.**\n\n"
            f"{failed_detail}\n\n"
            f"**Full SDLC Report:**\n```\n{sdlc_summary}\n```\n\n"
            "_Fix the issues above and re-submit the factory request._"
        )
    logger.info("[FACTORY] SDLC gate PASSED: %s", sdlc_summary)

    # Estágio 4: O Guardião revisa tudo [cite: 15]
    # The Guardian now also receives sandbox and SDLC results.
    guardian_input = (
        f"DEV CODE:\n{dev_code}\n"
        f"QA TESTS:\n{qa_tests}\n"
        f"SANDBOX TEST RESULTS (live execution in isolated container):\n{sandbox_report}\n"
        f"SDLC SECURITY SCAN:\n{sdlc_summary}\n"
        f"Review, incorporate the test results, and finalize."
    )
    final_output = _call_agent("Guardian", GUARDIAN_PROMPT, guardian_input)

    # DLP gate 3: scrub Guardian output before user delivery
    final_output, out_findings = _dlp.sanitize_text(final_output)
    if out_findings:
        logger.warning(
            "[FACTORY] DLP removed %d item(s) from Guardian output: %s",
            len(out_findings), [f.label for f in out_findings],
        )
    for finding in out_findings + sb_findings:
        jarvis_dlp_redactions_total.labels(label=finding.label).inc(finding.count)

    # ── GitOps Handover (only when sandbox PASS + Guardian approved) ──────────
    gitops_status = "skipped"
    pr_url = ""
    if sandbox_result.success and "GUARDIAN SEAL OF APPROVAL" in final_output.upper():
        try:
            run_id = str(uuid.uuid4())[:8]
            bridge = get_gitops_bridge()
            pr_body = (
                f"## Auto-generated by Jarvis v6.0 Software Factory\n\n"
                f"**Request:** {user_input[:300]}\n\n"
                f"**Sandbox:** {sandbox_status}\n"
                f"**SDLC:** {sdlc_status}\n"
                f"**DLP:** {dlp_status}\n\n"
                f"### SDLC Security Report\n```\n{sdlc_summary}\n```\n\n"
                f"### Guardian Review\n{final_output[:600]}\n\n"
                f"---\n*Review carefully before merging. This PR was opened by an autonomous agent.*"
            )
            gr = bridge.factory_handover(
                run_id=run_id,
                description=user_input[:80],
                files_to_commit=[
                    "workspace/projeto_calculadora.py",
                    "workspace/testes_calculadora.py",
                ],
                pr_body=pr_body,
            )
            if gr.success:
                gitops_status = f"PR opened: {gr.pr_url}"
                pr_url = gr.pr_url or ""
                logger.info("[FACTORY] GitOps handover complete: %s", gr.pr_url)
            else:
                gitops_status = f"PR failed: {gr.stderr[:100]}"
                logger.warning("[FACTORY] GitOps handover failed: %s", gr.stderr[:200])
        except Exception as gitops_exc:
            gitops_status = f"GitOps error: {gitops_exc}"
            logger.error("[FACTORY] GitOps exception: %s", gitops_exc)

    sandbox_status = "PASS" if sandbox_result.success else ("TIMEOUT" if sandbox_result.timed_out else "FAIL")
    dlp_status = f"{sum(f.count for f in out_findings + sb_findings)} item(s) redacted" if (out_findings or sb_findings) else "clean"
    return (
        "🏭 **Fábrica de Software Concluída!** [cite: 9]\n"
        "🏗️ **Architect**: Plano concluído. \n"
        "👨‍💻 **Developer**: Código gerado com padrões Violetio. [cite: 5, 13]\n"
        "🧪 **Test Master**: Testes unitários criados. [cite: 14]\n"
        f"🔒 **Sandbox**: Testes executados em container isolado — `{sandbox_status}`\n"
        f"🔐 **SDLC**: SAST + CVE + SBOM + Tests — `{sdlc_status}`\n"
        f"🛡️ **DLP**: Saída sanitizada — `{dlp_status}`\n"
        f"🚀 **GitOps**: `{gitops_status}`\n"
        "🛡️ **Guardian**: Revisão e aprovação final. [cite: 15]\n\n"
        "📂 *Arquivos criados em C:\\Jarvis\\workspace:*\n"
        "- `projeto_calculadora.py`\n"
        "- `testes_calculadora.py`\n\n"
        f"{final_output}"
    )