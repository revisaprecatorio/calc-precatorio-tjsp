from __future__ import annotations
import sys
import os
import pandas as pd
import argparse
from psycopg2.extras import RealDictCursor

from database import get_db_connection
from financial import carregar_indices_csv, calcular_fim_graca
from webhook_n8n import enviar_relatorio_precatorio

DATA_CORTE_EC113 = pd.Timestamp("2021-12-09")
JUROS_MORA_MENSAL = 0.005

# LIGA / DESLIGA DO REDUTOR FINAL
APLICAR_REDUTOR_FINAL = False
REDUTOR_FINAL = 0.90


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
        if isinstance(v, str):
            v = v.strip().replace(".", "").replace(",", ".")
        return float(v)
    except Exception:
        return 0.0


def safe_index_value(df, idx, col, default=None):
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


def format_money(v):
    try:
        return f"R$ {float(v):,.2f}"
    except Exception:
        return f"R$ {v}"


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

    data_hoje_sistema = pd.Timestamp.now().replace(day=1)
    ultima_data_ipca = df_ipca.index.max()
    ultima_data_selic = df_selic.index.max()

    data_hoje = min(data_hoje_sistema, ultima_data_ipca, ultima_data_selic)

    print(f">>> Data limite do cálculo: {data_hoje.strftime('%Y-%m')}")
    print(f">>> Aplicar redutor final: {'SIM' if APLICAR_REDUTOR_FINAL else 'NÃO'}")

    processados_ids = set()
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
            saldo_final = safe_float(row["saldo_final"])
            valor_total_requisitado = safe_float(row["valor_total_requisitado"])
            valor_principal_bruto = safe_float(row["valor_principal_bruto"])
            juros_moratorios = safe_float(row["juros_moratorios"])

            if saldo_final > 0:
                principal = saldo_final
                fonte_principal = "saldo_final"
            else:
                principal = valor_total_requisitado
                fonte_principal = "valor_total_requisitado"

            if valor_principal_bruto > 0 and juros_moratorios > 0:
                ratio = min(principal / valor_principal_bruto, 1.0)
                juros_base = juros_moratorios * ratio
            else:
                juros_base = juros_moratorios if juros_moratorios > 0 else 0.0

            dt_req = pd.to_datetime(row["data_base_atualizacao"])

            if dt_req > data_hoje:
                raise ValueError(
                    f"data_base_atualizacao futura para o processo: {dt_req.strftime('%Y-%m-%d')}"
                )

            fim_graca = calcular_fim_graca(dt_req)
            cursor_data = dt_req.replace(day=1) + pd.DateOffset(months=1)

            saldo_principal = principal
            saldo_juros_base = juros_base
            juros_mora = 0.0

            fator_ipca = 1.0
            fator_selic = 1.0
            meses_antes = 0
            meses_pos = 0
            meses_juros = 0

            principal_transicao = None
            juros_base_transicao = None
            juros_mora_transicao = None

            principal_apos_antes = principal
            juros_base_apos_antes = juros_base
            juros_mora_apos_antes = 0.0

            while cursor_data <= data_hoje:
                if cursor_data < DATA_CORTE_EC113:
                    idx = safe_index_value(df_ipca, cursor_data, "variacao_mensal", None)
                    if idx is None:
                        raise ValueError(f"IPCA ausente para {cursor_data.strftime('%Y-%m')}")

                    fator = 1 + idx
                    fator_ipca *= fator

                    saldo_principal *= fator
                    saldo_juros_base *= fator

                    if cursor_data > fim_graca:
                        juros_mora += saldo_principal * JUROS_MORA_MENSAL
                        meses_juros += 1

                    meses_antes += 1

                else:
                    if principal_transicao is None:
                        principal_transicao = saldo_principal
                        juros_base_transicao = saldo_juros_base
                        juros_mora_transicao = juros_mora

                        principal_apos_antes = saldo_principal
                        juros_base_apos_antes = saldo_juros_base
                        juros_mora_apos_antes = juros_mora

                    taxa = safe_index_value(df_selic, cursor_data, "fator_mensal", None)
                    if taxa is None:
                        raise ValueError(f"SELIC ausente para {cursor_data.strftime('%Y-%m')}")

                    fator = 1 + taxa
                    fator_selic *= fator

                    saldo_principal *= fator
                    saldo_juros_base *= fator
                    juros_mora *= fator

                    meses_pos += 1

                cursor_data += pd.DateOffset(months=1)

            total_bruto = saldo_principal + saldo_juros_base + juros_mora

            if APLICAR_REDUTOR_FINAL:
                total_corrigido = total_bruto * REDUTOR_FINAL
            else:
                total_corrigido = total_bruto

            cursor.execute(
                "DELETE FROM esaj_calc_precatorio_resumo WHERE numero_processo_cnj = %s",
                (proc_num,)
            )

            cursor.execute("""
                INSERT INTO esaj_calc_precatorio_resumo (
                    cpf,
                    numero_processo_cnj,
                    principal_original,
                    fator_ipcae_antes,
                    fator_ipcae_pos,
                    principal_apos_antes,
                    principal_final_ipca_2aa,
                    principal_final,
                    juros_mora_anteriores_base,
                    juros_mora_apos_antes,
                    juros_mora_final_corrigido,
                    total_corrigido,
                    meses_juros,
                    meses_antes,
                    meses_pos,
                    criado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """, (
                cpf_raw,
                proc_num,
                principal,
                fator_ipca,
                fator_selic,
                principal_apos_antes,
                saldo_principal,
                saldo_principal,
                juros_base_apos_antes,
                juros_mora_apos_antes,
                (saldo_juros_base + juros_mora),
                total_corrigido,
                meses_juros,
                meses_antes,
                meses_pos
            ))

            cursor.execute(
                "UPDATE esaj_detalhe_processos SET process_calculo = TRUE WHERE id=%s",
                (pid,)
            )

            cursor.execute(
                "UPDATE consultas_esaj SET current_state='CALCULATION_DONE', state_updated_at=NOW() WHERE id=%s",
                (id_esaj,)
            )

            if cpf_raw not in cpfs_para_notificar:
                cpfs_para_notificar[cpf_raw] = {
                    "email": row.get("email"),
                    "ids_esaj_afetados": set()
                }
            cpfs_para_notificar[cpf_raw]["ids_esaj_afetados"].add(id_esaj)

            registrar_log(
                cursor,
                cpf_raw,
                (
                    f"Cálculo concluído: {proc_num} | "
                    f"fonte_principal={fonte_principal} | "
                    f"principal={format_money(principal)} | "
                    f"juros_base={format_money(juros_base)} | "
                    f"principal_apos_antes={format_money(principal_apos_antes)} | "
                    f"juros_base_apos_antes={format_money(juros_base_apos_antes)} | "
                    f"juros_mora_apos_antes={format_money(juros_mora_apos_antes)} | "
                    f"total_bruto={format_money(total_bruto)} | "
                    f"redutor_aplicado={'SIM' if APLICAR_REDUTOR_FINAL else 'NÃO'} | "
                    f"total_corrigido={format_money(total_corrigido)}"
                )
            )

            conn.commit()

        except Exception as e:
            conn.rollback()
            msg = f"Erro ao calcular processo {proc_num}: {e}"
            print(f"[ERRO] {msg}")
            registrar_log(cursor, cpf_raw, msg)

            try:
                cursor.execute(
                    "UPDATE consultas_esaj SET current_state='CALC_ERROR' WHERE id=%s",
                    (id_esaj,)
                )
                conn.commit()
            except Exception:
                pass

    if cpfs_para_notificar:
        print(f">>> Iniciando envio de notificações consolidadas para {len(cpfs_para_notificar)} CPF(s)...")

    for cpf_chave, dados in cpfs_para_notificar.items():
        email_dest = dados["email"]
        lista_ids = list(dados["ids_esaj_afetados"])
        sql_ids = None

        try:
            print(f">>> Enviando webhook único para CPF {cpf_chave}...")
            registrar_log(cursor, cpf_chave, "CPF enviado para o Webhook")

            enviado, detalhe_servidor = enviar_relatorio_precatorio(cpf_chave, email_dest)

            status_final = "REPORT_SENT" if enviado else "REPORT_FAILED"
            msg_log = f"Webhook consolidado: {'SUCESSO' if enviado else 'FALHA'} | Detalhe: {detalhe_servidor}"

            if lista_ids:
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
            if conn:
                conn.rollback()
            print(f"[ERRO WEBHOOK] Falha ao notificar CPF {cpf_chave}: {e}")
            registrar_log(cursor, cpf_chave, f"Erro crítico no fluxo: {str(e)}")
            conn.commit()

    registrar_log(cursor, args.cpf, "Fim execução script cálculo")
    conn.commit()
    cursor.close()
    conn.close()
    print(">>> FIM DO CÁLCULO <<<")


if __name__ == "__main__":
    main()