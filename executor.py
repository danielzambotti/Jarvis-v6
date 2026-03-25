import subprocess
from security import is_safe_command

def execute_os_command(cmd: str) -> str:
    """Executa o comando no Windows de forma isolada."""
    if not is_safe_command(cmd):
        return "[ALERTA DE SEGURANÇA] Comando bloqueado pela Blacklist."
    
    try:
        # Roda o comando e captura a saída
        result = subprocess.run(
            ["powershell", "-Command", cmd], 
            capture_output=True, 
            text=True, 
            timeout=30 # Mata o comando se demorar mais de 30s
        )
        
        output = result.stdout if result.returncode == 0 else result.stderr
        return output.strip() if output else "Comando executado sem retorno visual."
    
    except subprocess.TimeoutExpired:
        return "Timeout: O comando demorou muito para responder."
    except Exception as e:
        return f"Erro de execução: {e}"