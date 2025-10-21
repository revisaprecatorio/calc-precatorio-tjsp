# -*- coding: utf-8 -*-
"""
Converte planilha do IPCA-E (ou similar) para 'indices.csv' no formato:
  indice,ano,mes,variacao_mensal
onde variacao_mensal é FRAÇÃO mensal (ex.: 0,21% -> 0.0021).

Exemplos:
  python gerar_indices_csv.py --xls ipca-e_SerieHist/ipca-e_202509SerieHist.xls --sheet "SÉRIE HISTÓRICA" --indice "IPCA-E" --out indices.csv --header-row 3
  (Opcional) debug:
  python gerar_indices_csv.py --xls ipca-e_SerieHist/ipca-e_202509SerieHist.xls --sheet "SÉRIE HISTÓRICA" --indice "IPCA-E" --out indices.csv --header-row 3 --debug
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
    # remove quebras, NBSP e espaços duplicados
    s = str(x).replace("\n", " ").replace("\r", " ").replace("\xa0", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()

def to_int_safe(x):
    # trata None/NaN cedo
    try:
        import pandas as _pd
        if x is None or (_pd.isna(x) if hasattr(_pd, "isna") else False):
            return None
    except Exception:
        pass

    # numéricos comuns
    try:
        if isinstance(x, int):
            return int(x)
        if isinstance(x, float):
            # muitos .xls trazem 1940.0
            return int(round(x))
    except Exception:
        pass

    # strings "1940.0", " 1940 ", etc.
    try:
        s = str(x).strip()
        if s.endswith(".0"):
            s = s[:-2]
        return int(s)
    except Exception:
        # última tentativa: Decimal
        try:
            from decimal import Decimal
            return int(Decimal(str(x)))
        except Exception:
            return None


def parse_percent_to_fraction(x):
    """
    Converte '0,21', '0,21 %', '0.21%', 0.21, '−0,22', '-0,22'
    para FRAÇÃO Decimal: 0.0021 (0,21%).
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
    return (val / Decimal("100")) if abs(val) > Decimal("0.2") else val

def month_to_number(x):
    if pd.isna(x):
        return None
    s = norm_str(x).upper()
    # remove pontuação/ruído simples
    for ch in [".", ",", ";", ":"]:
        s = s.replace(ch, "")
    if s in PT_MONTHS:
        return PT_MONTHS[s]
    n = to_int_safe(s)
    if n and 1 <= n <= 12:
        return n
    return None

def normalize_sheet_arg(sheet):
    if isinstance(sheet, str) and sheet.strip().isdigit():
        return int(sheet.strip())
    return sheet

# -----------------------------
# Leitura .XLS com xlrd direto
# -----------------------------

def read_xls_with_xlrd(xlsx_path: Path, sheet, header_row: int = 0) -> pd.DataFrame:
    import xlrd
    from xlrd.xldate import xldate_as_datetime

    book = xlrd.open_workbook(xlsx_path.as_posix())
    if isinstance(sheet, str):
        sh = book.sheet_by_name(sheet)
    else:
        sh = book.sheet_by_index(int(sheet) if isinstance(sheet, int) else 0)

    rows = []
    for r in range(header_row, sh.nrows):
        row = []
        for c in range(sh.ncols):
            cell = sh.cell(r, c)
            val = cell.value
            if cell.ctype == 3:
                try:
                    val = xldate_as_datetime(val, book.datemode)
                except Exception:
                    pass
            row.append(val)
        rows.append(row)

    if not rows:
        raise RuntimeError("Planilha .xls sem conteúdo após header_row.")

    header = [norm_str(h) for h in rows[0]]
    data = rows[1:]
    df = pd.DataFrame(data, columns=header)
    return df

# -------- Leitura robusta (.xlsx/.xlsm/.xls-HTML) --------

def read_any_excel(xlsx_path: Path, sheet, table_index: int = 0, encoding_hint: str = None, header_row: int = 0, debug=False):
    from io import StringIO as _SIO

    def try_html_like():
        data = Path(xlsx_path).read_bytes()
        encs = ([encoding_hint] if encoding_hint else []) + ["utf-8", "cp1252", "latin-1", "iso-8859-1"]
        for enc in encs:
            if not enc:
                continue
            try:
                html = data.decode(enc, errors="ignore")
            except UnicodeDecodeError:
                continue
            try:
                import lxml  # noqa
                tables = pd.read_html(_SIO(html), flavor="lxml", header=header_row)
                if tables:
                    return tables[table_index if 0 <= table_index < len(tables) else 0]
            except Exception:
                pass
            try:
                import bs4  # noqa
                import html5lib  # noqa
                tables = pd.read_html(_SIO(html), flavor="bs4", header=header_row)
                if tables:
                    return tables[table_index if 0 <= table_index < len(tables) else 0]
            except Exception:
                pass
        raise RuntimeError("Não consegui decodificar como HTML embutido.")

    sheet = normalize_sheet_arg(sheet)
    suffix = xlsx_path.suffix.lower()

    if suffix in (".xlsx", ".xlsm"):
        print("[INFO] Lendo como .xlsx/.xlsm via openpyxl…")
        try:
            return pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl", header=header_row)
        except Exception as e:
            print(f"[AVISO] openpyxl não reconheceu como XLSX ({e}). Tentando como HTML embutido…")
            return try_html_like()

    if suffix == ".xls":
        try:
            print("[INFO] Lendo .xls diretamente com xlrd…")
            return read_xls_with_xlrd(Path(xlsx_path), sheet, header_row=header_row)
        except Exception as e1:
            print(f"[AVISO] xlrd direto falhou ({e1}). Tentando como HTML embutido…")
            try:
                print("[INFO] Tentando ler .xls como HTML embutido…")
                return try_html_like()
            except Exception as e2:
                print(f"[AVISO] HTML não funcionou ({e2}). Tentando engine xlrd via pandas (pode falhar)…")
                try:
                    import xlrd  # noqa
                    return pd.read_excel(xlsx_path, sheet_name=sheet, engine="xlrd", header=header_row)
                except Exception as e3:
                    raise RuntimeError(f"Falha total ao ler '{xlsx_path}': {e3}")

    print("[INFO] Tentando openpyxl (fallback)…")
    return pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl", header=header_row)

