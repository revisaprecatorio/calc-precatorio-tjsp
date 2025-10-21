#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
app_4.py — Atualização por IPCA-E (modelo PSC):

Períodos:
- ANTES (modo 'formacao', padrão): 07/(ano_venc-1) .. 12/(ano_venc) [~18 meses, IPCA-E puro]
- ANTES (modo 'full'):             07/(ano_venc-1) .. 11/2021       [IPCA-E puro]
- PÓS:                             12/2021 .. pos_fim                [IPCA-E + 2% a.a. simples]
  * 2% a.a. simples com número de meses = (n_meses_pos - 1)

Juros de Mora ANTERIORES:
- Valor informado (ex.: --juros-mora-ant 471676.23) é corrigido pelos MESMOS fatores do principal: ANTES + PÓS.

Overrides (opcionais) para bater 100% com a memória:
- --override-antes X        -> substitui o produto mensal do ANTES por X (ex.: 1.08370280)
- --override-pos-ipca Y     -> substitui o produto mensal do PÓS (IPCA-E) por Y (ex.: 1.21414986)

CSV aceito:
A) indices_ipcae.csv -> indice,ano,mes,variacao_mensal   (0.0065 = 0,65%/mês)
B) ipcae_mensal.csv  -> data,fator  (YYYY-MM, 1.0043 ou '0,43%')
"""

from __future__ import annotations
import argparse, csv
from dataclasses import dataclass
from datetime import date
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
        if not fator_mensal:
            raise ValueError("Nenhum índice carregado.")
        return Indices(fator_mensal)

    def product(self, ym_list: Iterable[Tuple[int,int]], debug: bool=False, label: str="") -> Decimal:
        prod = Decimal("1")
        for y, m in ym_list:
            if (y, m) not in self.fator_mensal:
                raise KeyError(f"Faltou IPCA-E para {y:04d}-{m:02d}")
            f = self.fator_mensal[(y, m)]
            if debug: print(f"  {label}  {y:04d}-{m:02d}  fator={f}")
            prod *= f
        return prod

    def last_available_month(self) -> Tuple[int,int]:
        return max(self.fator_mensal.keys())

# ----------------------------- períodos ---------------------------------------

def periodos_antes_formacao(ano_venc: int):
    # 07/(ano_venc-1) .. 12/(ano_venc)
    inicio = date(ano_venc - 1, 7, 1)
    fim_excl = first_day_next_month(date(ano_venc, 12, 1))  # 12/ano_venc inclusive
    return inicio, fim_excl

def periodos_antes_full(ano_venc: int):
    # 07/(ano_venc-1) .. 11/2021
    inicio = date(ano_venc - 1, 7, 1)
    fim_excl = date(2021, 12, 1)  # 11/2021 inclusive
    return inicio, fim_excl

def periodo_pos(pos_fim: Tuple[int,int] | None, indices: Indices):
    # 12/2021 .. pos_fim
    inicio = date(2021, 12, 1)
    if pos_fim is None:
        pos_fim = indices.last_available_month()
    y_f, m_f = pos_fim
    fim_excl = first_day_next_month(date(y_f, m_f, 1))
    return inicio, fim_excl

# ----------------------------- cálculo ----------------------------------------

@dataclass
class Resultado:
    fator_antes: Decimal
    fator_ipca_pos: Decimal
    fator_juros_simples_pos: Decimal
    fator_total_principal: Decimal
    principal_final: Decimal
    jm_ant_corrigido: Decimal
    total_corrigido: Decimal
    meses_antes: int
    meses_pos: int

def calcular(principal: Decimal, ano_venc: int, indices: Indices,
             pos_fim: Tuple[int,int] | None, juros_aa_pos: Decimal,
             juros_mora_ant: Decimal, antes_mode: str,
             override_antes: Decimal | None, override_pos_ipca: Decimal | None,
             debug: bool) -> Resultado:

    # ANTES
    if antes_mode == "full":
        inicio_antes, fim_antes_excl = periodos_antes_full(ano_venc)
    else:
        inicio_antes, fim_antes_excl = periodos_antes_formacao(ano_venc)

    meses_antes = month_range(inicio_antes, fim_antes_excl)

    # PÓS
    inicio_pos, fim_pos_excl = periodo_pos(pos_fim, indices)
    meses_pos = month_range(inicio_pos, fim_pos_excl)

    print(f"ANTES ({'full' if antes_mode=='full' else 'formacao'}): {inicio_antes:%Y-%m} .. {add_months(fim_antes_excl,-1):%Y-%m}  ({len(meses_antes)} meses, IPCA-E)")
    print(f"PÓS: {inicio_pos:%Y-%m} .. {add_months(fim_pos_excl,-1):%Y-%m}  ({len(meses_pos)} meses, IPCA-E + 2% a.a. simples)\n")

    # Fatores ANTES
    if override_antes is not None:
        fator_antes = d(override_antes)
        if debug: print(f"[override] Fator IPCA-E ANTES = {fator_antes}")
    else:
        fator_antes = indices.product(meses_antes, debug=debug, label="[ANTES]") if meses_antes else Decimal("1")

    # Fator IPCA no PÓS
    if override_pos_ipca is not None:
        fator_ipca_pos = d(override_pos_ipca)
        if debug: print(f"[override] Fator IPCA-E PÓS = {fator_ipca_pos}")
    else:
        fator_ipca_pos = indices.product(meses_pos, debug=debug, label="[ PÓS ]") if meses_pos else Decimal("1")

    # 2% a.a. simples — meses para 2% = (n_meses_pos - 1)
    n_pos = len(meses_pos)
    n_meses_para_2aa = max(n_pos - 1, 0)
    fator_juros_simples_pos = Decimal("1") + (juros_aa_pos * Decimal(n_meses_para_2aa) / Decimal("12"))

    # Principal
    principal_apos_antes = q2(principal * fator_antes)
    principal_pos_ipca   = q2(principal_apos_antes * fator_ipca_pos)
    principal_final      = q2(principal_pos_ipca * fator_juros_simples_pos)

    # JM ANTERIORES (corrigidos como principal)
    jm_ant_base = d(juros_mora_ant)
    jm_ant_apos_antes = q2(jm_ant_base * fator_antes)
    jm_ant_corrigido  = q2(jm_ant_apos_antes * fator_ipca_pos * fator_juros_simples_pos)

    total_corrigido = q2(principal_final + jm_ant_corrigido)

    print("\n>>> CÁLCULO DETALHADO")
    print(f"Fator IPCA-E ANTES ............: {fator_antes:.8f}")
    print(f"Fator IPCA-E PÓS ..............: {fator_ipca_pos:.8f}")
    print(f"Fator 2% a.a. (simples) .......: {fator_juros_simples_pos:.8f}  (meses para 2%={n_meses_para_2aa})")
    print("---------------------------------------------")
    print(f"Principal original .............: R$ {br_money(principal)}")
    print(f"Principal após ANTES ...........: R$ {br_money(principal_apos_antes)}")
    print(f"Principal pós (IPCA) ...........: R$ {br_money(principal_pos_ipca)}")
    print(f"Principal final (IPCA+2%) ......: R$ {br_money(principal_final)}")
    print(f"\nJuros mora anteriores (base) ...: R$ {br_money(jm_ant_base)}")
    print(f"Juros mora após ANTES ..........: R$ {br_money(jm_ant_apos_antes)}")
    print(f"Juros mora final (corrigido) ...: R$ {br_money(jm_ant_corrigido)}")
    print("---------------------------------------------")
    print(f"TOTAL CORRIGIDO ................: R$ {br_money(total_corrigido)}")

    return Resultado(
        fator_antes=fator_antes,
        fator_ipca_pos=fator_ipca_pos,
        fator_juros_simples_pos=fator_juros_simples_pos,
        fator_total_principal=fator_antes * fator_ipca_pos * fator_juros_simples_pos,
        principal_final=principal_final,
        jm_ant_corrigido=jm_ant_corrigido,
        total_corrigido=total_corrigido,
        meses_antes=len(meses_antes),
        meses_pos=n_pos,
    )

# ----------------------------- CLI -------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Atualização por IPCA-E (PSC): ANTES(formacao ou full) + PÓS(IPCA-E + 2% a.a. simples); JM anteriores corrigidos."
    )
    p.add_argument("--principal", required=True, type=str, help="Valor do principal (ex.: 1097665.34)")
    p.add_argument("--ano-venc", required=True, type=int, help="Ano de vencimento (ex.: 2008)")
    p.add_argument("--indices-csv", default="indices_ipcae.csv", help="CSV de índices")
    p.add_argument("--pos-fim", default=None, help="YYYY-MM do fim do período PÓS (ex.: 2025-10). Se ausente, usa o último mês do CSV.")
    p.add_argument("--juros-aa-pos", default="0.02", help="Juros a.a. simples no PÓS (default 0.02 = 2%)")
    p.add_argument("--juros-mora-ant", default="0", help="Valor de Juros de Mora ANTERIORES (será corrigido pelos mesmos fatores).")
    p.add_argument("--antes-mode", choices=["formacao","full"], default="formacao",
                   help="Modo do período ANTES: 'formacao' (07/(av-1)..12/(av)) ou 'full' (07/(av-1)..11/2021). Default: formacao.")
    p.add_argument("--override-antes", default=None, help="Se informado, usa este fator para o ANTES (ex.: 1.08370280).")
    p.add_argument("--override-pos-ipca", default=None, help="Se informado, usa este fator para o PÓS (IPCA-E) (ex.: 1.21414986).")
    p.add_argument("--clip-pos", action="store_true",
                   help="Se --pos-fim não existir no CSV, ajusta para o último mês disponível (com aviso).")
    p.add_argument("--debug", action="store_true", help="Lista fatores mês a mês.")
    return p.parse_args()

def main():
    args = parse_args()
    principal = d(args.principal)
    juros_aa_pos = d(args.juros_aa_pos)
    juros_mora_ant = d(args.juros_mora_ant)

    indices = Indices.from_csv(args.indices_csv)

    # pos_fim
    pos_fim_tuple = None
    if args.pos_fim:
        try:
            y, m = args.pos_fim.split("-")
            pos_fim_tuple = (int(y), int(m))
        except Exception:
            raise ValueError("--pos-fim inválido. Use YYYY-MM.")

    last_y, last_m = indices.last_available_month()
    if pos_fim_tuple is not None and pos_fim_tuple > (last_y, last_m):
        if args.clip_pos:
            print(f"Aviso: --pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe no CSV; "
                  f"ajustei para {last_y:04d}-{last_m:02d}.")
            pos_fim_tuple = (last_y, last_m)
        else:
            raise KeyError(f"--pos-fim {pos_fim_tuple[0]:04d}-{pos_fim_tuple[1]:02d} não existe (último={last_y:04d}-{last_m:02d}). "
                           "Use --clip-pos para ajustar automaticamente.")
    if pos_fim_tuple is None:
        pos_fim_tuple = (last_y, last_m)

    override_antes = d(args.override_antes) if args.override_antes is not None else None
    override_pos_ipca = d(args.override_pos_ipca) if args.override_pos_ipca is not None else None

    calcular(principal=principal, ano_venc=int(args.ano_venc),
             indices=indices, pos_fim=pos_fim_tuple,
             juros_aa_pos=juros_aa_pos,
             juros_mora_ant=juros_mora_ant,
             antes_mode=args.antes_mode,
             override_antes=override_antes,
             override_pos_ipca=override_pos_ipca,
             debug=args.debug)

if __name__ == "__main__":
    main()
