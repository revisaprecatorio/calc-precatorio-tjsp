#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
app_4.py — Atualização por IPCA-E (Modelo EC 136/2025)

Lógica de Cálculo (Pós-Emenda 09/09/2025):
O cálculo é dividido em dois períodos, com base na data do ofício requisitório:

1. PERÍODO DE GRAÇA (Correção IPCA-E Puro):
   - Início: Mês do ofício requisitório (data_requisitorio).
   - Fim: Início da aplicação dos juros (ver abaixo).

2. PERÍODO COM JUROS (Correção IPCA-E + 2% a.a. simples OU SELIC):
   - Início: É a data MAIS TARDE entre:
     a) 01/01 do ano (ano_requisitório + 2) -> (fim do período de graça constitucional)
     b) 01/09/2025 -> (Data de vigência da nova Emenda)
   - Fim: O mês final do cálculo (pos_fim).
   - REGRA: Aplica-se o FATOR MENOR entre (Fator IPCA-E * Fator Juros 2%) e (Fator SELIC).

Juros de Mora ANTERIORES:
- Corrigidos pelos mesmos fatores do principal (IPCA-E Puro, depois Fator Vencedor).
"""

from __future__ import annotations
import argparse, csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Iterable, List, Tuple

# ----------------------------- datas ------------------------------------------

def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)

def month_range(start: date, end_exclusive: date) -> List[Tuple[int,int]]:
    cur = start
    out: List[Tuple[int,int]] = []
    while cur < end_exclusive:
        out.append((cur.year, cur.month))
        cur = add_months(cur, 1)
    return out

def first_day_next_month(d: date) -> date:
    return add_months(date(d.year, d.month, 1), 1)

# ----------------------------- números ----------------------------------------

Q2 = Decimal("0.01")
def d(x): return Decimal(str(x)) if not isinstance(x, Decimal) else x
def q2(x: Decimal) -> Decimal: return d(x).quantize(Q2, rounding=ROUND_HALF_UP)
def br_money(x: Decimal) -> str:
    s = f"{q2(x):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

# ----------------------------- índices ----------------------------------------

@dataclass
class Indices:
    fator_mensal: Dict[Tuple[int,int], Decimal]  # (ano,mes)->multiplicador

    @staticmethod
    def from_csv(path: str) -> "Indices":
        fator_mensal: Dict[Tuple[int,int], Decimal] = {}
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                rd = csv.DictReader(f)
                cols = [c.strip().lower() for c in (rd.fieldnames or [])]
                if set(["indice","ano","mes","variacao_mensal"]).issubset(cols):
                    # Formato A (seu)
                    for row in rd:
                        ano = int(row["ano"]); mes = int(row["mes"])
                        taxa = Decimal(str(row["variacao_mensal"]).strip().replace(",", "."))
                        fator_mensal[(ano, mes)] = Decimal("1") + taxa
                elif set(["data","fator"]).issubset(cols):
                    # Formato B (alternativo)
                    for row in rd:
                        y, m = str(row["data"]).strip().split("-")
                        raw = str(row["fator"]).strip().replace(",", ".")
                        if raw.endswith("%"):
                            fator = Decimal("1") + (Decimal(raw[:-1]) / Decimal("100"))
                        else:
                            val = Decimal(raw)
                            fator = val if val > Decimal("0.5") else (Decimal("1") + val)
                        fator_mensal[(int(y), int(m))] = fator
                else:
                    raise ValueError("CSV não reconhecido. Use: "
                                     "A) indice,ano,mes,variacao_mensal  ou  "
                                     "B) data(YYYY-MM),fator")
        except FileNotFoundError:
            print(f"ERRO: Arquivo de índice não encontrado: {path}")
            raise
        except Exception as e:
            print(f"ERRO: Falha ao ler índice '{path}': {e}")
            raise

        if not fator_mensal:
            raise ValueError(f"Nenhum índice carregado de '{path}'.")
        return Indices(fator_mensal)

    def product(self, ym_list: Iterable[Tuple[int,int]], debug: bool=False, label: str="") -> Decimal:
        prod = Decimal("1")
        for y, m in ym_list:
            if (y, m) not in self.fator_mensal:
                raise KeyError(f"Faltou índice ({label}) para {y:04d}-{m:02d}")
            f = self.fator_mensal[(y, m)]
            if debug: print(f"  {label}  {y:04d}-{m:02d}  fator={f}")
            prod *= f
        return prod

    def last_available_month(self) -> Tuple[int,int]:
        return max(self.fator_mensal.keys())

# ----------------------------- cálculo ----------------------------------------

@dataclass
class Resultado:
    fator_ipcae_graca: Decimal
    fator_ipcae_juros: Decimal      # Fator IPCA-E (base) ou Fator SELIC (se venceu)
    fator_juros_2aa_simples: Decimal  # Fator 2% (se venceu) ou 1.0 (se SELIC venceu)
    fator_total_principal: Decimal
    principal_final: Decimal
    jm_ant_corrigido: Decimal
    total_corrigido: Decimal
    meses_graca: int
    meses_juros: int
    meses_para_2aa: int

def calcular(principal: Decimal, data_requisitorio_str: str,
             indices_ipcae: Indices, indices_selic: Indices,
             pos_fim: Tuple[int,int] | None, juros_aa_pos: Decimal,
             juros_mora_ant: Decimal,
             override_antes: Decimal | None, override_pos_ipca: Decimal | None,
             debug: bool) -> Resultado:

    # --- 1. Definir Datas Chave ---
    try:
        req_date = datetime.strptime(data_requisitorio_str, "%Y-%m-%d").date()
    except ValueError:
        req_date = datetime.strptime(data_requisitorio_str.split(" ")[0], "%Y-%m-%d").date()

    req_start_month_date = date(req_date.year, req_date.month, 1)
    ano_venc = req_date.year

    # Data final do cálculo (exclusive)
    if pos_fim is None:
        pos_fim = indices_ipcae.last_available_month()
    pos_fim_date_excl = first_day_next_month(date(pos_fim[0], pos_fim[1], 1))

    # Data de início dos juros (Regra EC 136/2025)
    data_marco_emenda = date(2025, 9, 1)
    inicio_regra_nova_pura = date(ano_venc + 2, 1, 1) # Início após graça constitucional
    
    inicio_aplicacao_juros = max(inicio_regra_nova_pura, data_marco_emenda)

    print(f"Data Requisitório: {req_date:%Y-%m-%d} (Ano Venc: {ano_venc})")
    print(f"Data Fim Cálculo: {add_months(pos_fim_date_excl,-1):%Y-%m}")
    print(f"Início Juros (EC136/25): {inicio_aplicacao_juros:%Y-%m-%d} (max(Fim Graça={inicio_regra_nova_pura}, Emenda={data_marco_emenda}))")

    # --- 2. Separar Meses ---
    meses_ipca_e_puro = []
    meses_ipca_e_juros = []

    if req_start_month_date < pos_fim_date_excl:
        p1_start = req_start_month_date
        p1_end_excl = min(inicio_aplicacao_juros, pos_fim_date_excl)
        
        if p1_start < p1_end_excl:
            meses_ipca_e_puro = month_range(p1_start, p1_end_excl)

        p2_start = max(inicio_aplicacao_juros, p1_start)
        p2_end_excl = pos_fim_date_excl

        if p2_start < p2_end_excl:
            meses_ipca_e_juros = month_range(p2_start, p2_end_excl)

    print(f"\nPERÍODO GRAÇA (IPCA-E): {meses_ipca_e_puro[0] if meses_ipca_e_puro else 'N/A'} .. {meses_ipca_e_puro[-1] if meses_ipca_e_puro else 'N/A'}  ({len(meses_ipca_e_puro)} meses)")
    print(f"PERÍODO JUROS (Comparativo): {meses_ipca_e_juros[0] if meses_ipca_e_juros else 'N/A'} .. {meses_ipca_e_juros[-1] if meses_ipca_e_juros else 'N/A'}  ({len(meses_ipca_e_juros)} meses)\n")

    # --- 3. Calcular Fatores ---

    # Fator IPCA-E no Período de Graça
    if override_antes is not None:
        fator_ipca_puro = d(override_antes)
        if debug: print(f"[override] Fator IPCA-E (Graça) = {fator_ipca_puro}")
    else:
        fator_ipca_puro = indices_ipcae.product(meses_ipca_e_puro, debug=debug, label="[GRAÇA]") if meses_ipca_e_puro else Decimal("1")

    # --- 4. Comparativo Período Juros ---
    
    n_juros = len(meses_ipca_e_juros)
    n_meses_para_2aa = max(n_juros - 1, 0)
    
    fator_ipcae_juros_base: Decimal
    fator_juros_2aa_simples_base: Decimal
    fator_total_emenda: Decimal
    fator_total_selic: Decimal
    
    fator_ipcae_juros_final: Decimal
    fator_juros_2aa_simples_final: Decimal
    
    principal_pos_ipca_juros: Decimal # Valor intermediário
    principal_final: Decimal
    jm_ant_corrigido: Decimal

    if n_juros == 0:
        # Se não há período de juros, todos os fatores são 1
        fator_ipcae_juros_base = Decimal("1")
        fator_juros_2aa_simples_base = Decimal("1")
        fator_total_emenda = Decimal("1")
        fator_total_selic = Decimal("1")
        fator_ipcae_juros_final = Decimal("1")
        fator_juros_2aa_simples_final = Decimal("1")
    else:
        # Fator IPCA-E (base) no Período com Juros
        if override_pos_ipca is not None:
            fator_ipcae_juros_base = d(override_pos_ipca)
            if debug: print(f"[override] Fator IPCA-E (Juros) = {fator_ipcae_juros_base}")
        else:
            fator_ipcae_juros_base = indices_ipcae.product(meses_ipca_e_juros, debug=debug, label="[JUROS IPCA-E]") if meses_ipca_e_juros else Decimal("1")

        # Fator Juros 2% a.a. simples (base)
        fator_juros_2aa_simples_base = Decimal("1") + (juros_aa_pos * Decimal(n_meses_para_2aa) / Decimal("12"))

        # Fator Total Emenda (IPCA-E + 2%)
        fator_total_emenda = fator_ipcae_juros_base * fator_juros_2aa_simples_base
        
        # Fator Total SELIC (Puro)
        fator_total_selic = indices_selic.product(meses_ipca_e_juros, debug=debug, label="[JUROS SELIC]") if meses_ipca_e_juros else Decimal("1")
        
        print(f"Comparativo Juros: Fator Emenda (IPCA-E+2%) = {fator_total_emenda:.8f}")
        print(f"Comparativo Juros: Fator SELIC (Puro)     = {fator_total_selic:.8f}")

        # --- 5. Decisão (Menor Fator) ---
        if fator_total_selic < fator_total_emenda:
            print(">>> Decisão: Aplicar Fator SELIC (menor).")
            fator_ipcae_juros_final = fator_total_selic       # Fator total vai para a coluna 'ipcae_juros'
            fator_juros_2aa_simples_final = Decimal("1")  # Coluna 'juros_2aa' vira 1
        else:
            print(">>> Decisão: Aplicar Fator IPCA-E + 2% (menor).")
            fator_ipcae_juros_final = fator_ipcae_juros_base      # Fator IPCA-E base
            fator_juros_2aa_simples_final = fator_juros_2aa_simples_base # Fator 2%

    # --- 6. Aplicar Fatores ---

    # Principal
    principal_apos_graca = q2(principal * fator_ipca_puro)
    principal_pos_ipca_juros = q2(principal_apos_graca * fator_ipcae_juros_final)
    principal_final = q2(principal_pos_ipca_juros * fator_juros_2aa_simples_final)

    # JM ANTERIORES (corrigidos como principal)
    jm_ant_base = d(juros_mora_ant)
    jm_ant_apos_graca = q2(jm_ant_base * fator_ipca_puro)
    fator_juros_total_aplicado = fator_ipcae_juros_final * fator_juros_2aa_simples_final
    jm_ant_corrigido  = q2(jm_ant_apos_graca * fator_juros_total_aplicado)

    total_corrigido = q2(principal_final + jm_ant_corrigido)

    print("\n>>> CÁLCULO DETALHADO")
    print(f"Fator IPCA-E (Graça) ..........: {fator_ipca_puro:.8f}")
    print(f"Fator IPCA-E (Juros) ..........: {fator_ipcae_juros_final:.8f} (Base ou SELIC)")
    print(f"Fator 2% a.a. (simples) .......: {fator_juros_2aa_simples_final:.8f}  (2% ou 1.0 se SELIC)")
    print(f"(Meses para 2%={n_meses_para_2aa}, se aplicável)")
    print("---------------------------------------------")
    print(f"Principal original .............: R$ {br_money(principal)}")
    print(f"Principal após Graça (IPCA-E) ..: R$ {br_money(principal_apos_graca)}")
    print(f"Principal pós Juros (Base/SELIC): R$ {br_money(principal_pos_ipca_juros)}")
    print(f"Principal final (c/ Juros 2%..): R$ {br_money(principal_final)}")
    print(f"\nJuros mora anteriores (base) ...: R$ {br_money(jm_ant_base)}")
    print(f"Juros mora após Graça ..........: R$ {br_money(jm_ant_apos_graca)}")
    print(f"Juros mora final (corrigido) ...: R$ {br_money(jm_ant_corrigido)}")
    print("---------------------------------------------")
    print(f"TOTAL CORRIGIDO ................: R$ {br_money(total_corrigido)}")

    return Resultado(
        fator_ipcae_graca=fator_ipca_puro,
        fator_ipcae_juros=fator_ipcae_juros_final,
        fator_juros_2aa_simples=fator_juros_2aa_simples_final,
        fator_total_principal=fator_ipca_puro * fator_juros_total_aplicado,
        principal_final=principal_final,
        jm_ant_corrigido=jm_ant_corrigido,
        total_corrigido=total_corrigido,
        meses_graca=len(meses_ipca_e_puro),
        meses_juros=n_juros,
        meses_para_2aa=n_meses_para_2aa,
    )

# ----------------------------- CLI -------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Atualização por IPCA-E (EC 136/2025): GRAÇA(IPCA-E) + PÓS(IPCA-E+2% ou SELIC)."
    )
    p.add_argument("--principal", required=True, type=str, help="Valor do principal (ex.: 1097665.34)")
    p.add_argument("--data-requisitorio", required=True, type=str, help="Data do ofício requisitório (YYYY-MM-DD)")
    # Default 'indices.csv' (IPCA-E) para bater com comando do usuário
    p.add_argument("--indices-csv", default="indices.csv", help="CSV de índices IPCA-E (default: indices.csv)")
    p.add_argument("--selic-csv", default="indices_selic.csv", help="CSV de índices SELIC (default: indices_selic.csv)")
    p.add_argument("--pos-fim", default=None, help="YYYY-MM do fim do período PÓS (ex.: 2025-10). Se ausente, usa o último mês do CSV.")
    p.add_argument("--juros-aa-pos", default="0.02", help="Juros a.a. simples no PÓS (default 0.02 = 2%)")
    p.add_argument("--juros-mora-ant", default="0", help="Valor de Juros de Mora ANTERIORES (será corrigido pelos mesmos fatores).")
    p.add_argument("--override-antes", default=None, help="Se informado, usa este fator para o Período Graça (ex.: 1.08370280).")
    p.add_argument("--override-pos-ipca", default=None, help="Se informado, usa este fator para o Período Juros (IPCA-E base) (ex.: 1.21414986).")
    p.add_argument("--clip-pos", action="store_true",
                   help="Se --pos-fim não existir no CSV, ajusta para o último mês disponível (com aviso).")
    p.add_argument("--debug", action="store_true", help="Lista fatores mês a mês.")
    return p.parse_args()

def main():
    args = parse_args()
    principal = d(args.principal)
    juros_aa_pos = d(args.juros_aa_pos)
    juros_mora_ant = d(args.juros_mora_ant)

    indices_ipcae = Indices.from_csv(args.indices_csv)
    indices_selic = Indices.from_csv(args.selic_csv)

    # pos_fim
    pos_fim_tuple = None
    if args.pos_fim:
        try:
            y, m = args.pos_fim.split("-")
            pos_fim_tuple = (int(y), int(m))
        except Exception:
            raise ValueError("--pos-fim inválido. Use YYYY-MM.")

    last_y, last_m = indices_ipcae.last_available_month()
    if pos_fim_tuple is not None and pos_fim_tuple > (last_y, last_m):
        if args.clip_pos:
            print(f"Aviso: --pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no CSV IPCA-E; "
                  f"ajustei para {last_y:04d}-{last_m:02d}.")
            pos_fim_tuple = (last_y, last_m)
        else:
            raise KeyError(f"--pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe (último={last_y:04d}-{last_m:02d}). "
                           "Use --clip-pos para ajustar automaticamente.")
    if pos_fim_tuple is None:
        pos_fim_tuple = (last_y, last_m)
    
    # Validar teto do SELIC também
    last_y_s, last_m_s = indices_selic.last_available_month()
    if pos_fim_tuple > (last_y_s, last_m_s):
         print(f"Aviso: --pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no CSV SELIC (último={last_y_s:04d}-{last_m_s:02d}). "
               f"Ajustando para o último disponível no SELIC: {last_y_s:04d}-{last_m_s:02d}.")
         pos_fim_tuple = (last_y_s, last_m_s)


    override_antes = d(args.override_antes) if args.override_antes is not None else None
    override_pos_ipca = d(args.override_pos_ipca) if args.override_pos_ipca is not None else None

    calcular(principal=principal, data_requisitorio_str=args.data_requisitorio,
             indices_ipcae=indices_ipcae, indices_selic=indices_selic,
             pos_fim=pos_fim_tuple,
             juros_aa_pos=juros_aa_pos,
             juros_mora_ant=juros_mora_ant,
             override_antes=override_antes,
             override_pos_ipca=override_pos_ipca,
             debug=args.debug)

if __name__ == "__main__":
    main()