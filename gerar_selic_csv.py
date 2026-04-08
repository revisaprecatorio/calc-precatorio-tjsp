#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Converte o JSON da SELIC acumulada no mês (Série 4390 do BCB)
para 'indices_selic.csv' no formato:

  indice,ano,mes,variacao_mensal

Onde:
- o valor da API já vem em % a.m.
- então basta converter percentual para fração
  ex.: "1.16" -> 0.0116

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
            dt_str = item.get("data")
            val_str = item.get("valor")

            if not dt_str or val_str is None:
                continue

            dt = datetime.strptime(dt_str, "%d/%m/%Y").date()

            # Série 4390 já vem em % ao mês
            #taxa_percentual_mensal = Decimal(str(val_str).replace(",", "."))
            #variacao_mensal = taxa_percentual_mensal / Decimal("100")
            taxa_anual = Decimal(str(val_str).replace(",", ".")) / Decimal("100")

            # converter anual -> mensal equivalente
            variacao_mensal = (Decimal("1") + taxa_anual) ** (Decimal("1") / Decimal("12")) - Decimal("1")
            
            rows.append({
                "indice": indice_nome,
                "ano": dt.year,
                "mes": dt.month,
                "variacao_mensal": f"{variacao_mensal:.8f}",
            })

        except (ValueError, InvalidOperation, TypeError) as e:
            print(f"⚠️ Ignorando registro inválido: {item}. Erro: {e}")
            continue

    if not rows:
        print("❌ Nenhum registro válido processado do JSON.", file=sys.stderr)
        sys.exit(1)

    rows.sort(key=lambda r: (r["ano"], r["mes"]))

    # remove duplicidade por ano/mês, mantendo o último
    dedup = {}
    for r in rows:
        dedup[(r["ano"], r["mes"])] = r

    rows_final = list(dedup.values())
    rows_final.sort(key=lambda r: (r["ano"], r["mes"]))

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["indice", "ano", "mes", "variacao_mensal"]
            )
            writer.writeheader()
            writer.writerows(rows_final)
    except Exception as e:
        print(f"❌ Erro ao gravar CSV '{csv_path}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ CSV gerado: {csv_path} ({len(rows_final)} linhas)")
    print("Amostra (últimos 5):")
    for r in rows_final[-5:]:
        print(f"  {r['indice']}, {r['ano']}, {r['mes']}, {r['variacao_mensal']}")

def main():
    parser = argparse.ArgumentParser(
        description="Converte JSON da SELIC acumulada no mês (Série 4390) para CSV."
    )
    parser.add_argument(
        "--json",
        default="selic_mensal.json",
        help="Arquivo JSON de entrada (default: selic_mensal.json)"
    )
    parser.add_argument(
        "--out",
        default="indices_selic.csv",
        help="Arquivo CSV de saída (default: indices_selic.csv)"
    )
    parser.add_argument(
        "--indice",
        default="SELIC",
        help="Nome do índice (default: SELIC)"
    )
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