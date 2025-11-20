# -*- coding: utf-8 -*-
"""
main.py
Lê esaj_detalhe_processos, imprime, chama app_4.py (modificado p/ EC 136/2025),
PARSEIA a saída de forma robusta e INSERE em esaj_calc_precatorio_resumo.
"""

from __future__ import annotations
import os
import sys
import re
import unicodedata
import argparse
import subprocess
from typing import Optional, Dict, Any
from datetime import date

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from decimal import Decimal


# ---------------------- CONFIG ----------------------
load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# Overrides (agora 'graca' e 'juros')
OVERRIDE_ANTES = os.getenv("OVERRIDE_ANTES") # Usado para o período de graça
OVERRIDE_POS = os.getenv("OVERRIDE_POS_IPCA") # Usado para o período com juros

# ---------------------- QUERY -----------------------
# Modificado para buscar a data_base_atualizacao completa
DEFAULT_SQL = """
SELECT
    id,
    numero_ordem,
    cpf,
    numero_processo_cnj,
    valor_total_requisitado AS valor_precatorio,
    valor_principal_bruto   AS principal,
    data_base_atualizacao AS data_requisitorio,
    juros_moratorios AS juros_mora
FROM esaj_detalhe_processos
WHERE data_base_atualizacao IS NOT NULL
AND process_calculo IS FALSE
"""

# ---------------------- HELPERS ---------------------
def _to_number_str_money(v) -> str:
    """Aceita Decimal/float/int sem mexer; só converte pt-BR quando houver vírgula."""
    if v is None:
        return "0"
    if isinstance(v, (int, float, Decimal)):
        # retorna como string simples (p/ passar ao CLI do app_4.py)
        return str(v)

    s = str(v).strip().replace("R$", "").strip()

    # Caso pt-BR: tem vírgula decimal
    if "," in s:
        # remove separador de milhar e troca vírgula por ponto
        s = s.replace(".", "").replace(",", ".")
    else:
        # Caso US: mantém ponto decimal, remove lixo
        s = re.sub(r"[^\d\.\-]", "", s)

    return s or "0"

def _to_number_str_factor(v) -> str:
    """Para fatores: mantém ponto decimal; troca vírgula por ponto (se vier)."""
    if v is None:
        return "0"
    s = str(v).strip().replace(",", ".")
    return s

def _to_date_str(v) -> str | None:
    """Converte date/datetime ou string para YYYY-MM-DD."""
    if v is None:
        return None
    if isinstance(v, (date)):
        return v.isoformat()
    s = str(v).strip()
    try:
        # Tenta parsear para normalizar
        from dateutil.parser import parse
        return parse(s).date().isoformat()
    except Exception:
        # Retorna os 10 primeiros caracteres se parecer uma data
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return None

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
    """
    Garante que campos NOT NULL não sejam None (usa 0/0.0) e
    renomeia chaves do parser para bater com os nomes de coluna do DB.
    """
    # Nomes de colunas do DB (destino)
    colunas_db = {
        "fator_ipcae_graca": 0.0,
        "fator_ipcae_juros": 0.0,
        "fator_juros_2aa_simples": 0.0,
        "principal_original": 0.0,
        "principal_apos_graca": 0.0,
        "principal_pos_juros": 0.0,
        "principal_final": 0.0, # Nome da coluna no DB
        "juros_mora_anteriores_base": 0.0,
        "juros_mora_apos_graca": 0.0,
        "juros_mora_final_corrigido": 0.0,
        "total_corrigido": 0.0,
        "meses_para_2aa": 0,
        "meses_graca": 0,
        "meses_juros": 0,
    }
    
    # Mapeamento: Chave do Parser (origem) -> Chave do DB (destino)
    mapa_chaves = {
        # Chaves do Parser -> Chaves do DB
        "fator_ipcae_graca": "fator_ipcae_graca",
        "fator_ipcae_antes": "fator_ipcae_graca", # Compatibilidade
        
        "fator_ipcae_juros": "fator_ipcae_juros",
        "fator_ipcae_pos": "fator_ipcae_juros", # Compatibilidade
        
        "fator_juros_2aa_simples": "fator_juros_2aa_simples",
        
        "principal_original": "principal_original",
        
        "principal_apos_graca": "principal_apos_graca",
        "principal_apos_antes": "principal_apos_graca", # Compatibilidade
        
        "principal_pos_juros": "principal_pos_juros",
        "principal_pos_ipca": "principal_pos_juros", # Compatibilidade
        
        "principal_final_ipca_2aa": "principal_final", # MUDANÇA DE NOME
        
        "juros_mora_anteriores_base": "juros_mora_anteriores_base",
        
        "juros_mora_apos_graca": "juros_mora_apos_graca",
        "juros_mora_apos_antes": "juros_mora_apos_graca", # Compatibilidade
        
        "juros_mora_final_corrigido": "juros_mora_final_corrigido",
        "total_corrigido": "total_corrigido",
        
        "meses_para_2aa": "meses_para_2aa",
        "meses_graca": "meses_graca",
        "meses_juros": "meses_juros",
    }

    out = dict(colunas_db) # Começa com todos os defaults
    
    # Preenche 'out' com os valores do dicionário 'd' (resultado do parser)
    for chave_parser, valor in d.items():
        if valor is None:
            continue # Mantém o default (0 ou 0.0)
            
        # Encontra o nome da coluna de destino no DB
        chave_db = mapa_chaves.get(chave_parser)
        
        if chave_db:
            out[chave_db] = valor # Define o valor na chave correta do DB
        else:
            # Chave do parser não esperada, mas guarda por segurança se for float/int
            if isinstance(valor, (int, float)) and chave_parser in colunas_db:
                 out[chave_parser] = valor

    return out


