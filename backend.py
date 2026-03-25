import ollama
from ollama import Client
import json
import config
import skills
import warnings
import re

warnings.simplefilter("ignore", ResourceWarning)
client = Client(host='http://localhost:11434')
historico = []

def limpar_resposta_visual(texto):
    # Remove qualquer bloco JSON que tenha vazado na resposta final
    texto_limpo = re.sub(r'\{"tool":.*?\}', '', texto, flags=re.DOTALL)
    return texto_limpo.strip()

def extrair_comando_json(texto):
    match = re.search(r'\{"tool":\s*".*?",\s*"args":\s*".*?"\}', texto.replace('\n', ' '), re.DOTALL)
    if match:
        return match.group(0)
    return None

def processar_comando(texto_usuario):
    global historico
    system_prompt = config.get_system_prompt()
    msgs = [{'role': 'system', 'content': system_prompt}] + historico[-4:] + [{'role': 'user', 'content': texto_usuario}]

    try:
        # 1. PENSAMENTO
        response = client.chat(model=config.MODEL_NAME, messages=msgs)
        conteudo_bruto = response['message']['content']
        
        json_comando = extrair_comando_json(conteudo_bruto)
        
        if json_comando:
            try:
                dados = json.loads(json_comando)
                ferramenta = dados["tool"]
                args = dados.get("args")
                
                print(f"🔧 Skill acionada: {ferramenta} -> {args}") # Só no terminal
                
                if ferramenta in skills.MAPA_DE_SKILLS:
                    # Executa e pega o texto COM FONTES
                    resultado_pesquisa = skills.MAPA_DE_SKILLS[ferramenta](args)
                    
                    # 2. SÍNTESE COM FONTES
                    prompt_final = (
                        f"CONTEXTO: O usuário perguntou '{texto_usuario}'.\n"
                        f"DADOS REAIS DA WEB:\n{resultado_pesquisa}\n"
                        f"ORDEM: Responda a pergunta citando a FONTE e o LINK de onde você tirou a informação. "
                        f"Se houver valores (dinheiro), copie exatamente como está na fonte. Não converta nada."
                    )
                    
                    resp_final = client.chat(model=config.MODEL_NAME, messages=[{'role': 'user', 'content': prompt_final}])
                    texto_final = resp_final['message']['content']
                    
                    # Limpeza final de segurança
                    texto_final = limpar_resposta_visual(texto_final)
                    
                    historico.append({'role': 'user', 'content': texto_usuario})
                    historico.append({'role': 'assistant', 'content': texto_final})
                    return texto_final
                    
            except Exception as e:
                print(f"Erro ao processar ferramenta: {e}")

        # Conversa normal
        texto_limpo = limpar_resposta_visual(conteudo_bruto)
        historico.append({'role': 'user', 'content': texto_usuario})
        historico.append({'role': 'assistant', 'content': texto_limpo})
        return texto_limpo

    except Exception as e:
        return f"Erro crítico: {e}"