"""
skills/tech_lead.py — The Enterprise Coder (Violetio Implementation)
====================================================================
Esta skill intercepta pedidos de criação de código avançado, detecta
a tecnologia desejada e injeta os System Prompts hiper-restritos da
biblioteca Violetio.

Ele roda com baixa temperatura para garantir precisão absoluta e
zero "alucinação" arquitetural.
"""

import os
import logging
import requests
from config import OLLAMA_MODEL
from skills.violetio_prompts import get_system_prompt

logger = logging.getLogger(__name__)

# Conexão com o motor do Ollama no Docker
OLLAMA_CHAT_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"

def _detect_technology(user_input: str) -> str:
    """
    Lê o pedido do usuário e descobre qual é a linguagem/framework alvo.
    Isso define qual Mandamento (System Prompt) será usado.
    """
    text = user_input.lower()
    
    # Adicione mais mapeamentos conforme a stack for crescendo
    if "spring" in text or "java" in text or "maven" in text:
        return "java"
    elif "python" in text or "fastapi" in text or "flask" in text or "django" in text:
        return "python"
    
    return "general"  # Fallback seguro

def execute(user_input: str) -> str:
    """
    Gera o código de Produção usando as regras da Violetio.
    """
    tech = _detect_technology(user_input)
    system_prompt = get_system_prompt(tech)
    
    logger.info(f"[TECH_LEAD] Linguagem detectada: '{tech.upper()}'. Injetando regras Enterprise.")

    messages = [
        {"role": "user", "content": user_input}
    ]

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,   # Baixa criatividade, alta precisão técnica
            "num_predict": 2048,  # Permite que ele escreva arquivos longos sem cortar (até 2k tokens)
        }
    }

    try:
        response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=120) # 2 minutos de timeout para códigos complexos
        response.raise_for_status()

        data = response.json()
        reply = (data.get("message") or {}).get("content", "").strip()

        if not reply:
            return "❌ Falha crítica: O modelo não retornou nenhum código."

        logger.info("[TECH_LEAD] Código gerado com sucesso.")
        return reply

    except requests.exceptions.ConnectionError:
        logger.error("[TECH_LEAD] Ollama inalcançável.")
        return "❌ Erro de conexão com o Cérebro LLM (Ollama não encontrado)."
    except requests.exceptions.Timeout:
        logger.error("[TECH_LEAD] Timeout gerando código.")
        return "⏱️ A geração de código demorou muito (Timeout de 2 min). Tente pedir um escopo menor."
    except Exception as e:
        logger.error("[TECH_LEAD] Erro interno: %s", e)
        return f"❌ Erro interno no Tech Lead: {e}"