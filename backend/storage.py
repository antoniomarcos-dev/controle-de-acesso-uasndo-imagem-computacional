import csv
import os
import datetime
import threading

# ── Caminhos ──────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
STATS_FILE = os.path.join(DATA_DIR, 'stats.csv')

# Lock para escrita e leitura segura entre threads
_lock = threading.Lock()

# Cache em memória para evitar leitura repetitiva do CSV a cada segundo
_cache = {
    "entries": 0, "exits": 0, "current": 0,
    "gender": {"Male": 0, "Female": 0, "Unknown": 0},
    "age": {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0},
    "last_events": []
}
_analyzed_ids = set()


def init_storage():
    """Cria o diretório e o arquivo CSV caso não existam."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'event_type', 'person_id', 'gender', 'age'])
    else:
        # Se o CSV já existe (reiniciou o servidor), recarrega o cache
        _reload_cache()


def _classify_age(age_raw):
    """Converte a faixa etária do modelo Caffe para uma categoria legível."""
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
    """Reconstrói o cache lendo o CSV existente (apenas na inicialização)."""
    global _cache, _analyzed_ids

    entries = 0
    exits = 0
    gender_dist = {"Male": 0, "Female": 0, "Unknown": 0}
    age_dist = {"Criança (0-12)": 0, "Jovem (15-32)": 0, "Adulto (38-53)": 0, "Idoso (60+)": 0, "Desconhecido": 0}
    ids = set()
    last_events = []

    try:
        with open(STATS_FILE, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['event_type'] == 'entry':
                    entries += 1
                elif row['event_type'] == 'exit':
                    exits += 1

                pid = row['person_id']
                if pid and pid != "None" and pid not in ids:
                    gen = row.get('gender', 'Unknown')
                    if gen in gender_dist:
                        gender_dist[gen] += 1
                    else:
                        gender_dist["Unknown"] += 1

                    age_cat = _classify_age(row.get('age', 'Unknown'))
                    age_dist[age_cat] += 1
                    ids.add(pid)

                # Últimos eventos para timeline
                last_events.append({
                    "time": row['timestamp'][-8:] if len(row['timestamp']) > 8 else row['timestamp'],
                    "type": row['event_type'],
                    "gender": row.get('gender', '?'),
                    "age": row.get('age', '?')
                })
    except Exception:
        pass

    with _lock:
        _cache["entries"] = entries
        _cache["exits"] = exits
        _cache["current"] = max(0, entries - exits)
        _cache["gender"] = gender_dist
        _cache["age"] = age_dist
        _cache["last_events"] = last_events[-20:]  # Últimos 20
        _analyzed_ids.update(ids)


def log_event(event_type, person_id, gender, age):
    """Registra um evento e atualiza o cache em memória."""
    global _cache, _analyzed_ids

    timestamp = datetime.datetime.now().isoformat()
    gender_val = gender if gender else "Unknown"
    age_val = age if age else "Unknown"

    with _lock:
        # Escrever no CSV
        with open(STATS_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, event_type, str(person_id), gender_val, age_val])

        # Atualizar cache em memória (sem necessidade de reler o CSV)
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
            "time": timestamp[-8:],
            "type": event_type,
            "gender": gender_val,
            "age": age_val
        })
        # Manter apenas os últimos 20
        if len(_cache["last_events"]) > 20:
            _cache["last_events"] = _cache["last_events"][-20:]


def get_current_stats():
    """Retorna as estatísticas do cache em memória (leitura instantânea)."""
    with _lock:
        return {
            "entries": _cache["entries"],
            "exits": _cache["exits"],
            "current": _cache["current"],
            "gender": dict(_cache["gender"]),
            "age": dict(_cache["age"]),
            "last_events": list(_cache["last_events"])
        }
