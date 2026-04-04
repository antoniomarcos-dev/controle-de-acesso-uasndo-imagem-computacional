import psycopg2
import os
import sys

URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/prefeitura_ceres")

try:
    conn = psycopg2.connect(URL)
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Truncar todas as tabelas e reiniciar sequences
    tables = ['registros_acesso', 'alertas_justica', 'pessoas', 'veiculos', 'events']
    
    for t in tables:
        try:
            cursor.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE;")
            print(f"Limpa tabela: {t}")
        except Exception as e:
            print(f"Erro ao limpar {t}: {e}")
            
    cursor.close()
    conn.close()
    print("Banco de dados resetado com sucesso (dados de teste removidos).")
    
except Exception as e:
    print(f"Erro ao conectar ao banco de dados: {e}")
