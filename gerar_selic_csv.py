#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Converte o 'selic_mensal.json' (baixado do BCB) para 'indices_selic.csv'
no formato padrão 'indice,ano,mes,variacao_mensal'.

A variação mensal é a fração (ex.: 0,45% -> 0.0045).

Uso:
  python gerar_selic_csv.py --json selic_mensal.json --out indices_selic.csv
"""

import argparse
import sys
import json
import csv
from pathlib import Path
from decimal import Decimal, InvalidOperation
from datetime import datetime

def convert_json_to_csv(json_path: Path, csv_path: Path, indice_nome: str):
    """Lê JSON e grava CSV no formato esperado."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Erro ao ler JSON '{json_path}': {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print("❌ JSON não contém uma lista de registros.", file=sys.stderr)
        sys.exit(1)

    rows = []
    for item in data:
        try:
            # Data vem como "DD/MM/YYYY"
            dt_str = item.get("data")
            val_str = item.get("valor")
            if not dt_str or val_str is None:
                continue

            dt = datetime.strptime(dt_str, "%d/%m/%Y").date()
            # Valor é percentual (ex: "0.45"). Converter para fração (0.0045)
            taxa = Decimal(str(val_str).replace(",", "."))
            variacao_mensal = taxa / Decimal("100")

            rows.append({
                "indice": indice_nome,
                "ano": dt.year,
                "mes": dt.month,
                "variacao_mensal": f"{variacao_mensal:.8f}", # Salva como string formatada
            })
        except (ValueError, InvalidOperation, TypeError) as e:
            print(f"⚠️  Ignorando registro inválido: {item}. Erro: {e}")
            continue

    if not rows:
        print("❌ Nenhum registro válido processado do JSON.", file=sys.stderr)
        sys.exit(1)
        
    rows.sort(key=lambda r: (r["ano"], r["mes"]))

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["indice", "ano", "mes", "variacao_mensal"])
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"❌ Erro ao gravar CSV '{csv_path}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ CSV gerado: {csv_path} ({len(rows)} linhas)")
    print("Amostra (últimos 5):")
    for r in rows[-5:]:
        print(f"  {r['indice']}, {r['ano']}, {r['mes']}, {r['variacao_mensal']}")

def main():
    parser = argparse.ArgumentParser(description="Converte JSON da SELIC (BCB) para CSV.")
    parser.add_argument("--json", default="selic_mensal.json",
                        help="Arquivo JSON de entrada (default: selic_mensal.json)")
    parser.add_argument("--out", default="indices_selic.csv",
                        help="Arquivo CSV de saída (default: indices_selic.csv)")
    parser.add_argument("--indice", default="SELIC",
                        help="Nome do índice (default: SELIC)")
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"❌ Arquivo de entrada não encontrado: {json_path}", file=sys.stderr)
        print("💡 Rode `python baixar_selic.py` primeiro.", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(args.out)
    convert_json_to_csv(json_path, csv_path, args.indice)
    print("🎉 Pronto!")

if __name__ == "__main__":
    main()