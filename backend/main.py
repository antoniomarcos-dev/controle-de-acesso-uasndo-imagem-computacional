"""
API Principal — Sistema de Segurança Integrada.

Endpoints:
- Legado: /api/stats, /api/video_feed, /api/config, /api/reset, /api/shutdown
- Novo:   /api/alerts, /api/mode, /api/pessoas, /api/veiculos, /api/registros, /api/gate, /api/security
"""

from fastapi import FastAPI, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import sys
import contextlib

try:
    from backend import storage
    from backend.video_processor import VideoProcessor
    from backend.models import (
        ConfigPayload, ModoPayload, PessoaCreate, VeiculoCreate, GateOverride,
        ModoOperacao, AcaoTomada
    )
except ImportError:
    from . import storage
    from .video_processor import VideoProcessor
    from .models import (
        ConfigPayload, ModoPayload, PessoaCreate, VeiculoCreate, GateOverride,
        ModoOperacao, AcaoTomada
    )

processor = None

# ── Configuração de câmera via argumento ou variável de ambiente ──
CAMERA_SOURCE = 0


def parse_camera_arg():
    """Detecta o argumento --camera na linha de comando."""
    global CAMERA_SOURCE
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--camera" and i + 1 < len(args):
            val = args[i + 1]
            try:
                CAMERA_SOURCE = int(val)
            except ValueError:
                CAMERA_SOURCE = val
            print(f"[CONFIG] Camera configurada: {CAMERA_SOURCE}")
            return
    env_cam = os.environ.get("SMART_COUNTER_CAMERA")
    if env_cam:
        try:
            CAMERA_SOURCE = int(env_cam)
        except ValueError:
            CAMERA_SOURCE = env_cam
        print(f"[CONFIG] Camera via env: {CAMERA_SOURCE}")

# Executa a configuração da câmera independentemente de como o script foi iniciado
parse_camera_arg()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global processor
    storage.init_storage()

    processor = VideoProcessor(camera_id=CAMERA_SOURCE)

    # Definir modo inicial do .env
    initial_mode = os.getenv("SYSTEM_MODE", "cidade")
    if initial_mode == "evento":
        processor.security_engine.set_modo(ModoOperacao.EVENTO)

    processor.start()
    print(f"\n[CERES SECURITY] Dashboard disponível em: http://localhost:8000")
    print(f"[CERES SECURITY] Camera: {CAMERA_SOURCE}")
    print(f"[CERES SECURITY] Modo: {processor.security_engine.get_modo().value}\n")
    yield
    if processor:
        processor.stop()


app = FastAPI(
    lifespan=lifespan,
    title="Ceres Security AI",
    description="Sistema de Segurança Integrada — Controle de Acesso com Visão Computacional",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')

if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


# ══════════════════════════════════════════════
#  Frontend
# ══════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve o dashboard principal."""
    index_path = os.path.join(frontend_dir, 'index.html')
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<h1>Frontend nao encontrado</h1>", status_code=404)


# ══════════════════════════════════════════════
#  Streaming de Vídeo
# ══════════════════════════════════════════════

def video_generator():
    """Gera fluxo contínuo de frames em MJPEG."""
    import time
    while True:
        if processor and processor.latest_frame:
            with processor.frame_lock:
                frame = processor.latest_frame
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.1)


