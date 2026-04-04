"""
Módulo de Persistência — PostgreSQL.

Gerencia todas as operações de banco de dados:
- Tabela events (legado, mantida para compatibilidade)
- Tabelas novas: pessoas, veiculos, registros_acesso, alertas_justica
- Cache em memória para dashboard de alta performance
"""

import os
import datetime
import threading
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "prefeitura_ceres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# Lock para proteção da memória cache
_lock = threading.Lock()

# Cache em memória rápido para a API do dashboard
_cache = {
    "entries": 0, "exits": 0, "current": 0,
    "gender": {"Male": 0, "Female": 0, "Unknown": 0},
    "age": {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0},
    "last_events": []
}
_analyzed_ids = set()


# ══════════════════════════════════════════════
#  Conexão ao Banco
# ══════════════════════════════════════════════

def get_db_connection():
    """Cria uma conexão ao PostgreSQL."""
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME
        )
    except psycopg2.OperationalError as e:
        print(f"[STORAGE ERRO CRÍTICO] Falha ao conectar no PostgreSQL. Detalhes: {e}")
        return None


# ══════════════════════════════════════════════
#  Inicialização do Schema
# ══════════════════════════════════════════════

def init_storage():
    """Cria todas as tabelas necessárias e recarrega cache."""
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
            # ── Tabela events (legado) ──
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

            # ── Tabela pessoas ──
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

            # ── Tabela veículos ──
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

            # ── Tabela registros de acesso ──
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

            # ── Tabela alertas judiciais ──
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pessoas_cpf ON pessoas(cpf);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_veiculos_placa ON veiculos(placa);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_registros_timestamp ON registros_acesso(timestamp);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_registros_nivel ON registros_acesso(nivel_alerta);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alertas_nivel ON alertas_justica(nivel);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alertas_resolvido ON alertas_justica(resolvido);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);")

        conn.commit()
        conn.close()
        print("[STORAGE] Schema PostgreSQL inicializado com sucesso!")
        _reload_cache()


# ══════════════════════════════════════════════
#  Classificadores
# ══════════════════════════════════════════════

def _classify_age(age_raw):
    """Converte a faixa etária para uma categoria legível."""
    if age_raw in ('(0-2)', '(4-6)', '(8-12)'):
        return 'Criança (0-12)'
    elif age_raw in ('(15-20)', '(25-32)'):
        return 'Jovem (15-32)'
    elif age_raw in ('(38-43)', '(48-53)'):
        return 'Adulto (38-53)'
    elif age_raw == '(60-100)':
        return 'Idoso (60+)'
    return 'Desconhecido'


# ══════════════════════════════════════════════
#  Cache do Dashboard (Legado — events)
# ══════════════════════════════════════════════

def _reload_cache():
    """Lê tudo que já estava no PostgreSQL para restaurar o estado do painel."""
    global _cache, _analyzed_ids

    entries = 0
    exits = 0
    gender_dist = {"Male": 0, "Female": 0, "Unknown": 0}
    age_dist = {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0}
    ids = set()
    last_events = []

    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT timestamp, event_type, person_id, gender, age FROM events ORDER BY timestamp ASC")
            rows = cur.fetchall()

            for row in rows:
                if row['event_type'] == 'entry':
                    entries += 1
                elif row['event_type'] == 'exit':
                    exits += 1

                pid = str(row['person_id'])
                if pid and pid != "None" and pid not in ids:
                    gen = row.get('gender') or 'Unknown'
                    if gen in gender_dist:
                        gender_dist[gen] += 1
                    else:
                        gender_dist["Unknown"] += 1

                    age_cat = _classify_age(row.get('age') or 'Unknown')
                    age_dist[age_cat] += 1
                    ids.add(pid)

                t_stamp = str(row['timestamp'])
                if " " in t_stamp:
                    t_stamp = t_stamp.split(" ")[1][:8]
                elif "T" in t_stamp:
                    t_stamp = t_stamp.split("T")[1][:8]

                last_events.append({
                    "time": t_stamp,
                    "type": row['event_type'],
                    "gender": row.get('gender') or '?',
                    "age": row.get('age') or '?'
                })
    except Exception as e:
        print(f"[STORAGE ERRO] Falha ao recarregar a partir do PostgreSQL: {e}")
    finally:
        conn.close()

    with _lock:
        _cache["entries"] = entries
        _cache["exits"] = exits
        _cache["current"] = max(0, entries - exits)
        _cache["gender"] = gender_dist
        _cache["age"] = age_dist
        _cache["last_events"] = last_events[-20:]
        _analyzed_ids.update(ids)


