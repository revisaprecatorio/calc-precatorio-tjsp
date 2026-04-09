#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs_pipeline"
LOG_DIR.mkdir(exist_ok=True)

ARQ_IPCA_CSV = BASE_DIR / "indices.csv"
ARQ_SELIC_CSV = BASE_DIR / "indices_selic.csv"
ARQ_SELIC_JSON = BASE_DIR / "selic_mensal.json"
ARQ_IPCA_ZIP = BASE_DIR / "ipca-e_SerieHist.zip"
DIR_IPCA_EXTRACT = BASE_DIR / "ipca-e_SerieHist"

SCRIPT_BAIXAR_SELIC = BASE_DIR / "baixar_selic.py"
SCRIPT_GERAR_SELIC = BASE_DIR / "gerar_selic_csv.py"

SCRIPT_BAIXAR_IPCA = BASE_DIR / "baixar.py"
SCRIPT_DESCOMPACTA_IPCA = BASE_DIR / "descompacta.py"
SCRIPT_GERAR_IPCA = BASE_DIR / "gerar_indices_csv.py"

SCRIPT_MAIN = BASE_DIR / "main.py"

IPCA_SHEET = "SÉRIE HISTÓRICA"
IPCA_INDICE = "IPCA-E"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ts_file() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg: str, fh=None):
    linha = f"[{now_str()}] {msg}"
    print(linha)
    if fh:
        fh.write(linha + "\n")
        fh.flush()


def run_cmd(cmd: List[str], fh=None, cwd: Optional[Path] = None) -> None:
    log(f"Executando: {' '.join(cmd)}", fh)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd or BASE_DIR),
        text=True,
        capture_output=True,
        shell=False
    )

    if proc.stdout:
        for line in proc.stdout.splitlines():
            log(f"STDOUT | {line}", fh)

    if proc.stderr:
        for line in proc.stderr.splitlines():
            log(f"STDERR | {line}", fh)

    if proc.returncode != 0:
        raise RuntimeError(f"Comando falhou com exit code {proc.returncode}: {' '.join(cmd)}")


def find_latest_ipca_xls() -> Path:
    candidatos = list(DIR_IPCA_EXTRACT.glob("*.xls")) + list(DIR_IPCA_EXTRACT.glob("*.xlsx"))
    if not candidatos:
        raise FileNotFoundError("Nenhum arquivo .xls/.xlsx encontrado em ipca-e_SerieHist/")
    candidatos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidatos[0]


def read_csv_months(csv_path: Path) -> List[Tuple[int, int, float]]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for row in rd:
            try:
                ano = int(str(row["ano"]).strip())
                mes = int(str(row["mes"]).strip())
                var = float(str(row["variacao_mensal"]).strip().replace(",", "."))
                rows.append((ano, mes, var))
            except Exception:
                continue
    return rows


def validate_monthly_csv(
    csv_path: Path,
    nome: str,
    min_rows: int,
    max_variacao: Optional[float],
    fh=None
) -> Tuple[int, int]:
    if not csv_path.exists():
        raise FileNotFoundError(f"{nome}: arquivo não encontrado: {csv_path}")

    rows = read_csv_months(csv_path)

    if len(rows) < min_rows:
        raise ValueError(f"{nome}: poucas linhas no CSV ({len(rows)}).")

    vistos = set()
    ultimo_ano_mes = None

    for ano, mes, var in rows:
        if not (1900 <= ano <= 2100):
            raise ValueError(f"{nome}: ano inválido encontrado: {ano}")
        if not (1 <= mes <= 12):
            raise ValueError(f"{nome}: mês inválido encontrado: {mes}")
        chave = (ano, mes)
        if chave in vistos:
            raise ValueError(f"{nome}: duplicidade encontrada para {ano}-{mes:02d}")
        vistos.add(chave)

        if max_variacao is not None and abs(var) > max_variacao:
            raise ValueError(
                f"{nome}: variação fora da faixa esperada em {ano}-{mes:02d}: {var}"
            )

        ultimo_ano_mes = chave

    if ultimo_ano_mes is None:
        raise ValueError(f"{nome}: CSV sem dados válidos.")

    log(
        f"{nome}: validado com sucesso | linhas={len(rows)} | último_mês={ultimo_ano_mes[0]}-{ultimo_ano_mes[1]:02d}",
        fh
    )
    return ultimo_ano_mes


