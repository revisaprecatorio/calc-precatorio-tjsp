import os
import sys
import pandas as pd
import numpy as np

# Pega a pasta onde este arquivo está (a raiz)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def carregar_indices_csv():
    print("--- Carregando Índices ---")
    
    # Tenta achar os CSVs na raiz ou na pasta data/indices
    path_ipca = os.path.join(BASE_DIR, "indices.csv")
    path_selic = os.path.join(BASE_DIR, "indices_selic.csv")

    # Fallback: Se não estiver na raiz, tenta na pasta data/indices (caso você tenha mantido)
    if not os.path.exists(path_ipca):
        path_ipca = os.path.join(BASE_DIR, "data", "indices", "indices.csv")
    if not os.path.exists(path_selic):
        path_selic = os.path.join(BASE_DIR, "data", "indices", "indices_selic.csv")

    try:
        # 1. IPCA
        df_ipca = pd.read_csv(path_ipca)
        df_ipca['data_ref'] = pd.to_datetime(df_ipca.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1))
        df_ipca.set_index('data_ref', inplace=True)

        # 2. SELIC
        df_selic = pd.read_csv(path_selic)
        col_val = df_selic.columns[-1] 
        df_selic['data_ref'] = pd.to_datetime(df_selic.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1))
        
        def converter_selic(val):
            try:
                v = float(val)
                if v > 1.0: v = v / 100.0 
                return (1 + v)**(1/12) - 1
            except:
                return 0.0
        
        df_selic['fator_mensal'] = df_selic[col_val].apply(converter_selic)
        df_selic.set_index('data_ref', inplace=True)
        
        return df_ipca, df_selic
    except Exception as e:
        print(f"Erro ao ler CSVs: {e}")
        sys.exit(1)

def calcular_fim_graca(data_req_obj):
    if not data_req_obj or pd.isna(data_req_obj): return pd.Timestamp.max
    try:
        dt = pd.to_datetime(data_req_obj)
        ano_base = dt.year + 1
        if dt.month > 7 or (dt.month == 7 and dt.day > 1):
            ano_base += 1
        return pd.Timestamp(f"{ano_base}-12-31")
    except:
        return pd.Timestamp.max