def clear_storage():
    """Apaga os dados no Postgres e zera o cache."""
    global _cache, _analyzed_ids

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE events")
            conn.commit()
            print("[STORAGE] Tabela 'events' limpa no PostgreSQL.")
        except Exception as e:
            print(f"[STORAGE ERRO] Erro ao limpar tabela: {e}")
        finally:
            conn.close()

    with _lock:
        _cache["entries"] = 0
        _cache["exits"] = 0
        _cache["current"] = 0
        _cache["gender"] = {"Male": 0, "Female": 0, "Unknown": 0}
        _cache["age"] = {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0}
        _cache["last_events"] = []
        _analyzed_ids.clear()


def log_event(event_type, person_id, gender, age):
    """Registra evento na tabela events (legado) e atualiza cache."""
    global _cache, _analyzed_ids

    timestamp_obj = datetime.datetime.now()
    timestamp_str = timestamp_obj.isoformat()
    gender_val = gender if gender else "Unknown"
    age_val = age if age else "Unknown"

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO events (timestamp, event_type, person_id, gender, age) VALUES (%s, %s, %s, %s, %s)",
                    (timestamp_obj, event_type, str(person_id), gender_val, age_val)
                )
            conn.commit()
        except Exception as e:
            print(f"[STORAGE ERRO] Erro ao inserir evento no PostgreSQL: {e}")
        finally:
            conn.close()

    with _lock:
        if event_type == 'entry':
            _cache["entries"] += 1
        elif event_type == 'exit':
            _cache["exits"] += 1

        _cache["current"] = max(0, _cache["entries"] - _cache["exits"])

        pid_str = str(person_id)
        if pid_str not in _analyzed_ids:
            _analyzed_ids.add(pid_str)

            if gender_val in _cache["gender"]:
                _cache["gender"][gender_val] += 1
            else:
                _cache["gender"]["Unknown"] += 1

            age_cat = _classify_age(age_val)
            _cache["age"][age_cat] += 1

        _cache["last_events"].append({
            "time": timestamp_str.split("T")[1][:8],
            "type": event_type,
            "gender": gender_val,
            "age": age_val
        })
        if len(_cache["last_events"]) > 20:
            _cache["last_events"] = _cache["last_events"][-20:]


def get_current_stats():
    """Retorna estatísticas do cache para o dashboard."""
    with _lock:
        return {
            "entries": _cache["entries"],
            "exits": _cache["exits"],
            "current": _cache["current"],
            "gender": dict(_cache["gender"]),
            "age": dict(_cache["age"]),
            "last_events": list(_cache["last_events"])
        }


# ══════════════════════════════════════════════
#  CRUD — Pessoas
# ══════════════════════════════════════════════

def create_pessoa(nome, cpf=None, genero=None, idade_estimada=None,
                   status_judicial='limpo', tem_mandado=False,
                   foto_referencia_path=None, embedding=None):
    """Cadastra uma nova pessoa no banco."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO pessoas (nome, cpf, genero, idade_estimada, status_judicial,
                                     tem_mandado, foto_referencia_path, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (nome, cpf, genero, idade_estimada, status_judicial,
                  tem_mandado, foto_referencia_path, embedding))
            result = cur.fetchone()
        conn.commit()
        pid = result[0] if result else None
        print(f"[STORAGE] Pessoa cadastrada: {nome} (ID: {pid})")
        return pid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        print(f"[STORAGE] CPF {cpf} já existe no cadastro.")
        return None
    except Exception as e:
        conn.rollback()
        print(f"[STORAGE ERRO] Erro ao cadastrar pessoa: {e}")
        return None
    finally:
        conn.close()


def get_pessoa_by_id(pessoa_id):
    """Busca pessoa pelo ID."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM pessoas WHERE id = %s", (pessoa_id,))
            return cur.fetchone()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao buscar pessoa: {e}")
        return None
    finally:
        conn.close()


def get_all_pessoas():
    """Lista todas as pessoas cadastradas."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, nome, cpf, genero, idade_estimada, status_judicial, tem_mandado, ultima_aparicao FROM pessoas ORDER BY nome")
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao listar pessoas: {e}")
        return []
    finally:
        conn.close()


def get_all_pessoas_with_embeddings():
    """Carrega todas as pessoas que possuem embedding facial cadastrado."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, nome, embedding FROM pessoas WHERE embedding IS NOT NULL")
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao carregar embeddings: {e}")
        return []
    finally:
        conn.close()


