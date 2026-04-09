"""
Script para resetar todos os dados do banco de dados.
Usa as mesmas variáveis de ambiente do sistema principal (.env).
"""

import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "prefeitura_ceres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

try:
    conn = psycopg.connect(
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}",
        autocommit=True
    )
    cur = conn.cursor()

    # Truncar todas as tabelas e reiniciar sequences
    tables = ['registros_acesso', 'alertas_justica', 'veiculos', 'pessoas', 'events']

    for t in tables:
        try:
            cur.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE;")
            print(f"Limpa tabela: {t}")
        except Exception as e:
            print(f"Erro ao limpar {t}: {e}")

    cur.close()
    conn.close()
    print("Banco de dados resetado com sucesso (dados de teste removidos).")

except Exception as e:
    print(f"Erro ao conectar ao banco de dados: {e}")
