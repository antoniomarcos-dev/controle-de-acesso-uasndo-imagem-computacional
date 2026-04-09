"""
Script de Setup do PostgreSQL — Schema Completo + Dados de Teste.

Uso:
    python scripts/setup_postgres.py
    python scripts/setup_postgres.py --seed    (inclui dados fictícios de teste)
"""

import os
import sys
import psycopg
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "prefeitura_ceres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")


def _conninfo(dbname=None):
    """Monta connection string."""
    db = dbname or DB_NAME
    return f"host={DB_HOST} port={DB_PORT} dbname={db} user={DB_USER} password={DB_PASSWORD}"


def setup_database():
    """Cria o banco de dados e todas as tabelas necessárias."""
    try:
        # ── Criar banco de dados se não existir ──
        print("[SETUP] Conectando ao cluster PostgreSQL...")
        conn = psycopg.connect(_conninfo("postgres"), autocommit=True)
        cur = conn.cursor()

        cur.execute(f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{DB_NAME}'")
        exists = cur.fetchone()
        if not exists:
            print(f"[SETUP] Criando banco de dados '{DB_NAME}'...")
            cur.execute(f"CREATE DATABASE {DB_NAME}")
        else:
            print(f"[SETUP] Banco de dados '{DB_NAME}' já existe.")

        cur.close()
        conn.close()

        # ── Conectar ao banco e criar tabelas ──
        print(f"[SETUP] Conectando no banco '{DB_NAME}'...")
        conn = psycopg.connect(_conninfo())
        cur = conn.cursor()

        print("[SETUP] Criando schema completo...")

        # Tabela events (legado)
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

        # Tabela pessoas
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pessoas (
                id SERIAL PRIMARY KEY,
                nome VARCHAR(255) NOT NULL,
                cpf VARCHAR(14) UNIQUE,
                foto_hash VARCHAR(128),
                embedding FLOAT8[],
                idade_estimada INTEGER,
                genero VARCHAR(20),
                status_judicial VARCHAR(50) DEFAULT 'limpo',
                tem_mandado BOOLEAN DEFAULT FALSE,
                foto_referencia_path TEXT,
                ultima_aparicao TIMESTAMP,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabela veículos
        cur.execute("""
            CREATE TABLE IF NOT EXISTS veiculos (
                id SERIAL PRIMARY KEY,
                placa VARCHAR(10) UNIQUE NOT NULL,
                modelo VARCHAR(100),
                cor VARCHAR(50),
                proprietario_id INTEGER REFERENCES pessoas(id) ON DELETE SET NULL,
                pendencias_detran TEXT,
                ipva_atrasado BOOLEAN DEFAULT FALSE,
                status_roubo BOOLEAN DEFAULT FALSE,
                licenciamento_atrasado BOOLEAN DEFAULT FALSE,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabela registros de acesso
        cur.execute("""
            CREATE TABLE IF NOT EXISTS registros_acesso (
                id SERIAL PRIMARY KEY,
                tipo VARCHAR(20) NOT NULL,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                localizacao VARCHAR(255),
                modo_operacao VARCHAR(20),
                pessoa_id INTEGER REFERENCES pessoas(id) ON DELETE SET NULL,
                veiculo_id INTEGER REFERENCES veiculos(id) ON DELETE SET NULL,
                nivel_alerta VARCHAR(20) DEFAULT 'verde',
                detalhes_alerta TEXT,
                confianca_facial FLOAT,
                placa_detectada VARCHAR(10),
                acao_tomada VARCHAR(50)
            );
        """)

        # Tabela alertas judiciais
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alertas_justica (
                id SERIAL PRIMARY KEY,
                pessoa_id INTEGER REFERENCES pessoas(id) ON DELETE SET NULL,
                veiculo_id INTEGER REFERENCES veiculos(id) ON DELETE SET NULL,
                tipo_alerta VARCHAR(50) NOT NULL,
                nivel VARCHAR(20) NOT NULL,
                descricao TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolvido BOOLEAN DEFAULT FALSE,
                registrado_por VARCHAR(100) DEFAULT 'sistema_automatico'
            );
        """)

        # ── Índices de Performance ──
        print("[SETUP] Criando índices de performance...")
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);",
            "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
            "CREATE INDEX IF NOT EXISTS idx_pessoas_cpf ON pessoas(cpf);",
            "CREATE INDEX IF NOT EXISTS idx_veiculos_placa ON veiculos(placa);",
            "CREATE INDEX IF NOT EXISTS idx_registros_timestamp ON registros_acesso(timestamp);",
            "CREATE INDEX IF NOT EXISTS idx_registros_nivel ON registros_acesso(nivel_alerta);",
            "CREATE INDEX IF NOT EXISTS idx_registros_pessoa ON registros_acesso(pessoa_id);",
            "CREATE INDEX IF NOT EXISTS idx_registros_veiculo ON registros_acesso(veiculo_id);",
            "CREATE INDEX IF NOT EXISTS idx_alertas_nivel ON alertas_justica(nivel);",
            "CREATE INDEX IF NOT EXISTS idx_alertas_resolvido ON alertas_justica(resolvido);",
            "CREATE INDEX IF NOT EXISTS idx_alertas_timestamp ON alertas_justica(timestamp);",
        ]
        for idx in indices:
            cur.execute(idx)

        conn.commit()
        print("[SETUP] ✓ Schema criado com sucesso!")

        # ── Seed de dados de teste ──
        if "--seed" in sys.argv:
            seed_test_data(cur, conn)

        cur.close()
        conn.close()
        print("\n[SETUP] ✓ Setup finalizado com sucesso!")

    except Exception as e:
        print(f"\n[ERRO FATAL] Ocorreu um erro: {e}")
        raise


