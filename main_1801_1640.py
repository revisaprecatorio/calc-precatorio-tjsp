from __future__ import annotations
import sys
import os
import pandas as pd
import argparse
from psycopg2.extras import RealDictCursor

# --- IMPORTS LOCAIS ---
# Certifique-se que estes arquivos existem na mesma pasta ou no PYTHONPATH
from database import get_db_connection
from financial import carregar_indices_csv, calcular_fim_graca
from webhook_n8n import enviar_relatorio_precatorio

# =======================
# CONFIGURAÇÕES GLOBAIS
# =======================
DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")
JUROS_MORA_MENSAL = 0.005


def registrar_log(cursor, cpf, descricao):
    """Grava logs operacionais no banco de dados"""
    if not cpf:
        return
    cpf_str = str(cpf).strip()
    # Remove caracteres não numéricos para garantir integridade
    cpf_limpo = ''.join(filter(str.isdigit, cpf_str))
    if not cpf_limpo:
        return

    sql = """
        INSERT INTO public.logs (id, cpf, "timestamp", descricao, processo)
        VALUES(nextval('logs_id_seq'::regclass), %s, CURRENT_TIMESTAMP, %s, 'calculo')
    """
    try:
        cursor.execute(sql, (cpf_limpo, descricao))
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def safe_float(v):
    """Converte valores sujos (None, Series, Strings) para float seguro"""
    try:
        if v is None:
            return 0.0
        if isinstance(v, pd.Series):
            v = v.iloc[0]
        return float(v)
    except Exception:
        return 0.0


def safe_index_value(df, idx, col, default=0.0):
    """Busca valor no DataFrame de índices protegendo contra datas inexistentes"""
    try:
        if idx not in df.index:
            return default
        val = df.loc[idx, col]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        return float(val)
    except Exception:
        return default


