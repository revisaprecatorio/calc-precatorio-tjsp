# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
from typing import List, Dict, Tuple, Optional
import csv
import os

@dataclass(frozen=True)
class Mensal:
    ano: int
    mes: int
    variacao: Decimal  # fração mensal (ex.: 0.0031 = 0,31%)

class IndicesProvider:
    """
    Provedor simples: lê um CSV local (indices.csv) com colunas:
      indice,ano,mes,variacao_mensal
    Ex.: IPCA-E,2021,1,0.0031
    """
    def __init__(self, csv_path: str = "indices.csv"):
        self.csv_path = csv_path
        self._cache: Dict[Tuple[str,int,int], Decimal] = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(
                f"Arquivo {self.csv_path} não encontrado. "
                "Crie um CSV com colunas: indice,ano,mes,variacao_mensal"
            )
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                indice = row["indice"].strip()
                ano = int(row["ano"])
                mes = int(row["mes"])
                # aceita ponto ou vírgula
                # aceita ponto ou vírgula
                vraw = str(row["variacao_mensal"]).strip().replace(",", ".")
                try:
                    val = Decimal(vraw)
                except Exception:
                    continue

                # CONVERSÃO DEFENSIVA:
                # se |val| >= 0.02 (2%), assume que veio em "porcento" e divide por 100
                if val.copy_abs() >= Decimal("0.02"):
                    val = (val / Decimal("100"))

                self._cache[(indice, ano, mes)] = val
        self._loaded = True


    def get_series(self, indice: str, start: date, end: date) -> List[Decimal]:
        """
        Retorna lista de variações mensais (fração) de start..end-1 (mês a mês).
        end é exclusivo (boa prática para intervalos).
        """
        self._load()
        cur = date(start.year, start.month, 1)
        series = []
        while cur < end:
            key = (indice, cur.year, cur.month)
            if key not in self._cache:
                raise KeyError(f"Série ausente p/ {indice} {cur.year}-{cur.month:02d} no CSV.")
            series.append(self._cache[key])
            # avança 1 mês
            ny = cur.year + (1 if cur.month == 12 else 0)
            nm = 1 if cur.month == 12 else cur.month + 1
            cur = date(ny, nm, 1)
        return series

def split_periodos(provider: IndicesProvider,
                   indice: str,
                   inicio_antes: date, fim_antes: date,
                   inicio_graca: Optional[date], fim_graca: Optional[date],
                   inicio_pos: date, fim_pos: date):
    """
    Devolve (antes, graca, pos) como listas de frações mensais.
    Datas: início incluso, fim exclusivo.
    Se não houver 'graça', passe None/None que devolve [].
    """
    antes = provider.get_series(indice, inicio_antes, fim_antes)
    graca = provider.get_series(indice, inicio_graca, fim_graca) if (inicio_graca and fim_graca) else []
    pos   = provider.get_series(indice, inicio_pos, fim_pos)
    return antes, graca, pos
