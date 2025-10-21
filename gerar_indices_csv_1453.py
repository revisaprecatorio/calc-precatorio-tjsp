# -*- coding: utf-8 -*-
"""
Converte planilha do IPCA-E (ou similar) para 'indices.csv' no formato:
  indice,ano,mes,variacao_mensal
onde variacao_mensal é FRAÇÃO mensal (ex.: 0,21% -> 0.0021). 

Exemplos:
  python gerar_indices_csv.py --xlsx ipca-e_SerieHist/ipca-e_202509SerieHist.xlsx --sheet "Série Histórica IPCA" --indice "IPCA-E" --out indices.csv --header-row 4 --year-col "ANO" --month-col "Mês" --var-col "(%)"

Também funciona com .xls “disfarçado” (HTML), usando parsers lxml/bs4+html5lib.

Dependências:
  pip install -U pandas openpyxl lxml html5lib beautifulsoup4
"""
import argparse
import sys
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import List, Tuple
from io import StringIO

import pandas as pd

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
    return str(x).strip().replace("\n", " ").replace("\r", " ")

def to_int_safe(x):
    try:
        return int(str(x).strip())
    except Exception:
        return None

def parse_percent_to_fraction(x):
    """
    Converte '0,21', '0,21 %', '0.21%', 0.21, '−0,22', '-0,22'
    para FRAÇÃO Decimal: 0.0021 (0,21%).
    Heurística:
      - remove espaços, símbolos de menos e '%'
      - troca vírgula por ponto
      - se |valor| > 0.2, interpreta como PERCENTUAL (divide por 100)
      - senão, assume que já é fração mensal (ex.: 0.0032)
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = norm_str(x)
    s = s.replace("–", "-").replace("−", "-").replace("%", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")  # '0,21' -> '0.21'
    try:
        val = Decimal(s)
    except InvalidOperation:
        return None
    return (val / Decimal(100)) if abs(val) > Decimal("0.2") else val

def month_to_number(x):
    if pd.isna(x):
        return None
    s = norm_str(x).upper()
    if s in PT_MONTHS:
        return PT_MONTHS[s]
    n = to_int_safe(s)
    if n and 1 <= n <= 12:
        return n
    return None

def normalize_sheet_arg(sheet):
    # "0" -> 0 ; "1" -> 1 ; "Nome da Aba" -> "Nome da Aba"
    if isinstance(sheet, str) and sheet.strip().isdigit():
        return int(sheet.strip())
    return sheet

# -------- Leitura robusta (.xlsx/.xlsm/.xls-HTML) --------
def read_any_excel(xlsx_path: Path, sheet, table_index: int = 0, encoding_hint: str = None, header_row: int = 0):
    sheet = normalize_sheet_arg(sheet)
    suffix = xlsx_path.suffix.lower()

    # 1) .xlsx/.xlsm (Excel moderno)
    if suffix in (".xlsx", ".xlsm"):
        print("[INFO] Lendo como .xlsx/.xlsm via openpyxl…")
        return pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl", header=header_row)

    # 2) .xls (muitos são HTML disfarçado; outros são BIFF antigo)
    if suffix == ".xls":
        # Tenta como HTML disfarçado (mais comum em órgãos públicos)
        try:
            print("[INFO] Tentando ler .xls como HTML embutido…")
            data = Path(xlsx_path).read_bytes()
            # heurística simples de encoding
            encodings = [encoding_hint] if encoding_hint else []
            encodings += ["utf-8", "cp1252", "latin-1", "iso-8859-1"]
            for enc in encodings:
                if not enc:
                    continue
                try:
                    html = data.decode(enc, errors="strict")
                except UnicodeDecodeError:
                    continue
                # tenta lxml
                try:
                    print(f"[INFO] Lendo HTML com lxml (encoding={enc})…")
                    import lxml  # noqa: F401
                    tables = pd.read_html(StringIO(html), flavor="lxml", header=header_row)
                    if tables:
                        idx = table_index if 0 <= table_index < len(tables) else 0
                        return tables[idx]
                except Exception:
                    pass
                # tenta bs4/html5lib
                try:
                    print(f"[INFO] Lendo HTML com html5lib/bs4 (encoding={enc})…")
                    import bs4  # noqa: F401
                    import html5lib  # noqa: F401
                    tables = pd.read_html(StringIO(html), flavor="bs4", header=header_row)
                    if tables:
                        idx = table_index if 0 <= table_index < len(tables) else 0
                        return tables[idx]
                except Exception:
                    pass
            raise RuntimeError("Não consegui decodificar o HTML com encodings comuns (utf-8/cp1252/latin-1).")
        except Exception as e:
            # último recurso: tentar realmente como .xls antigo (raríssimo hoje)
            print(f"[AVISO] HTML não funcionou ({e}). Tentando engine xlrd (pode falhar)…")
            try:
                import xlrd  # xlrd<2.0
                print(f"[INFO] Lendo .xls via xlrd {xlrd.__version__}…")
                return pd.read_excel(xlsx_path, sheet_name=sheet, engine="xlrd", header=header_row)
            except Exception as e2:
                raise RuntimeError(f"Falha total ao ler '{xlsx_path}': {e2}")

    # 3) fallback genérico
    print("[INFO] Tentando openpyxl (fallback)…")
    return pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl", header=header_row)

# -------- Detecção/transformação --------
def detect_columns(df) -> Tuple[str, str, str]:
    cols_up = [norm_str(c).upper() for c in df.columns]
    ano_candidates = [c for c in cols_up if "ANO" in c]
    mes_candidates = [c for c in cols_up if "MÊS" in c or "MES" in c]
    var_candidates = [c for c in cols_up if "VAR" in c or "%" in c or "MENSAL" in c]
    ano_col = df.columns[cols_up.index(ano_candidates[0])] if ano_candidates else None
    mes_col = df.columns[cols_up.index(mes_candidates[0])] if mes_candidates else None
    var_col = df.columns[cols_up.index(var_candidates[0])] if var_candidates else None
    return ano_col, mes_col, var_col

def tidy_rows(df, ano_col, mes_col, var_col) -> List[Tuple[int,int,Decimal]]:
    rows = []
    # ffill do ano (linhas com o ano só na 1ª linha do bloco)
    if ano_col in df.columns:
        df[ano_col] = df[ano_col].ffill()
    for _, r in df.iterrows():
        ano = to_int_safe(r.get(ano_col))
        mes = month_to_number(r.get(mes_col))
        var = parse_percent_to_fraction(r.get(var_col))
        if ano and mes and var is not None:
            rows.append((ano, mes, var))
    return rows

def wide_to_tidy(df) -> List[Tuple[int,int,Decimal]]:
    # Formato "amplo": uma coluna 'ANO' e 12 colunas de meses (JAN..DEZ)
    ano_col = None
    for c in df.columns:
        if "ANO" in norm_str(c).upper():
            ano_col = c
            break
    if not ano_col:
        return []
    month_cols = [c for c in df.columns if norm_str(c).upper() in PT_MONTHS]
    if not month_cols:
        return []
    # ffill do ano
    df[ano_col] = df[ano_col].ffill()
    rows = []
    for _, r in df.iterrows():
        ano = to_int_safe(r.get(ano_col))
        if not ano:
            continue
        for mc in month_cols:
            mes = month_to_number(mc)
            var = parse_percent_to_fraction(r.get(mc))
            if ano and mes and var is not None:
                rows.append((ano, mes, var))
    return rows

# -------- Pipeline principal --------
def build_indices_csv(xlsx_path, sheet_name, out_csv, indice_nome,
                      ano_col=None, mes_col=None, var_col=None,
                      table_index: int = 0, encoding_hint: str = None,
                      header_row: int = 0):
    df = read_any_excel(Path(xlsx_path), sheet_name, table_index=table_index,
                        encoding_hint=encoding_hint, header_row=header_row)

    # limpar colunas multilinha/espaços
    df.columns = [norm_str(c) for c in df.columns]

    # 1) tenta modo tidy (colunas claras)
    rows = []
    if ano_col and mes_col and var_col and all(c in df.columns for c in (ano_col, mes_col, var_col)):
        rows = tidy_rows(df, ano_col, mes_col, var_col)
    else:
        d_ano, d_mes, d_var = detect_columns(df)
        if d_ano and d_mes and d_var:
            rows = tidy_rows(df, d_ano, d_mes, d_var)

    # 2) se falhar, tenta “amplo” (ano + colunas JAN..DEZ)
    if not rows:
        rows = wide_to_tidy(df)

    if not rows:
        raise RuntimeError(
            "Não foi possível detectar dados. "
            "Use --year-col/--month-col/--var-col, ou ajuste --table-index/--encoding-hint/--header-row."
        )

    rows.sort(key=lambda x: (x[0], x[1]))  # ordena por ano/mês

    out_df = pd.DataFrame(
        [{"indice": indice_nome, "ano": a, "mes": m, "variacao_mensal": float(v)} for (a, m, v) in rows]
    )
    out_df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"[OK] Gerado: {out_csv} ({len(out_df)} linhas)")
    print(out_df.head(12).to_string(index=False))

# -------- CLI --------
def main():
    ap = argparse.ArgumentParser(description="Converter planilha do IPCA-E (ou similar) para indices.csv")
    ap.add_argument("--xlsx", required=True, help="Caminho da planilha (.xlsx/.xls ou .xls-HTML)")
    ap.add_argument("--sheet", default=0, help="Nome da aba (ou índice). Para .xls-HTML, use 0 (primeira tabela).")
    ap.add_argument("--indice", default="IPCA-E", help="Nome do índice a gravar (default: IPCA-E)")
    ap.add_argument("--out", default="indices.csv", help="Arquivo CSV de saída (default: indices.csv)")
    ap.add_argument("--year-col", dest="year_col", default=None, help="Nome exato da coluna 'Ano' (opcional)")
    ap.add_argument("--month-col", dest="month_col", default=None, help="Nome exato da coluna 'Mês' (opcional)")
    ap.add_argument("--var-col", dest="var_col", default=None, help="Nome exato da coluna de variação mensal (opcional)")
    ap.add_argument("--table-index", dest="table_index", type=int, default=0, help="Índice da tabela no HTML (default: 0)")
    ap.add_argument("--encoding-hint", dest="encoding_hint", default=None, help="Forçar encoding (ex.: cp1252, latin-1)")
    ap.add_argument("--header-row", dest="header_row", type=int, default=0,
                    help="Linha do cabeçalho (0-based). Ex.: se o cabeçalho está na linha 5 do Excel, use --header-row 4")
    args = ap.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(f"[ERRO] Arquivo não encontrado: {xlsx_path}")
        sys.exit(1)

    try:
        build_indices_csv(
            xlsx_path=xlsx_path,
            sheet_name=args.sheet,
            out_csv=args.out,
            indice_nome=args.indice,
            ano_col=args.year_col,
            mes_col=args.month_col,
            var_col=args.var_col,
            table_index=args.table_index,
            encoding_hint=args.encoding_hint,
            header_row=args.header_row,
        )
    except Exception as e:
        print(f"[ERRO] {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
