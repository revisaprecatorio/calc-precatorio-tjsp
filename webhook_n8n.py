import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def enviar_relatorio_precatorio(cpf: str, email: str) -> bool:
    url = "https://n8n.srv987902.hstgr.cloud/webhook/reporte-email-cpf"
    
    if not email:
        return False

    cpf_clean = ''.join(filter(str.isdigit, str(cpf)))

    payload = {
        "cpf": cpf_clean,
        "email": email
    }
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
        return False