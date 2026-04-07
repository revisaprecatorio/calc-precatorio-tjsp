import pandas as pd
from financial import carregar_indices_csv, calcular_fim_graca

DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")
DATA_CORTE_LEI_11960 = pd.Timestamp("2009-06-29")
JUROS_MORA_MENSAL_POS_2009 = 0.005
JUROS_6AA_MENSAL = 0.06 / 12.0


def safe_index_value(df, idx, col, default=0.0):
    try:
        if idx not in df.index:
            return default
        val = df.loc[idx, col]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        return float(val)
    except Exception:
        return default


def auditar(
    principal_base: float,
    jm_ant_base: float,
    dt_base: str,
    stop_at: str,
):
    df_ipca, df_selic = carregar_indices_csv()

    dt_base = pd.to_datetime(dt_base)
    stop_at_ts = pd.Timestamp(f"{stop_at}-01")
    fim_graca = calcular_fim_graca(dt_base)

    cursor_data = dt_base.replace(day=1) + pd.DateOffset(months=1)

    saldo_principal = float(principal_base)
    saldo_jm_ant = float(jm_ant_base)
    jm_novo = 0.0

    while cursor_data <= stop_at_ts:
        if cursor_data < DATA_CORTE_EC113:
            idx_ipca = safe_index_value(df_ipca, cursor_data, "variacao_mensal", 0.0)
            fator_mes = 1 + idx_ipca

            saldo_principal *= fator_mes
            saldo_jm_ant *= fator_mes

            if cursor_data > fim_graca:
                if cursor_data <= DATA_CORTE_LEI_11960:
                    taxa_jm = JUROS_6AA_MENSAL
                else:
                    taxa_jm = JUROS_MORA_MENSAL_POS_2009
                jm_novo += saldo_principal * taxa_jm
        else:
            taxa_selic = safe_index_value(df_selic, cursor_data, "fator_mensal", 0.008)
            fator_mes = 1 + taxa_selic

            saldo_principal *= fator_mes
            saldo_jm_ant *= fator_mes
            jm_novo *= fator_mes

        total = saldo_principal + saldo_jm_ant + jm_novo
        print(
            f"{cursor_data.strftime('%Y-%m')}: "
            f"principal={saldo_principal:,.2f} jm_ant={saldo_jm_ant:,.2f} jm_novo={jm_novo:,.2f} total={total:,.2f}"
        )

        cursor_data += pd.DateOffset(months=1)


if __name__ == "__main__":
    # Exemplo:
    # auditar(45009.08, 0.0, "2014-07-31", "2025-12")
    pass