# -------- Detecção/transformação --------

def detect_columns(df, debug=False) -> Tuple[str, str, str]:
    cols_norm = [norm_str(c) for c in df.columns]
    cols_up = [c.upper() for c in cols_norm]
    if debug:
        print("[DEBUG] Colunas lidas:", cols_norm)

    ano_candidates = [c for c in cols_up if "ANO" in c]
    mes_candidates = [c for c in cols_up if "MÊS" in c or "MES" in c]
    var_candidates = [c for c in cols_up if "VAR" in c or "%" in c or "MENSAL" in c]

    ano_col = df.columns[cols_up.index(ano_candidates[0])] if ano_candidates else None
    mes_col = df.columns[cols_up.index(mes_candidates[0])] if mes_candidates else None
    var_col = df.columns[cols_up.index(var_candidates[0])] if var_candidates else None

    if debug:
        print(f"[DEBUG] ano_col={ano_col} | mes_col={mes_col} | var_col={var_col}")

    return ano_col, mes_col, var_col

def tidy_rows(df, ano_col, mes_col, var_col, debug=False) -> List[Tuple[int, int, Decimal]]:
    rows = []
    if ano_col in df.columns:
        df[ano_col] = df[ano_col].ffill()
    for _, r in df.iterrows():
        ano = to_int_safe(r.get(ano_col))
        mes = month_to_number(r.get(mes_col))
        var = parse_percent_to_fraction(r.get(var_col))
        if ano and mes and var is not None:
            rows.append((ano, mes, var))
    if debug:
        print(f"[DEBUG] linhas válidas encontradas: {len(rows)}")
        print("[DEBUG] amostra:", rows[:5])
    return rows

def wide_to_tidy(df, debug=False) -> List[Tuple[int, int, Decimal]]:
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
    if debug:
        print(f"[DEBUG] wide->tidy linhas: {len(rows)} | meses detectados: {[norm_str(c) for c in month_cols]}")
    return rows

# -------- Pipeline principal --------

def build_indices_csv(xlsx_path, sheet_name, out_csv, indice_nome,
                      ano_col=None, mes_col=None, var_col=None,
                      table_index: int = 0, encoding_hint: str = None,
                      header_row: int = 0, debug: bool = False):
    df = read_any_excel(Path(xlsx_path), sheet_name, table_index=table_index,
                        encoding_hint=encoding_hint, header_row=header_row, debug=debug)

    df.columns = [norm_str(c) for c in df.columns]

    rows = []
    if ano_col and mes_col and var_col and all(c in df.columns for c in (ano_col, mes_col, var_col)):
        if debug:
            print("[DEBUG] Usando colunas passadas via CLI.")
        rows = tidy_rows(df, ano_col, mes_col, var_col, debug=debug)
    else:
        d_ano, d_mes, d_var = detect_columns(df, debug=debug)
        if d_ano and d_mes and d_var:
            rows = tidy_rows(df, d_ano, d_mes, d_var, debug=debug)

    if not rows:
        rows = wide_to_tidy(df, debug=debug)

    if not rows:
        raise RuntimeError(
            "Não foi possível detectar dados. "
            "Use --year-col/--month-col/--var-col, ou ajuste --table-index/--encoding-hint/--header-row (tente 3, 4 ou 5)."
        )

    rows.sort(key=lambda x: (x[0], x[1]))

    out_df = pd.DataFrame(
        [{"indice": indice_nome, "ano": a, "mes": m, "variacao_mensal": float(v)} for (a, m, v) in rows]
    )
    out_df.to_csv(out_csv, index=False, encoding="utf-8")
    print(f"[OK] Gerado: {out_csv} ({len(out_df)} linhas)")
    print(out_df.head(12).to_string(index=False))

# -------- CLI --------

def main():
    ap = argparse.ArgumentParser(description="Converter planilha do IPCA-E (ou similar) para indices.csv")
    ap.add_argument("--xlsx", required=False, help="Caminho da planilha (.xlsx/.xls ou .xls-HTML)")
    ap.add_argument("--xls", dest="xlsx", required=False, help="(Alias) Caminho da planilha .xls")
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
    ap.add_argument("--debug", action="store_true", help="Exibe colunas, amostras e caminhos de detecção")
    args = ap.parse_args()

    if not args.xlsx:
        print("[ERRO] Informe o caminho da planilha com --xlsx (ou --xls).")
        sys.exit(1)

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
            debug=args.debug,
        )
    except Exception as e:
        print(f"[ERRO] {e}")
        sys.exit(2)

if __name__ == "__main__":
    main()
