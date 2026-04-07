from __future__ import annotations
import pandas as pd
import argparse
from psycopg2.extras import RealDictCursor

# --- IMPORTS LOCAIS ---
from database import get_db_connection
from financial import carregar_indices_csv, calcular_fim_graca
from webhook_n8n import enviar_relatorio_precatorio

# =======================
# CONFIGURAÇÕES GLOBAIS
# =======================
DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")

# Marco clássico: até 29/06/2009 era 6% a.a. simples (Fazenda Pública)
DATA_CORTE_LEI_11960 = pd.Timestamp("2009-06-29")

# Default pós-2009 (poupança aproximada/compatível):
# Se você não tem TR/poupança real, isso é o mais “próximo” do padrão (0,5% a.m.)
JUROS_MORA_MENSAL_POS_2009 = 0.005

# 6% a.a. simples (equivalente mensal)
JUROS_6AA_MENSAL = 0.06 / 12.0


def registrar_log(cursor, cpf, descricao):
    """Grava logs operacionais no banco de dados"""
    if not cpf:
        return
    cpf_str = str(cpf).strip()
    cpf_limpo = "".join(filter(str.isdigit, cpf_str))
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


def escolher_principal_base(row) -> float:
    """
    Regra prática para evitar duplicidade:
    - Use valor_principal_bruto como "principal base" (melhor para juros simples do TJSP).
    - Se não existir, cai para saldo_final, senão valor_total_requisitado.
    """
    bruto = safe_float(row.get("valor_principal_bruto"))
    if bruto > 0:
        return bruto

    saldo_final = safe_float(row.get("saldo_final"))
    if saldo_final > 0:
        return saldo_final

    return safe_float(row.get("valor_total_requisitado"))