def atualizar_selic(fh=None):
    log("=== ETAPA: Atualização SELIC ===", fh)

    run_cmd([sys.executable, str(SCRIPT_BAIXAR_SELIC)], fh=fh, cwd=BASE_DIR)

    if not ARQ_SELIC_JSON.exists():
        raise FileNotFoundError(f"Arquivo JSON SELIC não encontrado: {ARQ_SELIC_JSON}")

    run_cmd(
        [
            sys.executable,
            str(SCRIPT_GERAR_SELIC),
            "--json", str(ARQ_SELIC_JSON),
            "--out", str(ARQ_SELIC_CSV),
        ],
        fh=fh,
        cwd=BASE_DIR
    )

    ultimo = validate_monthly_csv(
        csv_path=ARQ_SELIC_CSV,
        nome="SELIC",
        min_rows=12,
        max_variacao=0.03,  # ~3% ao mês teto operacional
        fh=fh
    )
    return ultimo


def atualizar_ipca(fh=None):
    log("=== ETAPA: Atualização IPCA-E ===", fh)

    run_cmd([sys.executable, str(SCRIPT_BAIXAR_IPCA)], fh=fh, cwd=BASE_DIR)
    run_cmd([sys.executable, str(SCRIPT_DESCOMPACTA_IPCA)], fh=fh, cwd=BASE_DIR)

    planilha_ipca = find_latest_ipca_xls()
    log(f"Planilha IPCA localizada: {planilha_ipca.name}", fh)

    run_cmd(
        [
            sys.executable,
            str(SCRIPT_GERAR_IPCA),
            "--xls", str(planilha_ipca),
            "--sheet", IPCA_SHEET,
            "--indice", IPCA_INDICE,
            "--out", str(ARQ_IPCA_CSV),
            "--debug",
        ],
        fh=fh,
        cwd=BASE_DIR
    )

    ultimo = validate_monthly_csv(
        csv_path=ARQ_IPCA_CSV,
        nome="IPCA-E",
        min_rows=12,
        max_variacao=0.10,  # teto de sanidade
        fh=fh
    )
    return ultimo


def validar_indices_em_conjunto(fh=None):
    log("=== ETAPA: Validação conjunta dos índices ===", fh)

    ultimo_ipca = validate_monthly_csv(
        csv_path=ARQ_IPCA_CSV,
        nome="IPCA-E",
        min_rows=12,
        max_variacao=0.10,
        fh=fh
    )
    ultimo_selic = validate_monthly_csv(
        csv_path=ARQ_SELIC_CSV,
        nome="SELIC",
        min_rows=12,
        max_variacao=0.03,
        fh=fh
    )

    data_limite = min(ultimo_ipca, ultimo_selic)
    log(
        f"Data limite operacional dos cálculos: {data_limite[0]}-{data_limite[1]:02d}",
        fh
    )
    return {
        "ultimo_ipca": ultimo_ipca,
        "ultimo_selic": ultimo_selic,
        "data_limite": data_limite,
    }


def rodar_calculo(cpf: Optional[str], fh=None):
    log("=== ETAPA: Cálculo de precatórios ===", fh)

    cmd = [sys.executable, str(SCRIPT_MAIN)]
    if cpf:
        cmd.extend(["--cpf", cpf])

    run_cmd(cmd, fh=fh, cwd=BASE_DIR)
    log("Cálculo finalizado com sucesso.", fh)


def main():
    parser = argparse.ArgumentParser(description="Pipeline de atualização de índices e cálculo de precatórios.")
    parser.add_argument("--cpf", type=str, default=None, help="Processa somente este CPF no cálculo final.")
    parser.add_argument("--somente-indices", action="store_true", help="Atualiza e valida índices, sem rodar cálculo.")
    parser.add_argument("--somente-calculo", action="store_true", help="Roda apenas o cálculo, sem atualizar índices.")
    args = parser.parse_args()

    if args.somente_indices and args.somente_calculo:
        raise ValueError("Use apenas um entre --somente-indices e --somente-calculo.")

    log_path = LOG_DIR / f"pipeline_{ts_file()}.log"
    with open(log_path, "w", encoding="utf-8") as fh:
        try:
            log("########## INÍCIO DO PIPELINE ##########", fh)
            log(f"Base dir: {BASE_DIR}", fh)

            if not args.somente_calculo:
                ultimo_selic = atualizar_selic(fh)
                ultimo_ipca = atualizar_ipca(fh)
                resumo = validar_indices_em_conjunto(fh)

                log(
                    f"Resumo índices | SELIC={ultimo_selic[0]}-{ultimo_selic[1]:02d} | "
                    f"IPCA-E={ultimo_ipca[0]}-{ultimo_ipca[1]:02d} | "
                    f"LIMITE={resumo['data_limite'][0]}-{resumo['data_limite'][1]:02d}",
                    fh
                )

            if not args.somente_indices:
                rodar_calculo(args.cpf, fh)

            log("########## PIPELINE FINALIZADO COM SUCESSO ##########", fh)
            log(f"Log salvo em: {log_path}", fh)

        except Exception as e:
            log(f"ERRO FATAL NO PIPELINE: {e}", fh)
            log(f"Log salvo em: {log_path}", fh)
            raise


if __name__ == "__main__":
    main()