def seed_test_data(cur, conn):
    """Popula o banco com dados fictícios para testes."""
    print("\n[SEED] Inserindo dados de teste...")

    # Verificar se já tem dados
    cur.execute("SELECT COUNT(*) FROM pessoas")
    count = cur.fetchone()[0]
    if count > 0:
        print("[SEED] Dados já existem no banco. Pulando seed.")
        return

    # ═══ Pessoas ═══
    pessoas = [
        # (nome, cpf, genero, idade, status_judicial, tem_mandado)
        ("João Silva Pereira", "123.456.789-00", "Male", 35, "limpo", False),
        ("Maria Oliveira Santos", "234.567.890-11", "Female", 28, "limpo", False),
        ("Carlos Eduardo Lima", "345.678.901-22", "Male", 42, "limpo", False),
        ("Ana Beatriz Costa", "456.789.012-33", "Female", 31, "limpo", False),
        ("Pedro Henrique Souza", "567.890.123-44", "Male", 55, "limpo", False),
        ("Fernanda Rodrigues", "678.901.234-55", "Female", 23, "limpo", False),
        # Pessoas com pendências (para teste de alerta VERMELHO)
        ("Roberto Almeida Ferreira", "789.012.345-66", "Male", 38, "procurado", True),
        ("Lucas Barbosa Araujo", "890.123.456-77", "Male", 29, "foragido", True),
        ("Marcos Vinícius Dias", "901.234.567-88", "Male", 45, "mandado_ativo", True),
        # Pessoa com pendência judicial menor (AMARELO)
        ("Juliana Martins Rocha", "012.345.678-99", "Female", 33, "audiencia_pendente", False),
    ]

    for nome, cpf, genero, idade, status, mandado in pessoas:
        cur.execute("""
            INSERT INTO pessoas (nome, cpf, genero, idade_estimada, status_judicial, tem_mandado)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (cpf) DO NOTHING
        """, (nome, cpf, genero, idade, status, mandado))

    print(f"[SEED] ✓ {len(pessoas)} pessoas inseridas")

    # ═══ Veículos ═══
    veiculos = [
        # (placa, modelo, cor, proprietario_idx, ipva_atrasado, status_roubo, licenciamento_atrasado)
        ("ABC1D23", "Fiat Argo", "Branco", 1, False, False, False),
        ("DEF2G45", "VW Gol", "Prata", 2, False, False, False),
        ("GHI3J67", "Chevrolet Onix", "Preto", 3, False, False, False),
        ("JKL4M89", "Toyota Corolla", "Cinza", 4, False, False, False),
        ("MNO5P01", "Honda Civic", "Azul", 5, False, False, False),
        # Veículo roubado (VERMELHO)
        ("QRS6T23", "Hyundai HB20", "Vermelho", None, False, True, False),
        ("UVW7X45", "Fiat Toro", "Preto", None, False, True, False),
        # Veículos com IPVA atrasado (AMARELO)
        ("YZA8B67", "Renault Sandero", "Branco", 6, True, False, False),
        ("BCD9E89", "Ford Ka", "Prata", None, True, False, True),
        # Veículo com licenciamento atrasado (AMARELO)
        ("EFG0H01", "Chevrolet Cruze", "Azul", None, False, False, True),
    ]

    for placa, modelo, cor, prop_idx, ipva, roubo, lic in veiculos:
        cur.execute("""
            INSERT INTO veiculos (placa, modelo, cor, proprietario_id, ipva_atrasado, status_roubo, licenciamento_atrasado)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (placa) DO NOTHING
        """, (placa, modelo, cor, prop_idx, ipva, roubo, lic))

    print(f"[SEED] ✓ {len(veiculos)} veículos inseridos")

    conn.commit()

    # ── Resumo ──
    print("\n[SEED] ═══ Resumo dos Dados de Teste ═══")
    print("[SEED] Pessoas:")
    print("[SEED]   • 6 pessoas limpas (sem pendências)")
    print("[SEED]   • 3 pessoas com mandado de prisão ativo (🔴 VERMELHO)")
    print("[SEED]   • 1 pessoa com pendência judicial menor (🟡 AMARELO)")
    print("[SEED] Veículos:")
    print("[SEED]   • 5 veículos limpos (sem pendências)")
    print("[SEED]   • 2 veículos com restrição de roubo (🔴 VERMELHO)")
    print("[SEED]   • 2 veículos com IPVA atrasado (🟡 AMARELO)")
    print("[SEED]   • 1 veículo com licenciamento vencido (🟡 AMARELO)")


if __name__ == "__main__":
    setup_database()
