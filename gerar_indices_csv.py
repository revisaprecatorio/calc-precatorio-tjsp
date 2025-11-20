# -*- coding: utf-8 -*-
"""
Converte planilha do IPCA-E (ou similar) para 'indices.csv' no formato:
  indice,ano,mes,variacao_mensal
onde variacao_mensal é FRAÇÃO mensal (ex.: 0,21% -> 0.0021).
"""

import argparse
import sys
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import List, Tuple
import io
import pandas as pd
import numpy as np 
from datetime import datetime
import json
# ... outros imports
# -----------------------------
# Mapas e helpers
# -----------------------------

PT_MONTHS = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4,
    "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9,
    "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
}

def norm_str(x) -> str:
    if x is None: return ""
    s = str(x).replace("\n", " ").replace("\r", " ").replace("\xa0", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip().upper()

def to_int_safe(x):
    try:
        if x is None or pd.isna(x): return None
        s = str(x).strip()
        if s.endswith(".0"): s = s[:-2]
        if not s: return None
        return int(float(s))
    except:
        return None

def parse_percent_to_fraction(x):
    """
    Converte '0,21', '0.21', '39.17' para FRAÇÃO Decimal.
    Divide SEMPRE por 100.
    """
    if x is None or pd.isna(x):
        return None
    
    s = str(x).strip()
    # Se for string "None" ou vazia
    if s.lower() == "none" or s == "":
        return None

    # Limpeza
    s = s.replace("–", "-").replace("−", "-").replace("%", "").replace(" ", "")
    
    # Detecção de formato numérico PT-BR vs EN-US
    # Se tem vírgula, assume PT-BR (troca , por .)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    
    try:
        val = Decimal(s)
        return val / Decimal("100")
    except InvalidOperation:
        return None

def month_to_number(x):
    if pd.isna(x): return None
    s = norm_str(x)
    for ch in [".", ",", ";", ":"]: s = s.replace(ch, "")
    if s in PT_MONTHS: return PT_MONTHS[s]
    n = to_int_safe(s)
    if n and 1 <= n <= 12: return n
    return None

# -----------------------------
# Lógica Inteligente de Leitura
# -----------------------------

def read_excel_smart(xlsx_path: Path, sheet_name):
    """
    Lê o Excel tentando detectar automaticamente a linha de cabeçalho correta.
    Procura pela linha que contém 'ANO' e 'MÊS'.
    """
    print(f"[INFO] Analisando estrutura do arquivo: {xlsx_path.name}")
    
    # 1. Ler as primeiras 20 linhas sem cabeçalho para inspecionar
    engine = 'openpyxl' if xlsx_path.suffix.lower() == '.xlsx' else None # Deixa pandas decidir para .xls (xlrd)
    
    try:
        df_raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None, nrows=20, engine=engine)
    except ValueError as e:
        # Fallback para sheet index 0 se o nome falhar
        print(f"[AVISO] Falha ao abrir aba '{sheet_name}'. Tentando primeira aba.")
        df_raw = pd.read_excel(xlsx_path, sheet_name=0, header=None, nrows=20, engine=engine)

    header_row_idx = None
    
    # 2. Procurar linha com "ANO" e "MÊS"
    for idx, row in df_raw.iterrows():
        row_str = " ".join([norm_str(c) for c in row.values])
        if "ANO" in row_str and ("MES" in row_str or "MÊS" in row_str):
            header_row_idx = idx
            print(f"[INFO] Cabeçalho detectado na linha {idx + 1} (Excel row {idx + 1})")
            break
    
    if header_row_idx is None:
        print("[AVISO] Não foi possível detectar 'ANO'/'MÊS' automaticamente. Usando linha 0.")
        header_row_idx = 0

    # 3. Recarregar com o cabeçalho correto
    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=header_row_idx, engine=engine)
    
    # Normaliza nomes das colunas para busca
    df.columns = [norm_str(c) for c in df.columns]
    
    return df

