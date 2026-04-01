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

# Cache em memória rápido para a API do dashboard não ter que buscar no Postgres a cada 1 segundo (alta performance)
_cache = {
    "entries": 0, "exits": 0, "current": 0,
    "gender": {"Male": 0, "Female": 0, "Unknown": 0},
    "age": {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0},
    "last_events": []
}
_analyzed_ids = set()

def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME
        )
    except psycopg2.OperationalError as e:
        print(f"[STORAGE ERRO CRÍTICO] Falha ao conectar no PostgreSQL. Verifique credenciais e se o banco está online. Detalhes: {e}")
        return None


def init_storage():
    """Garante que a tabela existe no PostgreSQL e recarrega o estado atual para o cache."""
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cur:
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
        conn.commit()
        conn.close()
        _reload_cache()


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


def _reload_cache():
    """Lê tudo que já estava no PostgreSQL para restaurar o estado do painel, caso seja um restart."""
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
                # Formata a HORA apenas pro log
                if " " in t_stamp: t_stamp = t_stamp.split(" ")[1][:8]
                elif "T" in t_stamp: t_stamp = t_stamp.split("T")[1][:8]

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
        _cache["last_events"] = last_events[-20:]  # Apenas os últimos 20
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
            print("[STORAGE] Tabela 'events' limpa no PostgreSQL através de request.")
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
    """Registra evento via query SQL no PostgreSQL e empurra no cache instantaneo."""
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
    """A API do dashboard continua extremamente rápida através desse cache."""
    with _lock:
        return {
            "entries": _cache["entries"],
            "exits": _cache["exits"],
            "current": _cache["current"],
            "gender": dict(_cache["gender"]),
            "age": dict(_cache["age"]),
            "last_events": list(_cache["last_events"])
        }
