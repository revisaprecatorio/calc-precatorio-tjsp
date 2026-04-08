import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def carregar_indices_csv():
    print("--- Carregando Índices ---")

    path_ipca = os.path.join(BASE_DIR, "indices.csv")
    path_selic = os.path.join(BASE_DIR, "indices_selic.csv")

    if not os.path.exists(path_ipca):
        path_ipca = os.path.join(BASE_DIR, "data", "indices", "indices.csv")
    if not os.path.exists(path_selic):
        path_selic = os.path.join(BASE_DIR, "data", "indices", "indices_selic.csv")

    try:
        # =========================
        # IPCA
        # =========================
        df_ipca = pd.read_csv(path_ipca)

        required_ipca = {"ano", "mes", "variacao_mensal"}
        missing_ipca = required_ipca - set(df_ipca.columns)
        if missing_ipca:
            raise ValueError(f"CSV IPCA sem colunas obrigatórias: {missing_ipca}")

        df_ipca["ano"] = pd.to_numeric(df_ipca["ano"], errors="coerce")
        df_ipca["mes"] = pd.to_numeric(df_ipca["mes"], errors="coerce")
        df_ipca["variacao_mensal"] = pd.to_numeric(df_ipca["variacao_mensal"], errors="coerce")

        df_ipca = df_ipca.dropna(subset=["ano", "mes", "variacao_mensal"]).copy()
        df_ipca["ano"] = df_ipca["ano"].astype(int)
        df_ipca["mes"] = df_ipca["mes"].astype(int)

        df_ipca["data_ref"] = pd.to_datetime(
            df_ipca.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1)
        )
        df_ipca = df_ipca.sort_values("data_ref").drop_duplicates(subset=["data_ref"], keep="last")
        df_ipca.set_index("data_ref", inplace=True)

        # =========================
        # SELIC
        # =========================
        df_selic = pd.read_csv(path_selic)

        required_selic = {"ano", "mes", "variacao_mensal"}
        missing_selic = required_selic - set(df_selic.columns)
        if missing_selic:
            raise ValueError(f"CSV SELIC sem colunas obrigatórias: {missing_selic}")

        df_selic["ano"] = pd.to_numeric(df_selic["ano"], errors="coerce")
        df_selic["mes"] = pd.to_numeric(df_selic["mes"], errors="coerce")
        df_selic["variacao_mensal"] = pd.to_numeric(df_selic["variacao_mensal"], errors="coerce")

        df_selic = df_selic.dropna(subset=["ano", "mes", "variacao_mensal"]).copy()
        df_selic["ano"] = df_selic["ano"].astype(int)
        df_selic["mes"] = df_selic["mes"].astype(int)

        # IMPORTANTE:
        # o gerar_selic_csv.py já grava variacao_mensal como taxa mensal em fração
        # ex: 0,45% -> 0.0045
        # então aqui basta usar diretamente
        df_selic["fator_mensal"] = df_selic["variacao_mensal"]

        df_selic["data_ref"] = pd.to_datetime(
            df_selic.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1)
        )
        df_selic = df_selic.sort_values("data_ref").drop_duplicates(subset=["data_ref"], keep="last")
        df_selic.set_index("data_ref", inplace=True)

        print(f"IPCA carregado: {len(df_ipca)} linhas | {df_ipca.index.min().date()} até {df_ipca.index.max().date()}")
        print(f"SELIC carregada: {len(df_selic)} linhas | {df_selic.index.min().date()} até {df_selic.index.max().date()}")

        return df_ipca, df_selic

    except Exception as e:
        print(f"Erro ao ler CSVs de índices: {e}")
        sys.exit(1)


def calcular_fim_graca(data_req_obj):
    if not data_req_obj or pd.isna(data_req_obj):
        return pd.Timestamp.max
    try:
        dt = pd.to_datetime(data_req_obj)
        ano_base = dt.year + 1
        if dt.month > 7 or (dt.month == 7 and dt.day > 1):
            ano_base += 1
        return pd.Timestamp(f"{ano_base}-12-31")
    except Exception:
        return pd.Timestamp.max