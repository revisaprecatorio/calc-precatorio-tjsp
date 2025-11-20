from main import carregar_indices_csv, calcular_fim_graca, DATA_CORTE_EC113, JUROS_MORA_MENSAL
import pandas as pd
import sys

# --- CONFIGURAÇÃO ---
ID_PARA_TESTE = 123  # <--- COLOQUE AQUI O ID DE UM PROCESSO REAL DO SEU BANCO
# OU DADOS MANUAIS PARA SIMULAÇÃO:
DADOS_MANUAIS = {
    "principal": 52228.43,
    "juros_base": 16702.17,
    "data_req": "2009-10-31"
}
USAR_DADOS_MANUAIS = True

def auditar():
    print(f"=== AUDITORIA DETALHADA (EC113) ===")
    
    # 1. Carrega Índices
    df_ipca, df_selic = carregar_indices_csv()
    
    # 2. Define dados
    if USAR_DADOS_MANUAIS:
        principal = DADOS_MANUAIS["principal"]
        juros_base = DADOS_MANUAIS["juros_base"]
        dt_req = pd.to_datetime(DADOS_MANUAIS["data_req"])
    else:
        # Aqui você conectaria no banco para buscar pelo ID
        print("Configure a conexão para buscar do banco ou use DADOS_MANUAIS = True")
        return

    fim_graca = calcular_fim_graca(dt_req)
    print(f"Data Base: {dt_req.date()}")
    print(f"Fim da Graça: {fim_graca.date()}")
    print(f"Data EC113: {DATA_CORTE_EC113.date()}")
    print("-" * 60)
    print(f"{'MÊS':<10} | {'ÍNDICE':<10} | {'FATOR':<10} | {'PRINCIPAL':<12} | {'JUROS ACUM':<12} | {'REGRA'}")
    print("-" * 60)

    cursor = dt_req.replace(day=1) + pd.DateOffset(months=1)
    hoje = pd.Timestamp.now().replace(day=1)
    
    saldo_princ = principal
    saldo_juros = 0.0
    
    while cursor <= hoje:
        msg_regra = ""
        fator_aplicado = 0.0
        
        # FASE 1: IPCA-E
        if cursor < DATA_CORTE_EC113:
            try: idx = float(df_ipca.loc[cursor]['variacao_mensal'])
            except: idx = 0.0
            
            fator = 1 + idx
            saldo_princ *= fator
            fator_aplicado = idx
            
            # Juros
            if cursor > fim_graca:
                juros_mes = saldo_princ * JUROS_MORA_MENSAL
                saldo_juros += juros_mes
                msg_regra = "IPCA + Juros"
            else:
                msg_regra = "IPCA (Graça)"

        # FASE 2: SELIC
        else:
            try: idx = float(df_selic.loc[cursor]['fator_mensal'])
            except: idx = 0.008
            
            fator = 1 + idx
            # Selic corrige tudo
            saldo_princ *= fator
            saldo_juros *= fator 
            fator_aplicado = idx
            msg_regra = "SELIC (Global)"

        print(f"{cursor.strftime('%Y-%m'):<10} | {fator_aplicado:.6f}   | {fator:.6f}   | {saldo_princ:,.2f}    | {saldo_juros:,.2f}     | {msg_regra}")
        
        cursor = cursor + pd.DateOffset(months=1)

    print("-" * 60)
    print(f"TOTAL FINAL: R$ {saldo_princ + saldo_juros:,.2f}")

if __name__ == "__main__":
    auditar()