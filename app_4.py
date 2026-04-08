#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
app_4.py — Atualização por IPCA-E / SELIC com regra comparativa

Lógica:
1) Período de Graça:
   - Correção por IPCA-E puro.
2) Período com Juros:
   - Compara:
       a) IPCA-E * juros simples anual (ex.: 2% a.a.)
       b) SELIC
   - Aplica o MENOR fator acumulado no período.
3) Juros de mora anteriores:
   - São corrigidos pelos mesmos fatores do principal.

Observações importantes:
- Este script mantém precisão interna sem arredondar a cada etapa.
- O arredondamento para 2 casas ocorre apenas na exibição e no resultado final.
- O mês inicial do cálculo é configurável:
    * padrão: mês seguinte à data base (--start-next-month)
    * opcional: incluir o próprio mês da data base (--include-start-month)
"""

from __future__ import annotations
import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, getcontext
from typing import Dict, Iterable, List, Tuple, Optional

# Mais precisão interna para evitar erro acumulado
getcontext().prec = 28

# ----------------------------- datas ------------------------------------------

def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)

def month_range(start: date, end_exclusive: date) -> List[Tuple[int, int]]:
    cur = start
    out: List[Tuple[int, int]] = []
    while cur < end_exclusive:
        out.append((cur.year, cur.month))
        cur = add_months(cur, 1)
    return out

def first_day_next_month(d: date) -> date:
    return add_months(date(d.year, d.month, 1), 1)

def first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)

# ----------------------------- números ----------------------------------------

Q2 = Decimal("0.01")

def d(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if x is None:
        return Decimal("0")
    s = str(x).strip()
    if s == "":
        return Decimal("0")
    s = s.replace(".", "").replace(",", ".") if "," in s and "." in s else s.replace(",", ".")
    return Decimal(s)

def q2(x: Decimal) -> Decimal:
    return d(x).quantize(Q2, rounding=ROUND_HALF_UP)

def br_money(x: Decimal) -> str:
    s = f"{q2(x):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

# ----------------------------- índices ----------------------------------------

@dataclass
class Indices:
    fator_mensal: Dict[Tuple[int, int], Decimal]  # (ano, mes) -> multiplicador

    @staticmethod
    def from_csv(path: str) -> "Indices":
        fator_mensal: Dict[Tuple[int, int], Decimal] = {}

        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                rd = csv.DictReader(f)
                cols = [c.strip().lower() for c in (rd.fieldnames or [])]

                if set(["indice", "ano", "mes", "variacao_mensal"]).issubset(cols):
                    # Formato padrão do seu pipeline:
                    # variacao_mensal = taxa mensal em fração
                    for row in rd:
                        ano = int(row["ano"])
                        mes = int(row["mes"])
                        taxa = Decimal(str(row["variacao_mensal"]).strip().replace(",", "."))
                        fator_mensal[(ano, mes)] = Decimal("1") + taxa

                elif set(["data", "fator"]).issubset(cols):
                    # Formato alternativo:
                    # data=YYYY-MM ; fator pode ser multiplicador ou percentual
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
                    raise ValueError(
                        "CSV não reconhecido. Use:\n"
                        "A) indice,ano,mes,variacao_mensal\n"
                        "B) data(YYYY-MM),fator"
                    )

        except FileNotFoundError:
            print(f"ERRO: Arquivo de índice não encontrado: {path}")
            raise
        except Exception as e:
            print(f"ERRO: Falha ao ler índice '{path}': {e}")
            raise

        if not fator_mensal:
            raise ValueError(f"Nenhum índice carregado de '{path}'.")

        return Indices(fator_mensal)

    def product(self, ym_list: Iterable[Tuple[int, int]], debug: bool = False, label: str = "") -> Decimal:
        prod = Decimal("1")
        for y, m in ym_list:
            if (y, m) not in self.fator_mensal:
                raise KeyError(f"Faltou índice ({label}) para {y:04d}-{m:02d}")
            f = self.fator_mensal[(y, m)]
            if debug:
                print(f"  {label:<14} {y:04d}-{m:02d}  fator={f}")
            prod *= f
        return prod

    def last_available_month(self) -> Tuple[int, int]:
        return max(self.fator_mensal.keys())

# ----------------------------- cálculo ----------------------------------------

@dataclass
class Resultado:
    fator_ipcae_graca: Decimal
    fator_ipcae_juros: Decimal
    fator_juros_2aa_simples: Decimal
    fator_total_principal: Decimal
    principal_apos_graca: Decimal
    principal_pos_ipca_juros: Decimal
    principal_final: Decimal
    jm_ant_corrigido: Decimal
    total_corrigido: Decimal
    meses_graca: int
    meses_juros: int
    meses_para_2aa: int
    criterio_vencedor: str

def calcular(
    principal: Decimal,
    data_requisitorio_str: str,
    indices_ipcae: Indices,
    indices_selic: Indices,
    pos_fim: Optional[Tuple[int, int]],
    juros_aa_pos: Decimal,
    juros_mora_ant: Decimal,
    override_antes: Optional[Decimal],
    override_pos_ipca: Optional[Decimal],
    debug: bool,
    include_start_month: bool,
) -> Resultado:

    # ------------------ 1. Definir Datas Chave ------------------

    try:
        req_date = datetime.strptime(data_requisitorio_str.split(" ")[0], "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"data_requisitorio inválida: '{data_requisitorio_str}'. Use YYYY-MM-DD.") from e

    if principal < 0:
        raise ValueError("principal não pode ser negativo.")
    if juros_mora_ant < 0:
        raise ValueError("juros_mora_ant não pode ser negativo.")
    if juros_aa_pos < 0:
        raise ValueError("juros_aa_pos não pode ser negativo.")

    ano_venc = req_date.year

    # Data final do cálculo (exclusive)
    if pos_fim is None:
        pos_fim = indices_ipcae.last_available_month()

    pos_fim_date_excl = first_day_next_month(date(pos_fim[0], pos_fim[1], 1))

    # Início do cálculo: parametrizável
    if include_start_month:
        req_start_month_date = first_day_of_month(req_date)
    else:
        req_start_month_date = first_day_next_month(req_date)

    # Data de início dos juros conforme regra do script
    data_marco_emenda = date(2025, 9, 1)
    inicio_regra_nova_pura = date(ano_venc + 2, 1, 1)
    inicio_aplicacao_juros = max(inicio_regra_nova_pura, data_marco_emenda)

    print(f"Data Requisitório..............: {req_date:%Y-%m-%d}")
    print(f"Início do cálculo..............: {req_start_month_date:%Y-%m-%d}")
    print(f"Data Fim Cálculo...............: {add_months(pos_fim_date_excl, -1):%Y-%m}")
    print(
        f"Início Juros...................: {inicio_aplicacao_juros:%Y-%m-%d} "
        f"(max(Fim Graça={inicio_regra_nova_pura}, Emenda={data_marco_emenda}))"
    )

    # ------------------ 2. Separar Meses ------------------

    meses_ipca_e_puro: List[Tuple[int, int]] = []
    meses_ipca_e_juros: List[Tuple[int, int]] = []

    if req_start_month_date < pos_fim_date_excl:
        p1_start = req_start_month_date
        p1_end_excl = min(inicio_aplicacao_juros, pos_fim_date_excl)

        if p1_start < p1_end_excl:
            meses_ipca_e_puro = month_range(p1_start, p1_end_excl)

        p2_start = max(inicio_aplicacao_juros, req_start_month_date)
        p2_end_excl = pos_fim_date_excl

        if p2_start < p2_end_excl:
            meses_ipca_e_juros = month_range(p2_start, p2_end_excl)

    print(
        f"\nPERÍODO GRAÇA (IPCA-E)........: "
        f"{meses_ipca_e_puro[0] if meses_ipca_e_puro else 'N/A'} .. "
        f"{meses_ipca_e_puro[-1] if meses_ipca_e_puro else 'N/A'}  "
        f"({len(meses_ipca_e_puro)} meses)"
    )
    print(
        f"PERÍODO JUROS (Comparativo)...: "
        f"{meses_ipca_e_juros[0] if meses_ipca_e_juros else 'N/A'} .. "
        f"{meses_ipca_e_juros[-1] if meses_ipca_e_juros else 'N/A'}  "
        f"({len(meses_ipca_e_juros)} meses)\n"
    )

    # ------------------ 3. Fatores ------------------

    fator_ipca_puro = Decimal("1")
    fator_ipcae_juros_base = Decimal("1")
    fator_juros_2aa_simples_base = Decimal("1")
    fator_total_emenda = Decimal("1")
    fator_total_selic = Decimal("1")
    fator_ipcae_juros_final = Decimal("1")
    fator_juros_2aa_simples_final = Decimal("1")
    fator_juros_total_aplicado = Decimal("1")
    criterio_vencedor = "SEM_PERIODO_JUROS"

    # Período de Graça
    if override_antes is not None:
        fator_ipca_puro = d(override_antes)
        if debug:
            print(f"[override] Fator IPCA-E (Graça) = {fator_ipca_puro}")
    else:
        fator_ipca_puro = (
            indices_ipcae.product(meses_ipca_e_puro, debug=debug, label="[GRAÇA]")
            if meses_ipca_e_puro else Decimal("1")
        )

    # Período com Juros
    n_juros = len(meses_ipca_e_juros)
    n_meses_para_2aa = max(n_juros - 1, 0)

    if n_juros > 0:
        if override_pos_ipca is not None:
            fator_ipcae_juros_base = d(override_pos_ipca)
            if debug:
                print(f"[override] Fator IPCA-E (Juros) = {fator_ipcae_juros_base}")
        else:
            fator_ipcae_juros_base = indices_ipcae.product(
                meses_ipca_e_juros, debug=debug, label="[JUROS IPCA-E]"
            )

        fator_juros_2aa_simples_base = Decimal("1") + (
            juros_aa_pos * Decimal(n_meses_para_2aa) / Decimal("12")
        )

        fator_total_emenda = fator_ipcae_juros_base * fator_juros_2aa_simples_base
        fator_total_selic = indices_selic.product(
            meses_ipca_e_juros, debug=debug, label="[JUROS SELIC]"
        )

        print(f"Comparativo Juros - Emenda....: {fator_total_emenda:.12f}")
        print(f"Comparativo Juros - SELIC.....: {fator_total_selic:.12f}")

        if fator_total_selic < fator_total_emenda:
            print(">>> Decisão: aplicar SELIC (menor fator).")
            fator_ipcae_juros_final = fator_total_selic
            fator_juros_2aa_simples_final = Decimal("1")
            criterio_vencedor = "SELIC"
        else:
            print(">>> Decisão: aplicar IPCA-E + 2% (menor fator).")
            fator_ipcae_juros_final = fator_ipcae_juros_base
            fator_juros_2aa_simples_final = fator_juros_2aa_simples_base
            criterio_vencedor = "IPCAE_2AA"

        fator_juros_total_aplicado = fator_ipcae_juros_final * fator_juros_2aa_simples_final

    # ------------------ 4. Aplicar Fatores ------------------

    principal_apos_graca = principal * fator_ipca_puro
    principal_pos_ipca_juros = principal_apos_graca * fator_ipcae_juros_final
    principal_final = principal_pos_ipca_juros * fator_juros_2aa_simples_final

    jm_ant_base = juros_mora_ant
    jm_ant_apos_graca = jm_ant_base * fator_ipca_puro
    jm_ant_corrigido = jm_ant_apos_graca * fator_juros_total_aplicado

    total_corrigido = principal_final + jm_ant_corrigido

    # ------------------ 5. Exibição ------------------

    print("\n>>> CÁLCULO DETALHADO")
    print(f"Critério vencedor..............: {criterio_vencedor}")
    print(f"Fator IPCA-E (Graça)...........: {fator_ipca_puro:.12f}")
    print(f"Fator IPCA-E (Juros)...........: {fator_ipcae_juros_final:.12f}")
    print(f"Fator 2% a.a. (simples)........: {fator_juros_2aa_simples_final:.12f}")
    print(f"Meses para 2%..................: {n_meses_para_2aa}")
    print("----------------------------------------------------------")
    print(f"Principal original.............: R$ {br_money(principal)}")
    print(f"Principal após Graça...........: R$ {br_money(principal_apos_graca)}")
    print(f"Principal pós Juros............: R$ {br_money(principal_pos_ipca_juros)}")
    print(f"Principal final................: R$ {br_money(principal_final)}")
    print("")
    print(f"Juros mora anteriores (base)...: R$ {br_money(jm_ant_base)}")
    print(f"Juros mora após Graça..........: R$ {br_money(jm_ant_apos_graca)}")
    print(f"Juros mora final corrigido.....: R$ {br_money(jm_ant_corrigido)}")
    print("----------------------------------------------------------")
    print(f"TOTAL CORRIGIDO................: R$ {br_money(total_corrigido)}")

    return Resultado(
        fator_ipcae_graca=q2(fator_ipca_puro),
        fator_ipcae_juros=q2(fator_ipcae_juros_final),
        fator_juros_2aa_simples=q2(fator_juros_2aa_simples_final),
        fator_total_principal=q2(fator_ipca_puro * fator_juros_total_aplicado),
        principal_apos_graca=q2(principal_apos_graca),
        principal_pos_ipca_juros=q2(principal_pos_ipca_juros),
        principal_final=q2(principal_final),
        jm_ant_corrigido=q2(jm_ant_corrigido),
        total_corrigido=q2(total_corrigido),
        meses_graca=len(meses_ipca_e_puro),
        meses_juros=n_juros,
        meses_para_2aa=n_meses_para_2aa,
        criterio_vencedor=criterio_vencedor,
    )

# ----------------------------- CLI -------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Atualização por IPCA-E: GRAÇA(IPCA-E) + PÓS(IPCA-E+2% ou SELIC)."
    )
    p.add_argument("--principal", required=True, type=str, help="Valor do principal (ex.: 1097665.34)")
    p.add_argument("--data-requisitorio", required=True, type=str, help="Data do ofício requisitório (YYYY-MM-DD)")
    p.add_argument("--indices-csv", default="indices.csv", help="CSV de índices IPCA-E")
    p.add_argument("--selic-csv", default="indices_selic.csv", help="CSV de índices SELIC")
    p.add_argument("--pos-fim", default=None, help="YYYY-MM do fim do cálculo. Se ausente, usa o último mês do CSV.")
    p.add_argument("--juros-aa-pos", default="0.02", help="Juros a.a. simples no pós (default 0.02 = 2%)")
    p.add_argument("--juros-mora-ant", default="0", help="Valor de juros de mora anteriores.")
    p.add_argument("--override-antes", default=None, help="Override do fator do período de graça.")
    p.add_argument("--override-pos-ipca", default=None, help="Override do fator IPCA-E do período com juros.")
    p.add_argument("--clip-pos", action="store_true",
                   help="Se --pos-fim não existir no CSV, ajusta para o último mês disponível.")
    p.add_argument("--debug", action="store_true", help="Lista fatores mês a mês.")

    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--include-start-month",
        action="store_true",
        help="Inclui o próprio mês da data base no cálculo."
    )
    group.add_argument(
        "--start-next-month",
        action="store_true",
        help="Começa no mês seguinte à data base (padrão recomendado)."
    )

    return p.parse_args()

def main():
    args = parse_args()

    principal = d(args.principal)
    juros_aa_pos = d(args.juros_aa_pos)
    juros_mora_ant = d(args.juros_mora_ant)

    indices_ipcae = Indices.from_csv(args.indices_csv)
    indices_selic = Indices.from_csv(args.selic_csv)

    # Padrão: começar no mês seguinte
    include_start_month = bool(args.include_start_month)

    pos_fim_tuple = None
    if args.pos_fim:
        try:
            y, m = args.pos_fim.split("-")
            pos_fim_tuple = (int(y), int(m))
        except Exception as e:
            raise ValueError("--pos-fim inválido. Use YYYY-MM.") from e

    last_y_ipca, last_m_ipca = indices_ipcae.last_available_month()
    if pos_fim_tuple is not None and pos_fim_tuple > (last_y_ipca, last_m_ipca):
        if args.clip_pos:
            print(
                f"Aviso: --pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no CSV IPCA-E; "
                f"ajustado para {last_y_ipca:04d}-{last_m_ipca:02d}."
            )
            pos_fim_tuple = (last_y_ipca, last_m_ipca)
        else:
            raise KeyError(
                f"--pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no IPCA-E "
                f"(último={last_y_ipca:04d}-{last_m_ipca:02d}). Use --clip-pos."
            )

    if pos_fim_tuple is None:
        pos_fim_tuple = (last_y_ipca, last_m_ipca)

    last_y_selic, last_m_selic = indices_selic.last_available_month()
    if pos_fim_tuple > (last_y_selic, last_m_selic):
        if args.clip_pos:
            print(
                f"Aviso: --pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no CSV SELIC; "
                f"ajustado para {last_y_selic:04d}-{last_m_selic:02d}."
            )
            pos_fim_tuple = (last_y_selic, last_m_selic)
        else:
            raise KeyError(
                f"--pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no SELIC "
                f"(último={last_y_selic:04d}-{last_m_selic:02d}). Use --clip-pos."
            )

    override_antes = d(args.override_antes) if args.override_antes is not None else None
    override_pos_ipca = d(args.override_pos_ipca) if args.override_pos_ipca is not None else None

    calcular(
        principal=principal,
        data_requisitorio_str=args.data_requisitorio,
        indices_ipcae=indices_ipcae,
        indices_selic=indices_selic,
        pos_fim=pos_fim_tuple,
        juros_aa_pos=juros_aa_pos,
        juros_mora_ant=juros_mora_ant,
        override_antes=override_antes,
        override_pos_ipca=override_pos_ipca,
        debug=args.debug,
        include_start_month=include_start_month,
    )

if __name__ == "__main__":
    main()