def update_ultima_aparicao(pessoa_id):
    """Atualiza timestamp da última aparição de uma pessoa."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pessoas SET ultima_aparicao = CURRENT_TIMESTAMP, atualizado_em = CURRENT_TIMESTAMP WHERE id = %s",
                (pessoa_id,)
            )
        conn.commit()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao atualizar última aparição: {e}")
    finally:
        conn.close()


# ══════════════════════════════════════════════
#  CRUD — Veículos
# ══════════════════════════════════════════════

def create_veiculo(placa, modelo=None, cor=None, proprietario_id=None,
                    pendencias_detran=None, ipva_atrasado=False,
                    status_roubo=False, licenciamento_atrasado=False):
    """Cadastra um novo veículo no banco."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO veiculos (placa, modelo, cor, proprietario_id,
                                      pendencias_detran, ipva_atrasado,
                                      status_roubo, licenciamento_atrasado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (placa.upper(), modelo, cor, proprietario_id,
                  pendencias_detran, ipva_atrasado,
                  status_roubo, licenciamento_atrasado))
            result = cur.fetchone()
        conn.commit()
        vid = result[0] if result else None
        print(f"[STORAGE] Veículo cadastrado: {placa.upper()} (ID: {vid})")
        return vid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        print(f"[STORAGE] Placa {placa} já existe no cadastro.")
        return None
    except Exception as e:
        conn.rollback()
        print(f"[STORAGE ERRO] Erro ao cadastrar veículo: {e}")
        return None
    finally:
        conn.close()


def get_veiculo_by_placa(placa):
    """Busca veículo pela placa."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM veiculos WHERE placa = %s", (placa.upper(),))
            return cur.fetchone()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao buscar veículo: {e}")
        return None
    finally:
        conn.close()


def get_all_veiculos():
    """Lista todos os veículos cadastrados."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM veiculos ORDER BY placa")
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao listar veículos: {e}")
        return []
    finally:
        conn.close()


# ══════════════════════════════════════════════
#  Registros de Acesso
# ══════════════════════════════════════════════

def log_registro_acesso(tipo, modo_operacao=None, pessoa_id=None, veiculo_id=None,
                         nivel_alerta='verde', detalhes_alerta=None,
                         confianca_facial=None, placa_detectada=None,
                         acao_tomada=None, localizacao=None):
    """Insere um novo registro de acesso."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO registros_acesso
                    (tipo, modo_operacao, pessoa_id, veiculo_id, nivel_alerta,
                     detalhes_alerta, confianca_facial, placa_detectada, acao_tomada, localizacao)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tipo, modo_operacao, pessoa_id, veiculo_id, nivel_alerta,
                  detalhes_alerta, confianca_facial, placa_detectada, acao_tomada, localizacao))
        conn.commit()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao registrar acesso: {e}")
    finally:
        conn.close()


def get_registros_acesso(limit=50, offset=0):
    """Lista registros de acesso recentes."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM registros_acesso ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao listar registros de acesso: {e}")
        return []
    finally:
        conn.close()


# ══════════════════════════════════════════════
#  Alertas
# ══════════════════════════════════════════════

def log_alerta(pessoa_id=None, veiculo_id=None, tipo_alerta='pendencia_generica',
               nivel='amarelo', descricao=None):
    """Registra um alerta no banco de dados."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO alertas_justica (pessoa_id, veiculo_id, tipo_alerta, nivel, descricao)
                VALUES (%s, %s, %s, %s, %s)
            """, (pessoa_id, veiculo_id, tipo_alerta, nivel, descricao))
        conn.commit()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao registrar alerta: {e}")
    finally:
        conn.close()


def get_alertas_ativos(limit=30):
    """Lista alertas ativos (não resolvidos)."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT a.*, p.nome as pessoa_nome, v.placa as veiculo_placa
                FROM alertas_justica a
                LEFT JOIN pessoas p ON a.pessoa_id = p.id
                LEFT JOIN veiculos v ON a.veiculo_id = v.id
                WHERE a.resolvido = FALSE
                ORDER BY a.timestamp DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao listar alertas: {e}")
        return []
    finally:
        conn.close()


def get_alertas_historico(limit=50, offset=0):
    """Lista histórico completo de alertas."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT a.*, p.nome as pessoa_nome, v.placa as veiculo_placa
                FROM alertas_justica a
                LEFT JOIN pessoas p ON a.pessoa_id = p.id
                LEFT JOIN veiculos v ON a.veiculo_id = v.id
                ORDER BY a.timestamp DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            return cur.fetchall()
    except Exception as e:
        print(f"[STORAGE ERRO] Erro ao listar histórico de alertas: {e}")
        return []
    finally:
        conn.close()
