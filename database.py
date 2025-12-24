import os
import sys
import psycopg2
from dotenv import load_dotenv, find_dotenv

# Carrega as variáveis do arquivo .env (procura automaticamente na raiz do projeto)
load_dotenv(find_dotenv())

def get_db_connection():
    """
    Estabelece conexão com o banco de dados PostgreSQL usando as variáveis do .env
    """
    # Tenta obter a senha (suporta DB_PASS ou DB_PASSWORD)
    db_pass = os.getenv("DB_PASS") or os.getenv("DB_PASSWORD")

    if not db_pass:
        print("Erro Crítico: Variável de senha (DB_PASS) não encontrada no .env")
        sys.exit(1)

    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=db_pass,
            port=os.getenv("DB_PORT", "5432") # Porta padrão 5432 se não estiver definida
        )
        return conn
    except Exception as e:
        print(f"Erro de conexão ao PostgreSQL: {e}")
        sys.exit(1)