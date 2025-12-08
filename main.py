from __future__ import annotations
import os
import sys
from datetime import date, datetime
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import numpy as np

# Carrega variáveis do arquivo .env
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

# --- CONFIGURAÇÕES DO BANCO ---
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
# Tenta pegar senha de variaveis comuns
DB_PASS = os.getenv("DB_PASS") or os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT", "5432")

# --- CONFIGURAÇÕES FINANCEIRAS ---
# Data de corte da Emenda Constitucional 113 (Transição IPCA-E -> SELIC)
DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")
# Juros de Mora Fase 1 (0.5% a.m.)
JUROS_MORA_MENSAL = 0.005 

def get_db_connection():
    if not DB_PASS:
        print("Erro: Variável de senha (DB_PASS) não encontrada no .env")
        sys.exit(1)
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT
        )
        return conn
    except Exception as e:
        print(f"Erro de conexão ao PostgreSQL: {e}")
        sys.exit(1)

def carregar_indices_csv():
    """Carrega CSVs e trata conversão da SELIC anual para mensal."""
    print("--- Carregando Índices ---")
    try:
        # 1. IPCA-E
        df_ipca = pd.read_csv("indices.csv")
        df_ipca['data_ref'] = pd.to_datetime(df_ipca.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1))
        df_ipca.set_index('data_ref', inplace=True)

        # 2. SELIC
        df_selic = pd.read_csv("indices_selic.csv")
        col_val = df_selic.columns[-1] # Assume valor na última coluna
        df_selic['data_ref'] = pd.to_datetime(df_selic.apply(lambda x: f"{int(x['ano'])}-{int(x['mes']):02d}-01", axis=1))
        
        # Converte Taxa Anual para Fator Mensal Composto
        def converter_selic(val):
            try:
                v = float(val)
                if v > 1.0: v = v / 100.0 # Se 11.75 -> 0.1175
                # Fórmula: (1 + taxa)^(1/12) - 1
                return (1 + v)**(1/12) - 1
            except:
                return 0.0
        
        df_selic['fator_mensal'] = df_selic[col_val].apply(converter_selic)
        df_selic.set_index('data_ref', inplace=True)
        
        return df_ipca, df_selic
    except Exception as e:
        print(f"Erro crítico ao ler arquivos CSV: {e}")
        sys.exit(1)

def calcular_fim_graca(data_req_obj):
    """Calcula data fim do período de graça (Art. 100 CF)."""
    if not data_req_obj: return pd.Timestamp.max
    try:
        dt = pd.to_datetime(data_req_obj)
        ano_base = dt.year + 1
        # Se apresentado após 1º de Julho, pula mais um ano
        if dt.month > 7 or (dt.month == 7 and dt.day > 1):
            ano_base += 1
        return pd.Timestamp(f"{ano_base}-12-31")
    except:
        return pd.Timestamp.max

