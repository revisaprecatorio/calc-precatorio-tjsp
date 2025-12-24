import requests

def enviar_relatorio_precatorio(cpf: str, email: str) -> dict:
    """
    Envia solicitação de relatório diagnóstico de precatórios.
    
    Args:
        cpf: CPF do cliente (apenas números, 11 dígitos)
        email: Email de destino para o relatório
    
    Returns:
        dict: Resposta do webhook
    """
    url = "https://n8n.srv987902.hstgr.cloud/webhook/reporte-email-cpf"
    
    payload = {
        "cpf": cpf,
        "email": email
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    
    return response.json()


# Exemplo de uso
if __name__ == "_main_":
    resultado = enviar_relatorio_precatorio(
        cpf="28455260831",
        email="persival.balleste@gmail.com"
    )
    print(resultado)