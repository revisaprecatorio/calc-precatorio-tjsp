import requests
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def enviar_relatorio_precatorio(cpf: str, email: str) -> tuple[bool, str]:
    url = "https://n8n.srv987902.hstgr.cloud/webhook/reporte-email-cpf"
    
    if not email:
        return False, "E-mail vazio ou nulo"

    cpf_clean = ''.join(filter(str.isdigit, str(cpf)))
    payload = {"cpf": cpf_clean, "email": email}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        # Se for 200, retorna Sucesso
        if response.status_code == 200:
            return True, "OK"
        else:
            # Retorna o código e o texto do erro (ex: 404 Not Found ou 500 Internal Error)
            return False, f"Erro {response.status_code}: {response.text[:100]}"
    except Exception as e:
        return False, f"Exceção: {str(e)}"
    
    return False

# Teste direto
if __name__ == "__main__":
    enviar_relatorio_precatorio("123.456.789-00", "marcos.kako01@gmail.com")