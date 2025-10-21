# -*- coding: utf-8 -*-
"""
main.py
Lê esaj_detalhe_processos, imprime, chama app_4.py, PARSEIA a saída de forma robusta
e INSERE em esaj_calc_precatorio_resumo (com coalesce p/ NOT NULL).
"""

from __future__ import annotations
import os
import sys
import re
import unicodedata
import argparse
import subprocess
from typing import Optional, Dict, Any

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------- CONFIG ----------------------
load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

OVERRIDE_ANTES = os.getenv("OVERRIDE_ANTES")
OVERRIDE_POS = os.getenv("OVERRIDE_POS_IPCA")

# ---------------------- QUERY -----------------------
DEFAULT_SQL = """
SELECT
    id,
    numero_ordem,
    cpf,
    numero_processo_cnj,
    valor_total_requisitado AS valor_precatorio,
    valor_principal_bruto   AS principal,
    EXTRACT(YEAR FROM data_base_atualizacao) AS ano_base,
    juros_moratorios AS juros_mora
FROM esaj_detalhe_processos where data_base_atualizacao is not null
and process_calculo is false
"""

# ---------------------- HELPERS ---------------------
def _to_number_str_money(v) -> str:
    """Para valores monetários: remove milhar (.) e troca vírgula por ponto."""
    if v is None:
        return "0"
    s = str(v).strip().replace("R$", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")
    return s

def _to_number_str_factor(v) -> str:
    """Para fatores: mantém ponto decimal; troca vírgula por ponto (se vier)."""
    if v is None:
        return "0"
    s = str(v).strip().replace(",", ".")
    return s

def _to_int_year(v) -> int:
    try:
        return int(float(v))
    except Exception:
        return int(str(v)[:4])

def _strip_accents_lower(s: str) -> str:
    """Normaliza: remove acentos e deixa minúsculo (para matching robusto)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()

def _num_token(line: str) -> str | None:
    """
    Retorna o *primeiro* token numérico contendo ao menos um dígito.
    Evita capturar apenas '............' (sem dígitos).
    """
    m = re.search(r"(\d[\d\.,]*)", line)
    return m.group(1) if m else None

def _coalesce_payload(d: Dict[str, Any]) -> Dict[str, Any]:
    """Garante que campos NOT NULL não sejam None (usa 0/0.0)."""
    numeric_float_keys = [
        "fator_ipcae_antes","fator_ipcae_pos","fator_juros_2aa_simples",
        "principal_original","principal_apos_antes","principal_pos_ipca","principal_final_ipca_2aa",
        "juros_mora_anteriores_base","juros_mora_apos_antes","juros_mora_final_corrigido",
        "total_corrigido"
    ]
    numeric_int_keys = ["meses_para_2aa"]
    out = dict(d)
    for k in numeric_float_keys:
        if out.get(k) is None:
            out[k] = 0.0
    for k in numeric_int_keys:
        if out.get(k) is None:
            out[k] = 0
    return out

# ---------------------- PARSER DA SAÍDA ----------------------
def parse_app4_output(output: str) -> dict:
    """
    Parser linha-a-linha, tolerante a variações.
    - Usa _num_token() para garantir que há dígitos no token capturado.
    - Para dinheiro, ancora em 'R$' e aplica normalização pt-BR -> US.
    """
    result = {
        "fator_ipcae_antes": None,
        "fator_ipcae_pos": None,
        "fator_juros_2aa_simples": None,
        "meses_para_2aa": None,
        "principal_original": None,
        "principal_apos_antes": None,
        "principal_pos_ipca": None,
        "principal_final_ipca_2aa": None,
        "juros_mora_anteriores_base": None,
        "juros_mora_apos_antes": None,
        "juros_mora_final_corrigido": None,
        "total_corrigido": None,
    }

    def _to_number_str_money(v) -> str:
        s = str(v).strip().replace("R$", "").replace(" ", "")
        return s.replace(".", "").replace(",", ".")

    def _to_number_str_factor(v) -> str:
        return str(v).strip().replace(",", ".")

    lines = output.splitlines()
    for raw in lines:
        line = raw.strip().replace("\xa0", " ")
        norm = _strip_accents_lower(line)
        norm_clean = re.sub(r"\.+", " ", norm)
        norm_clean = re.sub(r"\s+", " ", norm_clean)

        # -------- FATORES --------
        if "fator ipca-e antes" in norm_clean:
            tok = _num_token(line)
            if tok:
                result["fator_ipcae_antes"] = float(_to_number_str_factor(tok))
            continue

        if "fator ipca-e pos" in norm_clean:
            tok = _num_token(line)
            if tok:
                result["fator_ipcae_pos"] = float(_to_number_str_factor(tok))
            continue

        if "fator 2% a.a" in norm_clean or "fator 2% a a" in norm_clean:
            tok = _num_token(line)
            if tok:
                result["fator_juros_2aa_simples"] = float(_to_number_str_factor(tok))
            m2 = re.search(r"meses\s*para\s*2%=\s*(\d+)", norm_clean)
            if m2:
                result["meses_para_2aa"] = int(m2.group(1))
            continue

        # -------- PRINCIPAIS (DINHEIRO) --------
        if norm_clean.startswith("principal original"):
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_original"] = float(_to_number_str_money(m.group(1)))
            continue

        if "principal apos antes" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_apos_antes"] = float(_to_number_str_money(m.group(1)))
            continue

        if "principal pos (ipca)" in norm_clean or "principal pos ipca" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_pos_ipca"] = float(_to_number_str_money(m.group(1)))
            continue

        if "principal final (ipca+2%)" in norm_clean or "principal final (ipca+2 %)" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_final_ipca_2aa"] = float(_to_number_str_money(m.group(1)))
            continue

        # -------- JUROS (DINHEIRO) --------
        if "juros mora anteriores" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["juros_mora_anteriores_base"] = float(_to_number_str_money(m.group(1)))
            continue

        if "juros mora apos antes" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["juros_mora_apos_antes"] = float(_to_number_str_money(m.group(1)))
            continue

        if norm_clean.startswith("juros mora final"):
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["juros_mora_final_corrigido"] = float(_to_number_str_money(m.group(1)))
            continue

        # -------- TOTAL CORRIGIDO (DINHEIRO) --------
        if "total corrigido" in norm_clean or "valor total corrigido" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["total_corrigido"] = float(_to_number_str_money(m.group(1)))
            continue

    return result

# ---------------------- EXEC APP4 --------------------
def call_app4_and_parse(principal: str, ano_venc: int, juros_mora_ant: str,
                        indices_csv: str = "indices_ipcae.csv",
                        verbose: bool = False) -> Dict[str, float]:
    """Executa app_4.py e retorna dicionário com valores extraídos da saída."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app4_path = os.path.join(script_dir, "app_4.py")
    python_bin = sys.executable or "python"

    cmd = [
        python_bin, app4_path,
        "--principal", principal,
        "--ano-venc", str(ano_venc),
        "--indices-csv", indices_csv,
        "--juros-mora-ant", juros_mora_ant,
        "--debug",
    ]
    if OVERRIDE_ANTES:
        cmd += ["--override-antes", OVERRIDE_ANTES]
    if OVERRIDE_POS:
        cmd += ["--override-pos-ipca", OVERRIDE_POS]

    if verbose:
        print("\n[EXEC] ", " ".join(cmd))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stderr:
        print(proc.stderr)
    output = proc.stdout or ""
    print(output)

    result = parse_app4_output(output)
    if verbose:
        print("[PARSED]", result)
    return result

# ---------------------- DB SAVE ----------------------
def insert_calc_result(conn, id_registro: int, cpf: str, numero_processo: str, result: Dict[str, float], verbose: bool = False):
    """
    Insere em esaj_calc_precatorio_resumo e, na mesma transação, faz UPDATE
    em esaj_detalhe_processos.process_calculo = true. Se qualquer passo falhar,
    tudo é revertido (rollback).
    """
    sql_insert = """
        INSERT INTO public.esaj_calc_precatorio_resumo (
            cpf,
            numero_processo_cnj,
            fator_ipcae_antes,
            fator_ipcae_pos,
            fator_juros_2aa_simples,
            meses_para_2aa,
            principal_original,
            principal_apos_antes,
            principal_pos_ipca,
            principal_final_ipca_2aa,
            juros_mora_anteriores_base,
            juros_mora_apos_antes,
            juros_mora_final_corrigido,
            total_corrigido
        ) VALUES (
            %(cpf)s,
            %(numero_processo_cnj)s,
            %(fator_ipcae_antes)s,
            %(fator_ipcae_pos)s,
            %(fator_juros_2aa_simples)s,
            %(meses_para_2aa)s,
            %(principal_original)s,
            %(principal_apos_antes)s,
            %(principal_pos_ipca)s,
            %(principal_final_ipca_2aa)s,
            %(juros_mora_anteriores_base)s,
            %(juros_mora_apos_antes)s,
            %(juros_mora_final_corrigido)s,
            %(total_corrigido)s
        )
    """

    sql_update = """
        UPDATE public.esaj_detalhe_processos
        SET process_calculo = true
        WHERE id = %s
    """

    data = {"cpf": cpf, "numero_processo_cnj": numero_processo, **_coalesce_payload(result)}

    # Transação por linha: se algo falhar, rollback garante atomicidade
    try:
        with conn:  # inicia uma transação; commit automático ao sair se não houver exceção
            with conn.cursor() as cur:
                if verbose:
                    print("[INSERT DATA]", data)
                cur.execute(sql_insert, data)
                cur.execute(sql_update, (id_registro,))
        print(f"[OK] Resumo gravado e processo marcado: ID={id_registro}, CPF={cpf}, Processo={numero_processo}")
    except Exception as e:
        # 'with conn:' já executa rollback em caso de exceção,
        # mas registramos o erro para diagnóstico
        print(f"[ERRO] Falha ao inserir/atualizar ID={id_registro} (rollback efetuado): {e}")
        # Propaga se quiser parar o processamento; ou comente a linha abaixo para pular e continuar
        # raise


# ---------------------- MAIN LOOP --------------------
def fetch_and_process(limit: Optional[int] = None, specific_id: Optional[int] = None, verbose: bool = False):
    sql = DEFAULT_SQL.strip()
    where = []
    params = []

    if specific_id is not None:
        where.append("id = %s")
        params.append(int(specific_id))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if limit is not None and specific_id is None:
        sql += f" LIMIT {int(limit)}"

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if verbose:
                print("Executando SQL:\n", sql)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        if not rows:
            print("Nenhuma linha retornada.")
            return

        for i, row in enumerate(rows, start=1):
            ano_base = row.get("ano_base")
            if ano_base is not None:
                row["ano_base"] = _to_int_year(ano_base)

            cpf = row.get("cpf")
            num_proc = row.get("numero_processo_cnj")

            print(f"\n=== Row {i} ===")
            print(f"CPF: {cpf} | Processo: {num_proc}")
            for k, v in row.items():
                if k not in ("cpf", "numero_processo_cnj"):
                    print(f"{k}: {v}")

            principal_str = _to_number_str_money(row.get("principal"))
            ano_venc_int = _to_int_year(row.get("ano_base"))
            juros_ant_str = _to_number_str_money(row.get("juros_mora"))

            result = call_app4_and_parse(
                principal=principal_str,
                ano_venc=ano_venc_int,
                juros_mora_ant=juros_ant_str,
                indices_csv="indices_ipcae.csv",
                verbose=verbose,
            )

            insert_calc_result(conn, row["id"], cpf, num_proc, result, verbose=verbose)

    except Exception as e:
        print(f"[ERRO] {e}")
    finally:
        if conn:
            conn.close()

# ---------------------- ENTRYPOINT -------------------
def main():
    parser = argparse.ArgumentParser(description="Executa cálculos e grava resultados no resumo.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    missing = [k for k, v in DB_CONFIG.items() if not v]
    if missing:
        print("ERRO: faltam variáveis no .env:", missing)
        return

    fetch_and_process(limit=args.limit, specific_id=args.id, verbose=args.verbose)

if __name__ == "__main__":
    main()
