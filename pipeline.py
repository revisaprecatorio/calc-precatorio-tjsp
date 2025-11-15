#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pipeline.py

Executa o fluxo de trabalho completo de pré-processamento:
1. Baixa IPCA-E (IBGE)
2. Baixa SELIC (BCB)
3. Descompacta IPCA-E (usando 'descompacta.py')
4. Gera CSV do IPCA-E (encontra o .xls automaticamente)
5. Gera CSV da SELIC
6. Executa o cálculo principal ('main.py')

O pipeline para imediatamente se qualquer etapa falhar.
"""

import subprocess
import sys
from pathlib import Path

def run_command(cmd_list: list[str], title: str):
    """
    Executa um comando de subprocesso e para o script em caso de erro.
    """
    print("\n" + "="*60)
    print(f"INICIANDO: {title}")
    # Converte todos os argumentos para string
    cmd_list_str = [str(item) for item in cmd_list]
    print(f"COMANDO: {' '.join(cmd_list_str)}")
    print("="*60)
    
    try:
        # Executa o comando
        # check=True: levanta um erro se o comando falhar (exit code != 0)
        # text=True: usa encoding de texto (assume utf-8)
        subprocess.run(cmd_list_str, check=True, text=True, encoding='utf-8')
        
        print(f"\n--- SUCESSO: {title} ---")
        
    except subprocess.CalledProcessError as e:
        # Erro de execução (script falhou)
        print("\n" + "X"*60)
        print(f"ERRO AO EXECUTAR: {title}")
        print(f"O COMANDO FALHOU (Código de Saída: {e.returncode})")
        print(f"Comando: {' '.join(e.cmd)}")
        print("X"*60)
        print("\nPipeline interrompido.")
        sys.exit(1) # Interrompe o pipeline
        
    except FileNotFoundError:
        # Erro (script .py não encontrado ou python não está no PATH)
        print("\n" + "X"*60)
        print(f"ERRO: Comando não encontrado: '{cmd_list_str[0]}'")
        print("Verifique se o Python está no PATH e se o script .py existe no diretório.")
        print("X"*60)
        print("\nPipeline interrompido.")
        sys.exit(1) # Interrompe o pipeline

def main():
    """Define e executa todas as etapas do pipeline."""
    
    # Usa sys.executable para garantir que estamos usando o mesmo
    # interpretador Python que está executando este pipeline.
    python_exe = sys.executable
    
    # --- 1. Baixar IPCA-E ---
    cmd_baixar_ipcae = [python_exe, "baixar.py"]
    run_command(cmd_baixar_ipcae, "1. Baixando arquivo IPCA-E (IBGE)")
    
    # --- 2. Baixar SELIC ---
    cmd_baixar_selic = [python_exe, "baixar_selic.py", "--saida", "selic_mensal.json"]
    run_command(cmd_baixar_selic, "2. Baixando dados SELIC (BCB)")
    
    # --- 3. Descompactar IPCA-E ---
    # CORRIGIDO: Chamando o nome de ficheiro correto 'descompacta.py'
    cmd_descompactar = [python_exe, "descompacta.py"]
    run_command(cmd_descompactar, "3. Descompactando arquivo IPCA-E")
    
    # --- 4. Gerar CSV do IPCA-E (com detecção automática) ---
    print("\n[INFO] Procurando arquivo .xls do IPCA-E em 'ipca-e_SerieHist/'...")
    xls_dir = Path("ipca-e_SerieHist")
    if not xls_dir.is_dir():
        print(f"ERRO: Diretório não encontrado: '{xls_dir.resolve()}'")
        print("Certifique-se que 'descompacta.py' criou este diretório.")
        sys.exit(1)
        
    # Encontra o primeiro arquivo .xls dentro do diretório
    xls_files = list(xls_dir.glob("*.xls"))
    if not xls_files:
        print(f"ERRO: Nenhum arquivo .xls encontrado em '{xls_dir.resolve()}'")
        sys.exit(1)
        
    ipcae_xls_path = xls_files[0]
    print(f"[INFO] Arquivo IPCA-E encontrado: {ipcae_xls_path}")

    # Argumentos originais do seu comando
    cmd_gerar_ipcae = [
        python_exe, "gerar_indices_csv.py",
        "--xls", str(ipcae_xls_path),
        "--sheet", "SÉRIE HISTÓRICA",
        "--indice", "IPCA-E",
        "--out", "indices.csv",
        "--header-row", "3",
        "--year-col", "ANO",
        "--month-col", "MÊS",
        "--var-col", "VARIAÇÃO (%)",
        "--debug",
    ]
    run_command(cmd_gerar_ipcae, "4. Gerando CSV do IPCA-E")
    
    # --- 5. Gerar CSV da SELIC ---
    cmd_gerar_selic = [
        python_exe, "gerar_selic_csv.py",
        "--json", "selic_mensal.json",
        "--out", "indices_selic.csv",
    ]
    run_command(cmd_gerar_selic, "5. Gerando CSV da SELIC")
    
    # --- 6. Executar Cálculo Principal ---
    cmd_main = [python_exe, "main.py"]
    run_command(cmd_main, "6. Executando 'main.py' (Cálculo Principal)")
    
    print("\n" + "*"*60)
    print("PIPELINE DE PRÉ-PROCESSAMENTO CONCLUÍDO COM SUCESSO!")
    print("*"*60)

if __name__ == "__main__":
    main()