# ---------------------- PARSER DA SAÍDA ----------------------
def parse_app4_output(output: str) -> dict:
    """
    Parser linha-a-linha, tolerante a variações.
    Atualizado para os novos nomes de campos do app_4.py (EC 136/2025).
    """
    result = {
        "fator_ipcae_graca": None,
        "fator_ipcae_juros": None,
        "fator_juros_2aa_simples": None,
        "meses_para_2aa": None,
        "meses_graca": None,
        "meses_juros": None,
        "principal_original": None,
        "principal_apos_graca": None,
        "principal_pos_juros": None, # MUDOU (era principal_pos_ipca)
        "principal_final_ipca_2aa": None, # MUDOU (era principal_final (ipca+2%))
        "juros_mora_anteriores_base": None,
        "juros_mora_apos_graca": None, # MUDOU (era juros_mora_apos_antes)
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

        # -------- PERÍODOS (MESES) --------
        if "periodo graca (ipca-e)" in norm_clean:
            m = re.search(r"\((\d+)\s*meses\)", norm)
            if m:
                result["meses_graca"] = int(m.group(1))
            continue
        
        if "periodo juros (comparativo)" in norm_clean:
            m = re.search(r"\((\d+)\s*meses\)", norm)
            if m:
                result["meses_juros"] = int(m.group(1))
            continue

        # -------- FATORES --------
        if "fator ipca-e (graca)" in norm_clean:
            tok = _num_token(line)
            if tok:
                result["fator_ipcae_graca"] = float(_to_number_str_factor(tok))
            continue

        if "fator ipca-e (juros)" in norm_clean:
            tok = _num_token(line)
            if tok:
                result["fator_ipcae_juros"] = float(_to_number_str_factor(tok))
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

        if "principal apos graca" in norm_clean:
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_apos_graca"] = float(_to_number_str_money(m.group(1)))
            continue

        if "principal pos juros" in norm_clean: # MUDOU
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["principal_pos_juros"] = float(_to_number_str_money(m.group(1)))
            continue

        if "principal final (c/ juros 2%)" in norm_clean: # MUDOU
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

        if "juros mora apos graca" in norm_clean: # MUDOU
            m = re.search(r"R\$\s*(\d[\d\.,]*)", line)
            if m:
                result["juros_mora_apos_graca"] = float(_to_number_str_money(m.group(1)))
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
def call_app4_and_parse(principal: str, data_requisitorio: str, juros_mora_ant: str,
                        indices_csv: str = "indices.csv", # MUDOU
                        indices_selic_csv: str = "indices_selic.csv", # NOVO
                        verbose: bool = False) -> Dict[str, float]:
    """
    Executa app_4.py (modificado) e retorna dicionário com valores extraídos da saída.
    Usa --data-requisitorio ao invés de --ano-venc.
    Passa também o --selic-csv.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    app4_path = os.path.join(script_dir, "app_4.py")
    python_bin = sys.executable or "python"

    cmd = [
        python_bin, app4_path,
        "--principal", principal,
        "--data-requisitorio", data_requisitorio,
        "--indices-csv", indices_csv, # IPCA-E
        "--selic-csv", indices_selic_csv, # SELIC
        "--juros-mora-ant", juros_mora_ant,
        "--debug",
    ]
    if OVERRIDE_ANTES:
        cmd += ["--override-antes", OVERRIDE_ANTES]
    if OVERRIDE_POS:
        cmd += ["--override-pos-ipca", OVERRIDE_POS]

    if verbose:
        print("\n[EXEC] ", " ".join(cmd))

    # CORREÇÃO: Adicionado errors='ignore' para evitar o UnicodeDecodeError
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    
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
    Insere em esaj_calc_precatorio_resumo (com colunas atualizadas)
    e faz UPDATE em esaj_detalhe_processos.process_calculo = true.
    """
    # SQL ATUALIZADO para os novos nomes de coluna (graca/juros)
    # Garanta que a sua tabela no DB tenha estas colunas
    sql_insert = """
        INSERT INTO public.esaj_calc_precatorio_resumo (
            cpf,
            numero_processo_cnj,
            fator_ipcae_graca,
            fator_ipcae_juros,
            fator_juros_2aa_simples,
            meses_para_2aa,
            meses_graca,
            meses_juros,
            principal_original,
            principal_apos_graca,
            principal_pos_juros,
            principal_final,
            juros_mora_anteriores_base,
            juros_mora_apos_graca,
            juros_mora_final_corrigido,
            total_corrigido
        ) VALUES (
            %(cpf)s,
            %(numero_processo_cnj)s,
            %(fator_ipcae_graca)s,
            %(fator_ipcae_juros)s,
            %(fator_juros_2aa_simples)s,
            %(meses_para_2aa)s,
            %(meses_graca)s,
            %(meses_juros)s,
            %(principal_original)s,
            %(principal_apos_graca)s,
            %(principal_pos_juros)s,
            %(principal_final)s,
            %(juros_mora_anteriores_base)s,
            %(juros_mora_apos_graca)s,
            %(juros_mora_final_corrigido)s,
            %(total_corrigido)s
        )
    """

    sql_update = """
        UPDATE public.esaj_detalhe_processos
        SET process_calculo = true
        WHERE id = %s
    """

    # O _coalesce_payload renomeia as chaves do parser para bater com o DB
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
    params = []

    if specific_id is not None:
        # Modificado para ignorar o 'process_calculo' E filtrar por ID
        sql = sql.replace("AND process_calculo IS FALSE", "") 
        sql += " AND id = %s"
        params.append(int(specific_id))
    
    if limit is not None and specific_id is None:
        sql += f" LIMIT {int(limit)}"
        
    sql += " ORDER BY id"


    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if verbose:
                print("Executando SQL:\n", cur.mogrify(sql, tuple(params)).decode('utf-8', errors='ignore'))
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

        if not rows:
            print("Nenhuma linha retornada.")
            return

        for i, row in enumerate(rows, start=1):
            cpf = row.get("cpf")
            num_proc = row.get("numero_processo_cnj")
            
            # Normaliza data_requisitorio para YYYY-MM-DD
            data_req_str = _to_date_str(row.get("data_requisitorio"))

            print(f"\n=== Row {i} ===")
            print(f"CPF: {cpf} | Processo: {num_proc}")
            for k, v in row.items():
                if k not in ("cpf", "numero_processo_cnj"):
                    print(f"{k}: {v}")
            
            if data_req_str is None:
                print(f"[ERRO] ID={row.get('id')}: data_requisitorio nula ou inválida. Pulando.")
                continue

            principal_str = _to_number_str_money(row.get("principal"))
            juros_ant_str = _to_number_str_money(row.get("juros_mora"))

            result = call_app4_and_parse(
                principal=principal_str,
                data_requisitorio=data_req_str,
                juros_mora_ant=juros_ant_str,
                indices_csv="indices.csv", # MUDOU (IPCA-E)
                indices_selic_csv="indices_selic.csv", # NOVO (SELIC)
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
    parser = argparse.ArgumentParser(description="Executa cálculos (EC 136/2025) e grava resultados no resumo.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", type=int, default=None, help="Executa o cálculo para um ID específico, ignorando 'process_calculo'.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    missing = [k for k, v in DB_CONFIG.items() if not v]
    if missing:
        print("ERRO: faltam variáveis no .env:", missing)
        return

    fetch_and_process(limit=args.limit, specific_id=args.id, verbose=args.verbose)

if __name__ == "__main__":
    main()