def main():
    parser = argparse.ArgumentParser(description="Cálculo de Precatórios TJSP")
    parser.add_argument("--cpf", type=str)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--stop_at", type=str, default=None, help="Para debug: YYYY-MM (ex: 2021-12)")
    args = parser.parse_args()

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    filtro_display = args.cpf if args.cpf else "GLOBAL"
    print(f">>> Iniciando execução. Filtro: {filtro_display}")
    registrar_log(cursor, args.cpf, f"Início cálculo | Filtro={filtro_display}")
    conn.commit()

    print(">>> Carregando índices financeiros...")
    df_ipca, df_selic = carregar_indices_csv()
     
    print("SELIC min/max:", df_selic["fator_mensal"].min(), df_selic["fator_mensal"].max())
    print(df_selic.loc["2021-12-01":"2022-03-01", ["fator_mensal"]])
 
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
        cpf_limpo = "".join(filter(str.isdigit, args.cpf))
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

    # Data final do cálculo (sempre no 1º dia do mês atual)
    data_hoje = pd.Timestamp.now().replace(day=1)

    # Debug stop_at
    stop_at_ts = None
    if args.stop_at:
        try:
            stop_at_ts = pd.Timestamp(f"{args.stop_at}-01")
        except Exception:
            stop_at_ts = None

    processados_ids = set()

    # Batching por CPF
    cpfs_para_notificar = {}

    print(f">>> Processando {len(processos)} processos pendentes...")

    for row in processos:
        pid = row["id"]
        id_esaj = row["id_esaj"]
        cpf_raw = str(row["cpf"])[:11]
        proc_num = str(row["numero_processo_cnj"])[:30]

        if pid in processados_ids:
            continue
        processados_ids.add(pid)

        try:
            # =========================================================
            # BASE
            # =========================================================
            principal_base = escolher_principal_base(row)

            # JM_ant: juros moratórios já existentes na base do documento (até data_base_atualizacao)
            jm_ant_base = safe_float(row.get("juros_moratorios"))

            dt_base = pd.to_datetime(row["data_base_atualizacao"])
            fim_graca = calcular_fim_graca(dt_base)

            # começa no 1º dia do mês seguinte à data base
            cursor_data = dt_base.replace(day=1) + pd.DateOffset(months=1)

            # =========================================================
            # BALDES (sem juros sobre juros)
            # =========================================================
            saldo_principal = principal_base
            saldo_jm_ant = jm_ant_base      # será corrigido por IPCA (pré-EC113) e por SELIC (pós-EC113)
            jm_novo = 0.0                   # juros de mora simples gerado durante o período

            fator_ipca = 1.0
            fator_selic = 1.0
            meses_antes = 0
            meses_pos = 0
            meses_juros = 0
            principal_transicao = 0.0

            if args.debug:
                print(f"\n[DEBUG] Processo {proc_num} CPF {cpf_raw}")
                print(f"[DEBUG] dt_base={dt_base.date()} fim_graca={fim_graca.date() if fim_graca != pd.Timestamp.max else 'MAX'}")
                print(f"[DEBUG] principal_base={principal_base:,.2f} jm_ant_base={jm_ant_base:,.2f}")

            # =========================================================
            # LOOP MÊS A MÊS
            # =========================================================
            while cursor_data <= data_hoje:
                if stop_at_ts and cursor_data > stop_at_ts:
                    break

                if cursor_data < DATA_CORTE_EC113:
                    # -----------------------------
                    # PRÉ-EC113: IPCA + JM simples
                    # -----------------------------
                    idx_ipca = safe_index_value(df_ipca, cursor_data, "variacao_mensal", 0.0)
                    fator_mes = 1 + idx_ipca

                    # correção monetária no principal e no JM anterior
                    fator_ipca *= fator_mes
                    saldo_principal *= fator_mes
                    saldo_jm_ant *= fator_mes

                    # juros de mora simples APENAS sobre principal (incidência no principal)
                    # e somente após fim da graça
                    if cursor_data > fim_graca:
                        if cursor_data <= DATA_CORTE_LEI_11960:
                            taxa_jm = JUROS_6AA_MENSAL
                        else:
                            taxa_jm = JUROS_MORA_MENSAL_POS_2009

                        jm_novo += saldo_principal * taxa_jm
                        meses_juros += 1

                    meses_antes += 1

                else:
                    # -----------------------------
                    # PÓS-EC113: SELIC (correção+juros)
                    # -----------------------------
                    if principal_transicao == 0.0:
                        principal_transicao = saldo_principal

                    taxa_selic = safe_index_value(df_selic, cursor_data, "fator_mensal", 0.008)
                    fator_mes = 1 + taxa_selic
                    fator_selic *= fator_mes

                    saldo_principal *= fator_mes
                    saldo_jm_ant *= fator_mes
                    jm_novo *= fator_mes

                    meses_pos += 1

                if args.debug and cursor_data in (
                    pd.Timestamp("2021-12-01"),
                    pd.Timestamp("2022-01-01"),
                    pd.Timestamp("2025-12-01"),
                ):
                    total_dbg = saldo_principal + saldo_jm_ant + jm_novo
                    print(
                        f"[DEBUG] {cursor_data.strftime('%Y-%m')} "
                        f"principal={saldo_principal:,.2f} jm_ant={saldo_jm_ant:,.2f} jm_novo={jm_novo:,.2f} total={total_dbg:,.2f}"
                    )

                cursor_data += pd.DateOffset(months=1)

            total = saldo_principal + saldo_jm_ant + jm_novo

            # =========================================================
            # PERSISTÊNCIA
            # =========================================================
            cursor.execute(
                "DELETE FROM esaj_calc_precatorio_resumo WHERE numero_processo_cnj = %s",
                (proc_num,),
            )

            cursor.execute(
                """
                INSERT INTO esaj_calc_precatorio_resumo (
                    cpf, numero_processo_cnj, principal_original,
                    fator_ipcae_antes, fator_ipcae_pos,
                    principal_apos_antes, principal_final_ipca_2aa,
                    principal_final, juros_mora_anteriores_base,
                    juros_mora_apos_antes, juros_mora_final_corrigido,
                    total_corrigido, meses_juros, meses_antes, meses_pos, criado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (
                    cpf_raw,
                    proc_num,
                    principal_base,
                    fator_ipca,
                    fator_selic,
                    principal_transicao or principal_base,
                    saldo_principal,  # mantendo compatibilidade com seu schema (coluna existe)
                    saldo_principal,
                    jm_ant_base,      # base "do documento"
                    jm_novo,          # juros gerado pelo motor (simples)
                    (saldo_jm_ant + jm_novo),  # juros total corrigido
                    total,
                    meses_juros,
                    meses_antes,
                    meses_pos,
                ),
            )

            cursor.execute(
                "UPDATE esaj_detalhe_processos SET process_calculo = TRUE WHERE id=%s",
                (pid,),
            )

            cursor.execute(
                "UPDATE consultas_esaj SET current_state='CALCULATION_DONE', state_updated_at=NOW() WHERE id=%s",
                (id_esaj,),
            )

            # batching por CPF
            if cpf_raw not in cpfs_para_notificar:
                cpfs_para_notificar[cpf_raw] = {"email": row.get("email"), "ids_esaj_afetados": set()}
            cpfs_para_notificar[cpf_raw]["ids_esaj_afetados"].add(id_esaj)

            registrar_log(cursor, cpf_raw, f"Cálculo concluído: {proc_num} | R$ {total:,.2f}")
            conn.commit()

        except Exception as e:
            conn.rollback()
            msg = f"Erro ao calcular processo {proc_num}: {e}"
            print(f"[ERRO] {msg}")
            registrar_log(cursor, cpf_raw, msg)

            try:
                cursor.execute("UPDATE consultas_esaj SET current_state='CALC_ERROR' WHERE id=%s", (id_esaj,))
                conn.commit()
            except Exception:
                pass

    # =========================================================
    # NOTIFICAÇÃO CONSOLIDADA
    # =========================================================
    if cpfs_para_notificar:
        print(f">>> Iniciando envio de notificações consolidadas para {len(cpfs_para_notificar)} CPF(s)...")

    for cpf_chave, dados in cpfs_para_notificar.items():
        email_dest = dados["email"]
        lista_ids = list(dados["ids_esaj_afetados"])

        try:
            print(f">>> Enviando webhook único para CPF {cpf_chave}...")
            enviado = enviar_relatorio_precatorio(cpf_chave, email_dest)

            status_final = "REPORT_SENT" if enviado else "REPORT_FAILED"
            msg_log = f"Webhook consolidado: {'SUCESSO' if enviado else 'FALHA'}"

            if lista_ids:
                if len(lista_ids) == 1:
                    sql_ids = f"({lista_ids[0]})"
                else:
                    sql_ids = str(tuple(lista_ids))

                cursor.execute(
                    f"""
                    UPDATE consultas_esaj 
                    SET current_state=%s, state_updated_at=NOW() 
                    WHERE id IN {sql_ids}
                    """,
                    (status_final,),
                )

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
