import os
import csv
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "prefeitura_ceres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
CSV_FILE = os.path.join(DATA_DIR, 'stats.csv')

def setup_database():
    try:
        print("[SETUP] Conectando ao cluster PostgreSQL primário para criar banco de dados...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname="postgres"  # Conecta no padrão primeiro
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        # Cria banco de dados (se não existir, precisa capturar erro, psycopg2 não suporta CREATE DATABASE IF NOT EXISTS)
        cur.execute(f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{DB_NAME}'")
        exists = cur.fetchone()
        if not exists:
            print(f"[SETUP] Criando banco de dados '{DB_NAME}'...")
            cur.execute(f"CREATE DATABASE {DB_NAME}")
        else:
            print(f"[SETUP] Banco de dados '{DB_NAME}' já existe.")
            
        cur.close()
        conn.close()
        
        # Conecta no banco recém criado
        print(f"[SETUP] Conectando no banco '{DB_NAME}'...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME
        )
        cur = conn.cursor()
        
        # Cria tabela
        print("[SETUP] Garantindo estrutura da tabela 'events'...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                event_type VARCHAR(20) NOT NULL,
                person_id VARCHAR(50) NOT NULL,
                gender VARCHAR(20),
                age VARCHAR(30)
            );
        """)
        
        # Criar índice para acesso rápido
        cur.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type);")
        
        conn.commit()
        
        # Migração do CSV
        migrar = input("Deseja migrar os dados antigos do CSV ('stats.csv') para o banco? (s/N): ")
        if migrar.lower() in ('s', 'sim', 'y', 'yes'):
            if os.path.exists(CSV_FILE):
                print("[SETUP] Realizando upload das contagens antigas...")
                with open(CSV_FILE, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    migrated = 0
                    for row in reader:
                        cur.execute("""
                            INSERT INTO events (timestamp, event_type, person_id, gender, age) 
                            VALUES (%(timestamp)s, %(event_type)s, %(person_id)s, %(gender)s, %(age)s)
                        """, row)
                        migrated += 1
                conn.commit()
                print(f"[SETUP] Migração concluída: {migrated} eventos integrados com sucesso no banco de dados!")
            else:
                print("[SETUP] Arquivo CSV não encontrado. Pulando migração.")
        
        cur.close()
        conn.close()
        print("[SETUP] Setup Finalizado com Sucesso!")
        
    except Exception as e:
        print(f"[ERRO FATAL] Ocorreu um erro conectando ou configurando o Postgres: {e}")

if __name__ == "__main__":
    setup_database()