@app.get("/api/video_feed")
def video_feed():
    """Endpoint para streaming MJPEG do processamento de vídeo."""
    return StreamingResponse(video_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


# ══════════════════════════════════════════════
#  Stats (Legado + Expandido)
# ══════════════════════════════════════════════

@app.get("/api/stats")
def fetch_stats():
    """Retorna estatísticas em tempo real (contagem + segurança)."""
    base_stats = storage.get_current_stats()

    # Adicionar dados de segurança
    if processor:
        security = processor.security_engine.get_security_stats()
        base_stats["security"] = security
        base_stats["last_plates"] = processor.alpr_processor.get_last_plates()[-5:]
        base_stats["last_face_matches"] = processor.face_processor.get_last_matches()[-5:]
        base_stats["alertas_recentes"] = processor.security_engine.get_alertas_recentes()[-10:]
    else:
        base_stats["security"] = {}
        base_stats["last_plates"] = []
        base_stats["last_face_matches"] = []
        base_stats["alertas_recentes"] = []

    return base_stats


# ══════════════════════════════════════════════
#  Configuração da Câmera
# ══════════════════════════════════════════════

@app.get("/api/config")
def get_config():
    if not processor:
        return {"error": "Processador offline"}
    return {
        "mirror_camera": getattr(processor, 'mirror_camera', False),
        "swap_direction": getattr(processor, 'swap_direction', False),
        "face_conf_threshold": getattr(processor, 'face_conf_threshold', 0.35),
        "camera_source": getattr(processor, 'camera_id', 0)
    }


@app.post("/api/config")
def set_config(payload: ConfigPayload):
    if processor:
        processor.mirror_camera = payload.mirror_camera
        processor.swap_direction = payload.swap_direction
        processor.face_conf_threshold = payload.face_conf_threshold
        if payload.camera_source is not None and getattr(processor, 'camera_id', None) != payload.camera_source:
             processor.update_camera(payload.camera_source)
    return {"status": "ok"}


# ══════════════════════════════════════════════
#  Modo de Operação (Cidade / Evento)
# ══════════════════════════════════════════════

@app.get("/api/mode")
def get_mode():
    """Retorna o modo de operação atual."""
    if not processor:
        return {"modo": "offline"}
    return {
        "modo": processor.security_engine.get_modo().value,
        "gate_status": processor.security_engine.get_gate_status()
    }


@app.post("/api/mode")
def set_mode(payload: ModoPayload):
    """Alterna entre modo Cidade e Evento."""
    if processor:
        processor.security_engine.set_modo(payload.modo)
    return {
        "status": "ok",
        "modo": payload.modo.value
    }


# ══════════════════════════════════════════════
#  Cancela Virtual (Modo Evento)
# ══════════════════════════════════════════════

@app.get("/api/gate")
def get_gate():
    """Retorna estado da cancela virtual."""
    if not processor:
        return {"status": "offline"}
    return {"gate_status": processor.security_engine.get_gate_status()}


@app.post("/api/gate/override")
def gate_override(payload: GateOverride):
    """Override manual do operador na cancela."""
    if processor:
        processor.security_engine.override_gate(payload.acao, payload.motivo)
    return {
        "status": "ok",
        "gate_status": processor.security_engine.get_gate_status()
    }


# ══════════════════════════════════════════════
#  Alertas
# ══════════════════════════════════════════════

@app.get("/api/alerts")
def get_alerts():
    """Lista alertas ativos (não resolvidos)."""
    alerts = storage.get_alertas_ativos()
    # Converter datetime para string
    for a in alerts:
        if a.get('timestamp'):
            a['timestamp'] = str(a['timestamp'])
    return {"alerts": alerts}


@app.get("/api/alerts/history")
def get_alerts_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """Histórico completo de alertas."""
    alerts = storage.get_alertas_historico(limit=limit, offset=offset)
    for a in alerts:
        if a.get('timestamp'):
            a['timestamp'] = str(a['timestamp'])
    return {"alerts": alerts, "limit": limit, "offset": offset}


# ══════════════════════════════════════════════
#  CRUD — Pessoas
# ══════════════════════════════════════════════

@app.get("/api/pessoas")
def list_pessoas():
    """Lista todas as pessoas cadastradas."""
    pessoas = storage.get_all_pessoas()
    for p in pessoas:
        if p.get('ultima_aparicao'):
            p['ultima_aparicao'] = str(p['ultima_aparicao'])
    return {"pessoas": pessoas}


@app.post("/api/pessoas")
def create_pessoa(payload: PessoaCreate):
    """Cadastra uma nova pessoa no sistema."""
    pid = storage.create_pessoa(
        nome=payload.nome,
        cpf=payload.cpf,
        genero=payload.genero,
        idade_estimada=payload.idade_estimada,
        status_judicial=payload.status_judicial,
        tem_mandado=payload.tem_mandado,
        foto_referencia_path=payload.foto_referencia_path
    )
    if pid:
        return {"status": "ok", "id": pid}
    return {"status": "error", "message": "Falha ao cadastrar (CPF duplicado?)"}


# ══════════════════════════════════════════════
#  CRUD — Veículos
# ══════════════════════════════════════════════

@app.get("/api/veiculos")
def list_veiculos():
    """Lista todos os veículos cadastrados."""
    veiculos = storage.get_all_veiculos()
    for v in veiculos:
        if v.get('criado_em'):
            v['criado_em'] = str(v['criado_em'])
        if v.get('atualizado_em'):
            v['atualizado_em'] = str(v['atualizado_em'])
    return {"veiculos": veiculos}


@app.post("/api/veiculos")
def create_veiculo(payload: VeiculoCreate):
    """Cadastra um novo veículo no sistema."""
    vid = storage.create_veiculo(
        placa=payload.placa,
        modelo=payload.modelo,
        cor=payload.cor,
        proprietario_id=payload.proprietario_id,
        pendencias_detran=payload.pendencias_detran,
        ipva_atrasado=payload.ipva_atrasado,
        status_roubo=payload.status_roubo,
        licenciamento_atrasado=payload.licenciamento_atrasado
    )
    if vid:
        return {"status": "ok", "id": vid}
    return {"status": "error", "message": "Falha ao cadastrar (placa duplicada?)"}


# ══════════════════════════════════════════════
#  Registros de Acesso
# ══════════════════════════════════════════════

@app.get("/api/registros")
def list_registros(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """Lista registros de acesso recentes."""
    registros = storage.get_registros_acesso(limit=limit, offset=offset)
    for r in registros:
        if r.get('timestamp'):
            r['timestamp'] = str(r['timestamp'])
    return {"registros": registros, "limit": limit, "offset": offset}


# ══════════════════════════════════════════════
#  Segurança — Stats detalhados
# ══════════════════════════════════════════════

@app.get("/api/security")
def get_security_stats():
    """Retorna estatísticas detalhadas de segurança."""
    if not processor:
        return {"error": "Processador offline"}

    return {
        **processor.security_engine.get_security_stats(),
        "last_plates": processor.alpr_processor.get_last_plates(),
        "last_face_matches": processor.face_processor.get_last_matches(),
        "alertas": processor.security_engine.get_alertas_recentes()
    }


# ══════════════════════════════════════════════
#  Reset / Shutdown
# ══════════════════════════════════════════════

@app.post("/api/reset")
def reset_stats():
    """Reseta as estatísticas (limpa cache e tabela events)."""
    storage.clear_storage()
    if processor:
        processor.tracked_objects.clear()
        processor.demographics.clear()
    return {"status": "ok"}


@app.post("/api/shutdown")
def shutdown_server(background_tasks: BackgroundTasks):
    """Encerra o servidor e libera a câmera."""
    print("\n[CERES SECURITY] Desligamento solicitado pelo Dashboard...")

    def kill_server():
        import time
        time.sleep(1)
        os._exit(0)

    background_tasks.add_task(kill_server)
    return {"status": "shutting_down"}


# ══════════════════════════════════════════════
#  Entrypoint
# ══════════════════════════════════════════════

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  CERES SECURITY AI — Sistema de Segurança Integrada")
    print(f"  Camera: {CAMERA_SOURCE}")
    print("  Dashboard: http://localhost:8000")
    print()
    print("  Uso:")
    print("    python -m backend.main              (webcam padrão)")
    print("    python -m backend.main --camera 1   (câmera HDMI)")
    print("    python -m backend.main --camera 2   (outro dispositivo)")
    print("=" * 60)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000)
