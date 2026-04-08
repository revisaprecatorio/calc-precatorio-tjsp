#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Baixa a série histórica da SELIC ACUMULADA NO MÊS (Série 4390)
da API de Séries Temporais do Banco Central do Brasil (BCB-SGS).

- Série 4390: Taxa de juros - Selic acumulada no mês
- Unidade: % a.m.
- Salva como: selic_mensal.json

Uso:
  python baixar_selic.py
  python baixar_selic.py --saida selic_mensal.json
"""

import argparse
import sys
from pathlib import Path
from datetime import date
import requests
from time import sleep

# Série correta para uso mensal
API_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados"

TARGET_FILENAME = "selic_mensal.json"

HOJE = date.today()

# Mantive a lógica em blocos longos para evitar problemas de janela de consulta
INTERVALOS = [
    ("01/01/1986", "31/12/1989"),
    ("01/01/1990", "31/12/1999"),
    ("01/01/2000", "31/12/2009"),
    ("01/01/2010", "31/12/2019"),
    ("01/01/2020", HOJE.strftime("%d/%m/%Y")),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

def download_selic_json_chunks(dest_path: Path):
    """Baixa os dados da API em blocos e salva em JSON."""
    todos_os_dados = []

    print(f"🌐 Consultando API do BCB-SGS para SELIC mensal (Série 4390) em {len(INTERVALOS)} partes...")

    try:
        for i, (inicio, fim) in enumerate(INTERVALOS):
            print(f"   - Parte {i+1}/{len(INTERVALOS)}: Buscando de {inicio} até {fim}...")

            params = {
                "formato": "json",
                "dataInicial": inicio,
                "dataFinal": fim,
            }

            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()

            dados_parte = r.json()
            if not isinstance(dados_parte, list):
                print(f"❌ Resposta inesperada da API na parte {i+1}.", file=sys.stderr)
                sys.exit(1)

            todos_os_dados.extend(dados_parte)
            sleep(0.3)

    except requests.RequestException as e:
        print(f"❌ Erro ao baixar dados do BCB: {e}", file=sys.stderr)
        sys.exit(1)

    if not todos_os_dados:
        print("❌ Nenhum dado foi baixado.", file=sys.stderr)
        sys.exit(1)

    # Ordena por data
    try:
        from datetime import datetime
        todos_os_dados.sort(key=lambda x: datetime.strptime(x["data"], "%d/%m/%Y"))
    except Exception as e:
        print(f"⚠️ Não foi possível ordenar por data ({e}). Salvando como veio.")

    try:
        import json
        dest_path.write_text(json.dumps(todos_os_dados, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"❌ Erro ao salvar JSON '{dest_path}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Download concluído: {dest_path} ({len(todos_os_dados)} registros)")
    if todos_os_dados:
        print(f"   - Primeiro: {todos_os_dados[0]['data']} -> {todos_os_dados[0]['valor']}")
        print(f"   - Último..: {todos_os_dados[-1]['data']} -> {todos_os_dados[-1]['valor']}")

def main():
    parser = argparse.ArgumentParser(description="Baixa SELIC acumulada no mês (Série 4390) do BCB.")
    parser.add_argument(
        "--saida",
        "-o",
        default=TARGET_FILENAME,
        help=f"Arquivo JSON de saída (default: {TARGET_FILENAME})"
    )
    args = parser.parse_args()

    out_path = Path(args.saida).expanduser().resolve()
    download_selic_json_chunks(out_path)
    print("🎉 Pronto!")

if __name__ == "__main__":
    main()