def main():
    parser = argparse.ArgumentParser(description="Cálculo de Precatórios TJSP")
    parser.add_argument("--cpf", type=str)
    args = parser.parse_args()

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    filtro_display = args.cpf if args.cpf else "GLOBAL"
    print(f">>> Iniciando execução. Filtro: {filtro_display}")
    registrar_log(cursor, args.cpf, f"Início cálculo | Filtro={filtro_display}")
    conn.commit()

    print(">>> Carregando índices financeiros...")
    df_ipca, df_selic = carregar_indices_csv()

    # Query principal: Busca processos que já têm OCR (data_base_atualizacao)
    # mas ainda não foram calculados (process_calculo IS FALSE/NULL)
    sql = """
        SELECT 
            e.id AS id_esaj,
            d.id,
            d.cpf,
            e.email,
            d.numero_processo_cnj,
            d.valor_total_requisitado,
            d.saldo_final,
            d.valor_principal_bruto,
            d.data_base_atualizacao,
            d.juros_moratorios
        FROM esaj_detalhe_processos d
        INNER JOIN consultas_esaj e ON e.cpf = d.cpf
        WHERE d.data_base_atualizacao IS NOT NULL
        AND (d.process_calculo IS FALSE OR d.process_calculo IS NULL)
    """

    params = []
    if args.cpf:
        cpf_limpo = ''.join(filter(str.isdigit, args.cpf))
        sql += " AND d.cpf LIKE %s"
        params.append(f"{cpf_limpo}%")

    cursor.execute(sql, tuple(params))
    processos = cursor.fetchall()

    if not processos:
        print(">>> Nenhum processo pendente encontrado.")
        registrar_log(cursor, args.cpf, "Nenhum processo pendente.")
        conn.commit()
        cursor.close()
        conn.close()
        return

    data_hoje = pd.Timestamp.now().replace(day=1)
    
    # Controle para não processar o mesmo ID duas vezes na mesma execução
    processados_ids = set()
    
    # DICIONÁRIO PARA AGRUPAR POR CPF (BATCHING)
    # Estrutura: { '12345678900': {'email': 'x@x.com', 'ids_esaj_afetados': {10, 11}} }
    cpfs_para_notificar = {}

    print(f">>> Processando {len(processos)} processos pendentes...")

    # =========================================================================
    # 1. ETAPA DE CÁLCULO (Itera por Processo)
    # =========================================================================
    for row in processos:
        pid = row["id"]
        id_esaj = row["id_esaj"]
        cpf_raw = str(row["cpf"])[:11] # Garante CPF limpo
        proc_num = str(row["numero_processo_cnj"])[:30]

        if pid in processados_ids:
            continue
        processados_ids.add(pid)

        try:
            # --- Lógica Financeira ---
            principal = safe_float(row["saldo_final"]) or safe_float(row["valor_total_requisitado"])
            bruto = safe_float(row["valor_principal_bruto"])
            
            # Ratio: Proporção para corrigir os juros base proporcionalmente ao principal líquido
            ratio = min(principal / bruto, 1.0) if bruto > 0 else 1.0
            juros_base = safe_float(row["juros_moratorios"]) * ratio

            dt_req = pd.to_datetime(row["data_base_atualizacao"])
            fim_graca = calcular_fim_graca(dt_req)

            cursor_data = dt_req.replace(day=1) + pd.DateOffset(months=1)
            saldo_principal = principal
            saldo_juros_base = juros_base
            juros_mora = 0.0

            fator_ipca = 1.0
            fator_selic = 1.0
            meses_antes = meses_pos = meses_juros = 0
            principal_transicao = 0.0

            # Loop Temporal (IPCA-E até Dez/21 -> SELIC após)
            while cursor_data <= data_hoje:
                if cursor_data < DATA_CORTE_EC113:
                    # Regra Antiga: IPCA-E + Juros de Mora Simples (0.5%)
                    idx = safe_index_value(df_ipca, cursor_data, "variacao_mensal", 0.0)
                    fator = 1 + idx
                    fator_ipca *= fator
                    saldo_principal *= fator
                    saldo_juros_base *= fator
                    if cursor_data > fim_graca:
                        juros_mora += saldo_principal * JUROS_MORA_MENSAL
                        meses_juros += 1
                    meses_antes += 1
                else:
                    # Regra Nova (EC113): SELIC (Engloba Correção + Juros)
                    if principal_transicao == 0:
                        principal_transicao = saldo_principal
                    taxa = safe_index_value(df_selic, cursor_data, "fator_mensal", 0.008)
                    fator = 1 + taxa
                    fator_selic *= fator
                    saldo_principal *= fator
                    saldo_juros_base *= fator
                    juros_mora *= fator
                    meses_pos += 1

                cursor_data += pd.DateOffset(months=1)

            total = saldo_principal + saldo_juros_base + juros_mora

            # --- [FIX CRÍTICO] LIMPEZA DE VERSÕES ANTERIORES ---
            # Remove cálculo antigo deste processo para evitar duplicidade no relatório final
            cursor.execute(
                "DELETE FROM esaj_calc_precatorio_resumo WHERE numero_processo_cnj = %s", 
                (proc_num,)
            )

            # --- Persistência do Novo Cálculo ---
            cursor.execute("""
                INSERT INTO esaj_calc_precatorio_resumo (
                    cpf, numero_processo_cnj, principal_original,
                    fator_ipcae_antes, fator_ipcae_pos,
                    principal_apos_antes, principal_final_ipca_2aa,
                    principal_final, juros_mora_anteriores_base,
                    juros_mora_apos_antes, juros_mora_final_corrigido,
                    total_corrigido, meses_juros, meses_antes, meses_pos, criado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """, (
                cpf_raw, proc_num, principal,
                fator_ipca, fator_selic,
                principal_transicao or principal,
                saldo_principal, saldo_principal,
                saldo_juros_base, juros_mora,
                saldo_juros_base + juros_mora,
                total, meses_juros, meses_antes, meses_pos
            ))

            # Marca o processo como calculado na tabela de detalhes
            cursor.execute("UPDATE esaj_detalhe_processos SET process_calculo = TRUE WHERE id=%s", (pid,))
            
            # Atualiza status intermediário na tabela de controle (consultas_esaj)
            # Isso informa ao usuário/sistema que o cálculo matemático foi feito
            cursor.execute(
                "UPDATE consultas_esaj SET current_state='CALCULATION_DONE', state_updated_at=NOW() WHERE id=%s", 
                (id_esaj,)
            )

            # --- AGRUPAMENTO (Batching) ---
            # Não envia e-mail agora. Guarda na lista para enviar 1 por CPF no final.
            if cpf_raw not in cpfs_para_notificar:
                cpfs_para_notificar[cpf_raw] = {
                    'email': row.get("email"),
                    'ids_esaj_afetados': set()
                }
            cpfs_para_notificar[cpf_raw]['ids_esaj_afetados'].add(id_esaj)

            registrar_log(cursor, cpf_raw, f"Cálculo concluído: {proc_num} | R$ {total:,.2f}")
            
            # COMMIT IMEDIATO DO CÁLCULO
            # Garante que se o webhook falhar depois, o cálculo já está salvo.
            conn.commit()

        except Exception as e:
            conn.rollback()
            msg = f"Erro ao calcular processo {proc_num}: {e}"
            print(f"[ERRO] {msg}")
            registrar_log(cursor, cpf_raw, msg)
            
            # Tenta marcar erro na tabela de controle (nova transação isolada)
            try:
                cursor.execute("UPDATE consultas_esaj SET current_state='CALC_ERROR' WHERE id=%s", (id_esaj,))
                conn.commit()
            except:
                pass

    # =========================================================================
    # 2. ETAPA DE NOTIFICAÇÃO CONSOLIDADA (Itera por CPF)
    # =========================================================================
    if cpfs_para_notificar:
        print(f">>> Iniciando envio de notificações consolidadas para {len(cpfs_para_notificar)} CPF(s)...")
    
    for cpf_chave, dados in cpfs_para_notificar.items():
        email_dest = dados['email']
        lista_ids = list(dados['ids_esaj_afetados']) # IDs da tabela consultas_esaj
        
        try:
            # Chama o webhook APENAS UMA VEZ por CPF
            print(f">>> Enviando webhook único para CPF {cpf_chave}...")
            enviado = enviar_relatorio_precatorio(cpf_chave, email_dest)
            
            status_final = "REPORT_SENT" if enviado else "REPORT_FAILED"
            msg_log = f"Webhook consolidado: {'SUCESSO' if enviado else 'FALHA'}"
            
            # Atualiza TODOS os jobs (consultas_esaj) desse CPF para o status final de uma vez
            if lista_ids:
                # Formata tupla para SQL IN: (1, 2) ou (1) se for único
                if len(lista_ids) == 1:
                    sql_ids = f"({lista_ids[0]})"
                else:
                    sql_ids = str(tuple(lista_ids))
                
                cursor.execute(f"""
                    UPDATE consultas_esaj 
                    SET current_state=%s, state_updated_at=NOW() 
                    WHERE id IN {sql_ids}
                """, (status_final,))
                
            registrar_log(cursor, cpf_chave, msg_log)
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            print(f"[ERRO WEBHOOK] Falha ao notificar CPF {cpf_chave}: {e}")
            registrar_log(cursor, cpf_chave, f"Erro webhook consolidado: {e}")

    registrar_log(cursor, args.cpf, "Fim execução script cálculo")
    conn.commit()
    cursor.close()
    conn.close()
    print(">>> FIM DO CÁLCULO <<<")


if __name__ == "__main__":
    main()