def find_column(df, candidates: List[str]):
    """Retorna o nome real da coluna que corresponde a um dos candidatos."""
    cols = list(df.columns)
    
    # 1. Busca exata/parcial baseada na lista de candidatos
    for cand in candidates:
        for c in cols:
            if cand in c: # Busca se o candidato está dentro do nome da coluna
                return c
    
    # 2. Busca de "Desespero": Procura coluna que tenha "VAR" e "%" ao mesmo tempo
    # Isso resolve casos bizarros como "VARIAÇÃ0 (%)" onde o 0 é um zero.
    for c in cols:
        if "VAR" in c and "%" in c:
            return c
            
    return None

# -----------------------------
# Pipeline Principal
# -----------------------------

def build_indices_csv(xlsx_path, sheet_name, out_csv, indice_nome, debug=False, **kwargs):
    
    path_obj = Path(xlsx_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {xlsx_path}")

    # 1. Leitura Inteligente
    df = read_excel_smart(path_obj, sheet_name)

    # 2. Detecção de Colunas
    # Prioridade para nomes padrão IBGE
    col_ano = find_column(df, ["ANO"])
    col_mes = find_column(df, ["MES", "MÊS"])
    
    # Lista de candidatos para Variação (incluindo erros de digitação comuns do IBGE)
    var_candidates = [
        "NO MÊS", 
        "VARIAÇÃO MENSAL", 
        "VARIAÇÃO (%)", 
        "VARIAÇÃO", 
        "VARIAÇÃ0", # Erro com zero
        "VARIAÇAO", # Sem til
        "VARIAÇ"    # Parcial
    ]
    col_var = find_column(df, var_candidates)

    if debug:
        print(f"[DEBUG] Colunas encontradas no DF: {list(df.columns)}")
        print(f"[DEBUG] Mapeamento: ANO='{col_ano}', MÊS='{col_mes}', VAR='{col_var}'")

    if not (col_ano and col_mes and col_var):
        raise RuntimeError(f"Colunas essenciais não encontradas. Detectado: Ano={col_ano}, Mês={col_mes}, Var={col_var}")

    # 3. Extração e Limpeza
    rows = []
    
    # Preencher anos vazios (merged cells)
    df[col_ano] = df[col_ano].replace(r'^\s*$', np.nan, regex=True).ffill()

    for idx, r in df.iterrows():
        ano = to_int_safe(r[col_ano])
        mes = month_to_number(r[col_mes])
        raw_var = r[col_var]
        
        # Parse do valor
        val = parse_percent_to_fraction(raw_var)
        
        if ano and mes:
            if val is not None:
                rows.append((ano, mes, val))
            elif debug and idx < 15: 
                pass 

    if not rows:
        print("\n[ERRO CRÍTICO] Nenhuma linha válida extraída.")
        print("Amostra dos dados brutos (5 primeiras linhas):")
        print(df[[col_ano, col_mes, col_var]].head())
        raise RuntimeError("Falha na extração de dados.")

    # 4. Ordenação e Salvamento
    rows.sort(key=lambda x: (x[0], x[1]))
    
    out_df = pd.DataFrame(
        [{"indice": indice_nome, "ano": a, "mes": m, "variacao_mensal": f"{v:.8f}"} for (a, m, v) in rows]
    )
    
    # Filtro de sanidade 
    out_df = out_df[(out_df['ano'] > 1900) & (out_df['ano'] < 2100)]

    out_df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"[OK] Gerado: {out_csv} ({len(out_df)} registros: {rows[0][0]}/{rows[0][1]} a {rows[-1][0]}/{rows[-1][1]})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", "--xls", dest="xlsx", required=True)
    ap.add_argument("--sheet", default="SÉRIE HISTÓRICA")
    ap.add_argument("--indice", default="IPCA-E")
    ap.add_argument("--out", default="indices.csv")
    # Argumentos legados 
    ap.add_argument("--year-col", default=None)
    ap.add_argument("--month-col", default=None)
    ap.add_argument("--var-col", default=None)
    ap.add_argument("--header-row", type=int, default=0)
    ap.add_argument("--debug", action="store_true")
    
    args = ap.parse_args()

    try:
        build_indices_csv(
            xlsx_path=args.xlsx,
            sheet_name=args.sheet,
            out_csv=args.out,
            indice_nome=args.indice,
            debug=args.debug
        )
    except Exception as e:
        print(f"[ERRO] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()