def main():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    df_ipca, df_selic = carregar_indices_csv()

    # --- 1. BUSCA PROCESSOS PENDENTES ---
    # Usa nomes exatos da sua tabela esaj_detalhe_processos
    sql_busca = """
        SELECT 
            id, 
            numero_ordem, 
            cpf, 
            numero_processo_cnj, 
            valor_total_requisitado AS valor_precatorio,
            saldo_final AS principal, 
            data_base_atualizacao AS data_requisitorio, 
            juros_moratorios AS juros_mora 
        FROM esaj_detalhe_processos 
        WHERE data_base_atualizacao IS NOT NULL
        AND (process_calculo IS FALSE OR process_calculo IS NULL)
    """ ""
    
    cursor.execute(sql_busca)
    processos = cursor.fetchall()
    
    if not processos:
        print(">>> Nenhum processo pendente de cálculo encontrado.")
        conn.close()
        return

    print(f">>> Iniciando processamento de {len(processos)} registros...")
    
    data_hoje = pd.Timestamp.now().replace(day=1)

    # --- 2. LOOP DE CÁLCULO ---
    for row in processos:
        pid = row['id']
        # Tratamento de CPF e Processo (limpeza básica)
        cpf_raw = str(row['cpf'])[:11] if row['cpf'] else '00000000000'
        proc_num = str(row['numero_processo_cnj'])[:30] if row['numero_processo_cnj'] else 'NDA'

        try:
            # Conversão segura de valores monetários
            def safe_float(v):
                if v is None: return 0.0
                return float(v)

            val_principal = safe_float(row['principal'])
            if val_principal == 0: val_principal = safe_float(row['valor_precatorio'])
            val_juros_base = safe_float(row['juros_mora'])
            
            dt_req = pd.to_datetime(row['data_requisitorio'])
            fim_graca = calcular_fim_graca(dt_req)

            print(f"ID {pid} | Processo {proc_num} | Base {dt_req.date()}")

            # Setup do Loop Temporal
            cursor_data = dt_req.replace(day=1) + pd.DateOffset(months=1)
            
            saldo_principal = val_principal
            saldo_juros_base = val_juros_base
            saldo_juros_mora_acum = 0.0
            
            # Contadores e Fatores para o Relatório
            fator_acum_ipca = 1.0
            fator_acum_selic = 1.0
            
            meses_fase1 = 0
            meses_fase2 = 0
            meses_com_juros = 0
            
            # Snapshot da Transição (Valores em Dez/21)
            principal_na_transicao = 0.0
            
            while cursor_data <= data_hoje:
                
                # --- FASE 1: ANTES DA EC113 (IPCA-E + JUROS) ---
                if cursor_data < DATA_CORTE_EC113:
                    try: idx = float(df_ipca.loc[cursor_data]['variacao_mensal'])
                    except: idx = 0.0
                    
                    fator = 1 + idx
                    fator_acum_ipca *= fator
                    
                    # IPCA corrige o Principal e os Juros Base
                    saldo_principal *= fator
                    saldo_juros_base *= fator
                    
                    # Juros de Mora (0.5%) se fora da graça
                    if cursor_data > fim_graca:
                        juros_mes = saldo_principal * JUROS_MORA_MENSAL
                        saldo_juros_mora_acum += juros_mes
                        meses_com_juros += 1
                    
                    meses_fase1 += 1

                # --- FASE 2: APÓS EC113 (SELIC) ---
                else:
                    # Registra valor no momento da virada (para coluna principal_apos_antes)
                    if principal_na_transicao == 0.0:
                        principal_na_transicao = saldo_principal
                    
                    try: taxa_selic = float(df_selic.loc[cursor_data]['fator_mensal'])
                    except: taxa_selic = 0.008 # Fallback
                    
                    fator_selic_mes = 1 + taxa_selic
                    fator_acum_selic *= fator_selic_mes
                    
                    # Selic corrige TUDO (Principal + Juros Base + Juros Acumulados)
                    saldo_principal *= fator_selic_mes
                    saldo_juros_base *= fator_selic_mes
                    saldo_juros_mora_acum *= fator_selic_mes
                    
                    meses_fase2 += 1
                
                cursor_data = (cursor_data + pd.DateOffset(months=1)).replace(day=1)

            # Totais Finais
            # Se não teve fase 1, o principal na transição é o original
            if principal_na_transicao == 0.0:
                principal_na_transicao = val_principal

            total_final = saldo_principal + saldo_juros_base + saldo_juros_mora_acum

            # --- 3. OPERAÇÕES SQL (INSERT + UPDATE) ---
            
            # A) INSERT na tabela resumo (Mapeamento exato do seu CREATE TABLE)
            sql_insert = """
                INSERT INTO esaj_calc_precatorio_resumo (
                    cpf,
                    numero_processo_cnj,
                    principal_original,
                    
                    -- Fatores Acumulados
                    fator_ipcae_antes,
                    fator_ipcae_pos,
                    
                    -- Valores Intermediários (Transição)
                    principal_apos_antes,
                    
                    -- Valores Finais
                    principal_final_ipca_2aa,
                    principal_final,
                    
                    -- Juros
                    juros_mora_anteriores_base,
                    juros_mora_apos_antes,
                    juros_mora_final_corrigido,
                    
                    -- Totais e Contadores
                    total_corrigido,
                    meses_juros,
                    meses_antes,
                    meses_pos,
                    criado_em
                ) VALUES (
                    %s, %s, %s, 
                    %s, %s, 
                    %s, 
                    %s, %s, 
                    %s, %s, %s, 
                    %s, %s, %s, %s, NOW()
                )
            """
            
            valores_insert = (
                cpf_raw,
                proc_num,
                float(val_principal),
                
                float(fator_acum_ipca),
                float(fator_acum_selic),
                
                float(principal_na_transicao),
                
                float(saldo_principal), # principal_final_ipca_2aa
                float(saldo_principal), # principal_final (redundante, mas seguro)
                
                float(saldo_juros_base),       # juros base atualizados
                float(saldo_juros_mora_acum),  # juros moratorios do periodo
                float(saldo_juros_base + saldo_juros_mora_acum), # soma juros
                
                float(total_final),
                meses_com_juros,
                meses_fase1,
                meses_fase2
            )
            
            cursor.execute(sql_insert, valores_insert)

            # B) UPDATE na tabela original (Flag de Controle)
            sql_update = "UPDATE esaj_detalhe_processos SET process_calculo = TRUE WHERE id = %s"
            cursor.execute(sql_update, (pid,))
            
            conn.commit()
            print(f"   -> [OK] Calculado e Salvo! Total: R$ {total_final:,.2f}")

        except Exception as e:
            print(f"   -> [ERRO] Falha ID {pid}: {e}")
            conn.rollback()
            # Opcional: Logar erro no banco se tiver campo para isso

    cursor.close()
    conn.close()
    print(">>> FIM DO PROCESSAMENTO <<<")

if __name__ == "__main__":
    main()