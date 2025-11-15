#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Baixa a série histórica da SELIC (acumulada mensal) da API
de Séries Temporais do Banco Central do Brasil (BCB-SGS).

- Série 1178: Taxa SELIC acumulada no mês
- Salva como: selic_mensal.json

Uso:
  python baixar_selic.py
"""

import argparse
import sys
from pathlib import Path
from datetime import date
import requests
from time import sleep

# URL da API do BCB-SGS para a série 1178 (SELIC acumulada no mês)
API_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.1178/dados"

TARGET_FILENAME = "selic_mensal.json"

# CORREÇÃO v6:
# O API tem um limite de 10 anos por consulta.
# Vamos fazer várias consultas em "pedaços" de 10 anos e juntar os resultados.
HOJE = date.today()
ANO_ATUAL = HOJE.year

# Definir os intervalos de 10 anos (ou menos)
# (Início da série: 1980)
INTERVALOS = [
    ("01/01/1980", "31/12/1989"),
    ("01/01/1990", "31/12/1999"),
    ("01/01/2000", "31/12/2009"),
    ("01/01/2010", "31/12/2019"),
    # O último intervalo vai de 2020 até à data de hoje
    ("01/01/2020", HOJE.strftime("%d/%m/%Y")), 
]

# Usar um User-Agent de navegador para garantir
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def download_selic_json_chunks(dest_path: Path):
    """Baixa os dados da API em pedaços de 10 anos e salva em JSON."""
    
    todos_os_dados = []
    
    print(f"🌐 Consultando API do BCB-SGS para SELIC (Série 1178) em {len(INTERVALOS)} partes...")

    try:
        for i, (inicio, fim) in enumerate(INTERVALOS):
            print(f"   - Parte {i+1}/{len(INTERVALOS)}: Buscando de {inicio} até {fim}...")
            
            params = {
                "formato": "json",
                "dataInicial": inicio,
                "dataFinal": fim
            }
            
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            
            dados_parte = r.json()
            if not isinstance(dados_parte, list):
                print(f"❌ Resposta da API (parte {i+1}) inesperada. Não é uma lista.", file=sys.stderr)
                sys.exit(1)
                
            todos_os_dados.extend(dados_parte)
            sleep(0.5) # Pausa educada entre as chamadas

    except requests.RequestException as e:
        print(f"❌ Erro ao baixar dados do BCB (na parte de {inicio} a {fim}): {e}", file=sys.stderr)
        sys.exit(1)

    if not todos_os_dados:
        print("❌ Nenhum dado foi baixado.", file=sys.stderr)
        sys.exit(1)

    # Ordenar por data para garantir (embora já deva vir ordenado)
    # A API retorna 'data' como 'dd/mm/AAAA'
    try:
        todos_os_dados.sort(key=lambda x: datetime.strptime(x['data'], '%d/%m/%Y'))
    except Exception as e:
        print(f"⚠️  Aviso: Não foi possível re-ordenar os dados por data ({e}). Salvando como recebido.")

    try:
        # Salva a lista completa como JSON
        import json
        dest_path.write_text(json.dumps(todos_os_dados, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"❌ Erro ao salvar o arquivo JSON: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Download concluído: {dest_path} ({len(todos_os_dados)} registros totais)")
    if todos_os_dados:
        print(f"   - Primeiro registo (mais antigo): {todos_os_dados[0]['data']} - {todos_os_dados[0]['valor']}%")
        print(f"   - Último registo (mais recente): {todos_os_dados[-1]['data']} - {todos_os_dados[-1]['valor']}%")


def main():
    parser = argparse.ArgumentParser(description="Baixa SELIC (Série 1178) da API do BCB.")
    parser.add_argument("--saida", "-o", default=TARGET_FILENAME,
                        help=f"Arquivo JSON de saída (default: {TARGET_FILENAME})")
    args = parser.parse_args()

    # Importação necessária para o sort
    from datetime import datetime
    
    out_path = Path(args.saida).expanduser().resolve()
    download_selic_json_chunks(out_path)
    print("🎉 Pronto!")

if __name__ == "__main__":
    main()