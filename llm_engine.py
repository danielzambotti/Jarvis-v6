import requests
from config import OLLAMA_URL, OLLAMA_MODEL
from skills import os_controller, conversational

def classify_intent(prompt: str) -> str:
    """O Roteador: Decide qual Skill usar com base no pedido do usuário."""
    system_prompt = """Analise o pedido do usuário. 
Ele quer executar uma ação física no computador (como abrir um app, pesquisar na web, desligar, abrir arquivos) OU ele quer apenas conversar/tirar uma dúvida teórica?
Responda APENAS com a palavra 'OS' para ação no computador, ou 'CHAT' para conversa/dúvida. NADA MAIS."""
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\nPedido: {prompt}",
        "stream": False,
        "options": {"temperature": 0.0} # Temp 0.0 para ser extremamente lógico na decisão
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        result = response.json().get("response", "").strip().upper()
        # Se a IA disser OS, retorna OS. Se não, assume que é conversa (CHAT)
        return "OS" if "OS" in result else "CHAT"
    except Exception:
        return "CHAT" # Fallback seguro

def processar_comando(prompt: str) -> dict:
    """Aciona a Skill correta e retorna a resposta."""
    route = classify_intent(prompt)
    print(f"[*] Roteador escolheu a Skill: {route}")
    
    if route == "OS":
        response = os_controller.execute(prompt)
    else:
        response = conversational.execute(prompt)
        
    return {"route": route, "response": response}