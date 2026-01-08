from __future__ import annotations
import sys
import os
import pandas as pd
import argparse
from psycopg2.extras import RealDictCursor

# --- IMPORTS LOCAIS (TUDO NA RAIZ) ---
# Certifique-se de que database.py, financial.py e webhook_n8n.py estao na mesma pasta
from database import get_db_connection
from financial import carregar_indices_csv, calcular_fim_graca
from webhook_n8n import enviar_relatorio_precatorio

# Configurações Globais
DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")
JUROS_MORA_MENSAL = 0.005 

def main():
    # --- 1. CONFIGURAÇÃO DE ARGUMENTOS (NOVO) ---
    parser = argparse.ArgumentParser(description="Calculadora de Precatórios TJSP")
    parser.add_argument('--cpf', type=str, help='Filtrar por um CPF específico (apenas números)')
    args = parser.parse_args()

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    print(">>> Carregando índices financeiros...")
    df_ipca, df_selic = carregar_indices_csv()

    # --- 2. BUSCA PROCESSOS PENDENTES (COM JOIN) ---
    # Query base: Busca detalhes de processos que ainda não foram calculados
    sql_busca = """
        SELECT 
            e.id as id_esaj,              -- ID da consulta (pai)
            d.id, 
            d.numero_ordem, 
            d.cpf, 
            e.email,                      -- Email da tabela de consultas
            d.numero_processo_cnj, 
            d.valor_total_requisitado AS valor_precatorio,
            d.saldo_final AS principal, 
            d.data_base_atualizacao AS data_requisitorio, 
            d.juros_moratorios AS juros_mora,
            d.valor_principal_bruto 
        FROM esaj_detalhe_processos d
        INNER JOIN consultas_esaj e ON d.cpf = e.cpf
        WHERE d.data_base_atualizacao IS NOT NULL
        AND (d.process_calculo IS FALSE OR d.process_calculo IS NULL)
    """
    
    params = []
    
    # Se um CPF foi passado pelo orquestrador, filtra a busca
    if args.cpf:
        # Remove caracteres não numéricos por segurança
        cpf_limpo = ''.join(filter(str.isdigit, args.cpf))
        print(f">>> Filtrando pelo CPF: {cpf_limpo}")
        sql_busca += " AND d.cpf LIKE %s"
        params.append(f"{cpf_limpo}%")

    cursor.execute(sql_busca, tuple(params))
    processos = cursor.fetchall()
    
    if not processos:
        print(">>> Nenhum processo pendente encontrado para os critérios informados.")
        cursor.close()
        conn.close()
        return

    print(f">>> Iniciando processamento de {len(processos)} registros...")
    data_hoje = pd.Timestamp.now().replace(day=1)

    for row in processos:
        pid = row['id']
        id_esaj = row['id_esaj']
        
        cpf_raw = str(row['cpf'])[:11] if row['cpf'] else '00000000000'
        proc_num = str(row['numero_processo_cnj'])[:30]

        try:
            # Função auxiliar segura
            def safe_float(v): return float(v) if v is not None else 0.0

            val_principal = safe_float(row['principal'])
            # Se o saldo final vier zerado, tenta usar o valor total requisitado
            if val_principal == 0: val_principal = safe_float(row['valor_precatorio'])
            
            # Lógica de Proporcionalidade
            val_bruto_orig = safe_float(row['valor_principal_bruto'])
            ratio = 1.0
            if val_bruto_orig > 0:
                ratio = val_principal / val_bruto_orig
                if ratio > 1.0: ratio = 1.0
            
            val_juros_base = safe_float(row['juros_mora']) * ratio
            
            dt_req = pd.to_datetime(row['data_requisitorio'])
            fim_graca = calcular_fim_graca(dt_req)

            print(f"ID {pid} | {proc_num} | Principal: {val_principal:.2f} | Ratio: {ratio:.4f}")

            # --- Loop Temporal de Cálculo ---
            cursor_data = dt_req.replace(day=1) + pd.DateOffset(months=1)
            saldo_principal = val_principal
            saldo_juros_base = val_juros_base
            saldo_juros_mora_acum = 0.0
            fator_acum_ipca = 1.0
            fator_acum_selic = 1.0
            meses_fase1 = 0
            meses_fase2 = 0
            meses_com_juros = 0
            principal_na_transicao = 0.0
            
            while cursor_data <= data_hoje:
                # FASE 1: Antes da EC113 (Usa IPCA-E + Juros de 0.5% a.m. se fora da graça)
                if cursor_data < DATA_CORTE_EC113:
                    try: idx = float(df_ipca.loc[cursor_data]['variacao_mensal'])
                    except: idx = 0.0
                    fator = 1 + idx
                    fator_acum_ipca *= fator
                    saldo_principal *= fator
                    saldo_juros_base *= fator
                    
                    if cursor_data > fim_graca:
                        saldo_juros_mora_acum += (saldo_principal * JUROS_MORA_MENSAL)
                        meses_com_juros += 1
                    meses_fase1 += 1
                
                # FASE 2: Pós EC113 (Usa SELIC para tudo)
                else:
                    if principal_na_transicao == 0.0: principal_na_transicao = saldo_principal
                    try: taxa_selic = float(df_selic.loc[cursor_data]['fator_mensal'])
                    except: taxa_selic = 0.008 
                    
                    fator_selic = 1 + taxa_selic
                    fator_acum_selic *= fator_selic
                    saldo_principal *= fator_selic
                    saldo_juros_base *= fator_selic
                    saldo_juros_mora_acum *= fator_selic
                    meses_fase2 += 1
                
                cursor_data = (cursor_data + pd.DateOffset(months=1)).replace(day=1)

            if principal_na_transicao == 0.0: principal_na_transicao = val_principal
            total_final = saldo_principal + saldo_juros_base + saldo_juros_mora_acum

            # --- 3. PERSISTÊNCIA (INSERT) ---
            sql_insert = """
                INSERT INTO esaj_calc_precatorio_resumo (
                    cpf, numero_processo_cnj, principal_original, fator_ipcae_antes, fator_ipcae_pos,
                    principal_apos_antes, principal_final_ipca_2aa, principal_final,
                    juros_mora_anteriores_base, juros_mora_apos_antes, juros_mora_final_corrigido,
                    total_corrigido, meses_juros, meses_antes, meses_pos, criado_em
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """
            vals = (
                cpf_raw, proc_num, float(val_principal), float(fator_acum_ipca), float(fator_acum_selic),
                float(principal_na_transicao), float(saldo_principal), float(saldo_principal), 
                float(saldo_juros_base), float(saldo_juros_mora_acum), 
                float(saldo_juros_base + saldo_juros_mora_acum), float(total_final),
                meses_com_juros, meses_fase1, meses_fase2
            )
            
            cursor.execute(sql_insert, vals)
            
            # 4. ATUALIZAÇÕES DE STATUS
            # Marca o processo unitário como calculado para não repetir
            cursor.execute("UPDATE esaj_detalhe_processos SET process_calculo = TRUE WHERE id = %s", (pid,))
            
            # Atualiza o status global da consulta (Opcional, pois o orchestrator também fará no final,
            # mas bom manter para tracking em tempo real)
            if id_esaj:
                cursor.execute(
                    "UPDATE consultas_esaj SET current_state = 'CALCULATION_CONFIRMED', state_updated_at=NOW() WHERE id = %s", 
                    (id_esaj,)
                )
            
            conn.commit()
            print(f"   -> [OK] Calculado e Confirmado: R$ {total_final:,.2f}")

            # --- 5. WEBHOOK (Notificação) ---
            email_dest = row.get('email')
            if enviar_relatorio_precatorio(cpf_raw, email_dest):
                print("   -> [WEBHOOK] Enviado com sucesso.")
            else:
                print("   -> [WEBHOOK] Falha ao enviar.")

        except Exception as e:
            print(f"   -> [ERRO] Falha no processo ID {pid}: {e}")
            conn.rollback()

    cursor.close()
    conn.close()
    print(">>> FIM DO CÁLCULO <<<")

if __name__ == "__main__":
    main()