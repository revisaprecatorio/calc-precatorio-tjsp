#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Baixa o arquivo 'ipca-e_SerieHist.zip' do IBGE.
- Verifica a presença do item no HTML da página de downloads (jstree dinâmico).
- Faz o download direto da cópia oficial no FTP (via HTTPS), que o próprio site indica.
- Valida o ZIP após baixar.

Uso:
  python baixar_ipcae.py --saida "C:\\Users\\seuusuario\\Downloads"
"""

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PAGE_URL = ("https://www.ibge.gov.br/estatisticas/economicas/precos-e-custos/"
            "9262-indice-nacional-de-precos-ao-consumidor-amplo-especial.html?=&t=downloads")

# Caminho público oficial (HTTPS) equivalente ao FTP do IBGE
FTP_FILE_URL = ("https://ftp.ibge.gov.br/Precos_Indices_de_Precos_ao_Consumidor/"
                "IPCA_E/Series_Historicas/ipca-e_SerieHist.zip")

TARGET_FILENAME = "ipca-e_SerieHist.zip"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}


def find_zip_mention_in_page(session: requests.Session) -> bool:
    """Verifica se o texto 'ipca-e_SerieHist.zip' aparece no HTML (jstree)."""
    try:
        r = session.get(PAGE_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"⚠️  Não consegui abrir a página do IBGE ({PAGE_URL}). Erro: {e}")
        return False

    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    return TARGET_FILENAME in text


def stream_download(session: requests.Session, url: str, dest_path: Path) -> None:
    """Baixa arquivo com barra de progresso simples."""
    with session.get(url, stream=True, headers=HEADERS, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", "0"))
        downloaded = 0
        chunk_size = 1024 * 64

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    sys.stdout.write(f"\r⬇️  Baixando {TARGET_FILENAME}... {pct}%")
                    sys.stdout.flush()
        if total:
            sys.stdout.write("\n")
    print(f"✅ Download concluído: {dest_path}")


def validate_zip(zip_path: Path) -> None:
    """Valida o ZIP e lista conteúdo."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise zipfile.BadZipFile(f"Arquivo corrompido: {bad}")
            names = zf.namelist()
            print("📦 Conteúdo do ZIP:")
            for n in names:
                print(f"   - {n}")
    except zipfile.BadZipFile as e:
        print(f"❌ ZIP inválido/corrompido: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Baixa ipca-e_SerieHist.zip do IBGE")
    parser.add_argument("--saida", "-o", default=".",
                        help="Diretório de destino para salvar o ZIP (default: diretório atual)")
    args = parser.parse_args()

    out_dir = Path(args.saida).expanduser().resolve()
    out_path = out_dir / TARGET_FILENAME

    with requests.Session() as s:
        print("🌐 Acessando página de downloads do IBGE…")
        found = find_zip_mention_in_page(s)
        if found:
            print("🔎 Item encontrado no HTML (jstree): 'ipca-e_SerieHist.zip'")
        else:
            print("ℹ️  Não consegui confirmar o item no HTML (página usa JS dinâmico). "
                  "Prosseguindo via repositório oficial (FTP/HTTPS).")

        print("🔗 Baixando do repositório oficial (FTP/HTTPS do IBGE)…")
        try:
            stream_download(s, FTP_FILE_URL, out_path)
        except requests.HTTPError as e:
            # Tenta variação de capitalização (raro, mas seguro)
            alt_url = FTP_FILE_URL.replace("ipca-e_SerieHist.zip", "IPCA-E_SerieHist.zip")
            print(f"⚠️  Erro HTTP ao baixar ({e}). Tentando variação: {alt_url}")
            stream_download(s, alt_url, out_path)

    print("🧪 Validando ZIP…")
    validate_zip(out_path)
    print("🎉 Pronto!")

if __name__ == "__main__":
    main()