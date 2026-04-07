import os
import sys
import pandas as pd

# Pega a pasta onde este arquivo está (a raiz)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def carregar_indices_csv():
    print("--- Carregando Índices ---")

    # Tenta achar os CSVs na raiz ou na pasta data/indices
    path_ipca = os.path.join(BASE_DIR, "indices.csv")
    path_selic = os.path.join(BASE_DIR, "indices_selic.csv")

    # Fallback: Se não estiver na raiz, tenta na pasta data/indices
    if not os.path.exists(path_ipca):
        path_ipca = os.path.join(BASE_DIR, "data", "indices", "indices.csv")
    if not os.path.exists(path_selic):
        path_selic = os.path.join(BASE_DIR, "data", "indices", "indices_selic.csv")

    def _parse_num_ptbr(x):
        """
        Converte strings PT-BR e identifica porcentagem:
          "1,01%" -> (1.01, True)
          "0,73"  -> (0.73, False)
          "0,0101"-> (0.0101, False)
          0.0101  -> (0.0101, False)
        """
        if x is None:
            return 0.0, False
        if isinstance(x, (int, float)):
            return float(x), False

        s = str(x).strip()
        if not s:
            return 0.0, False

        has_pct = "%" in s
        s = s.replace("%", "").strip()

        # se tem vírgula, assume pt-br: "." milhar e "," decimal
        if "," in s:
            s = s.replace(".", "")
            s = s.replace(",", ".")
        return float(s), has_pct

    def normalizar_selic_mensal(val):
        """
        Normaliza para taxa MENSAL em FRAÇÃO (ex.: 1,01% -> 0.0101)

        Aceita:
          - "1,01%"  -> 0.0101
          - "1,01"   -> 0.0101 (assume % se parecer percentual)
          - "0,0101" -> 0.0101 (já fração)
          - "1,0101" -> 0.0101 (se veio como fator)
          - "0,73"   -> 0.0073 (percentual)
        """
        v, has_pct = _parse_num_ptbr(val)

        if has_pct:
            return v / 100.0

        # veio como fator (1.00xx)
        if 1.0 <= v < 1.2:
            return v - 1.0

        # veio como percentual (0.05 a 5.0) -> divide por 100
        if 0.05 <= v <= 5.0:
            return v / 100.0

        # já veio como fração mensal
        if 0.0 <= v < 0.05:
            return v

        # fora disso, quase certeza coluna errada (ex.: 100,85)
        raise ValueError(f"SELIC mensal fora do padrão: raw={val} parsed={v}")

    try:
        # 1) IPCA
        df_ipca = pd.read_csv(path_ipca)
        df_ipca["data_ref"] = pd.to_datetime(
            df_ipca.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1)
        )
        df_ipca.set_index("data_ref", inplace=True)

        # 2) SELIC (mensal)
        df_selic = pd.read_csv(path_selic)

        # ✅ Escolha explícita da coluna da taxa (evita pegar coluna errada)
        if "variacao_mensal" in df_selic.columns:
            col_val = "variacao_mensal"
        elif "valor" in df_selic.columns:
            col_val = "valor"
        elif "taxa" in df_selic.columns:
            col_val = "taxa"
        else:
            # fallback (mantém compatibilidade), mas pode estar errado se o CSV tiver colunas extras
            col_val = df_selic.columns[-1]

        df_selic["data_ref"] = pd.to_datetime(
            df_selic.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1)
        )

        df_selic["fator_mensal"] = df_selic[col_val].apply(normalizar_selic_mensal)
        df_selic.set_index("data_ref", inplace=True)

        # ✅ Sanity check: SELIC mensal não deve passar de 5% (0.05)
        mx = float(df_selic["fator_mensal"].max())
        if mx > 0.05:
            raise ValueError(
                f"SELIC mensal muito alta (max={mx}). "
                f"Provável coluna errada ({col_val}) ou percentual não convertido."
            )

        return df_ipca, df_selic

    except Exception as e:
        print(f"Erro ao ler/normalizar CSVs: {e}")
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
