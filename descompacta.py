#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
descompacta.py

Descompacta o 'ipca-e_SerieHist.zip' (encontrado no mesmo diretório)
para a pasta 'ipca-e_SerieHist/'.
"""

import zipfile
from pathlib import Path
import sys

ZIP_FILENAME = "ipca-e_SerieHist.zip"
# O pipeline (passo 4) espera que os arquivos estejam em 'ipca-e_SerieHist/'
EXTRACT_DIR = Path("ipca-e_SerieHist") 

def main():
    zip_path = Path(ZIP_FILENAME)
    
    if not zip_path.exists():
        print(f"❌ ERRO: Arquivo ZIP não encontrado: '{ZIP_FILENAME}'", file=sys.stderr)
        print("💡 (Passo 1 'baixar.py' falhou ou foi pulado?)", file=sys.stderr)
        sys.exit(1)
        
    try:
        # Garante que o diretório de destino existe
        EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
        
        print(f"📦 Descompactando '{zip_path}' para o diretório '{EXTRACT_DIR.resolve()}'...")
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(EXTRACT_DIR)
            
        print("✅ Descompactado com sucesso.")
        print("Conteúdo extraído:")
        for f in EXTRACT_DIR.glob('*'):
            print(f"   - {f.name}")

    except zipfile.BadZipFile:
        print(f"❌ ERRO: O arquivo '{zip_path}' não é um ZIP válido ou está corrompido.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ ERRO inesperado ao descompactar: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()