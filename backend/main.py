from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import os
import sys
import contextlib

try:
    from backend import storage
    from backend.video_processor import VideoProcessor
except ImportError:
    from . import storage
    from .video_processor import VideoProcessor

processor = None

# ── Configuracao de camera via argumento ou variavel de ambiente ──
# Uso: python -m backend.main --camera 1
#      python -m backend.main --camera "rtsp://192.168.1.100:554/stream"
CAMERA_SOURCE = 0  # Default: webcam integrada


def parse_camera_arg():
    """Detecta o argumento --camera na linha de comando."""
    global CAMERA_SOURCE
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--camera" and i + 1 < len(args):
            val = args[i + 1]
            # Se for numero, usar como indice inteiro
            try:
                CAMERA_SOURCE = int(val)
            except ValueError:
                # Caso contrario, usar como URL/string (RTSP, arquivo de video, etc)
                CAMERA_SOURCE = val
            print(f"[CONFIG] Camera configurada: {CAMERA_SOURCE}")
            return
    # Checar variavel de ambiente como fallback
    env_cam = os.environ.get("SMART_COUNTER_CAMERA")
    if env_cam:
        try:
            CAMERA_SOURCE = int(env_cam)
        except ValueError:
            CAMERA_SOURCE = env_cam
        print(f"[CONFIG] Camera via env: {CAMERA_SOURCE}")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global processor
    storage.init_storage()

    processor = VideoProcessor(camera_id=CAMERA_SOURCE)
    processor.start()
    print(f"[SMART-COUNTER] Dashboard disponivel em: http://localhost:8000")
    print(f"[SMART-COUNTER] Camera: {CAMERA_SOURCE}")
    yield
    if processor:
        processor.stop()


app = FastAPI(lifespan=lifespan, title="Smart Counter AI")

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


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve o dashboard principal."""
    index_path = os.path.join(frontend_dir, 'index.html')
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<h1>Frontend nao encontrado</h1>", status_code=404)


@app.get("/api/stats")
def fetch_stats():
    """Retorna estatisticas em tempo real (leitura do cache em memoria)."""
    return storage.get_current_stats()


class ConfigPayload(BaseModel):
    mirror_camera: bool
    swap_direction: bool
    face_conf_threshold: float

@app.get("/api/config")
def get_config():
    if not processor:
        return {"error": "Processador offline"}
    return {
        "mirror_camera": getattr(processor, 'mirror_camera', False),
        "swap_direction": getattr(processor, 'swap_direction', False),
        "face_conf_threshold": getattr(processor, 'face_conf_threshold', 0.35)
    }

@app.post("/api/config")
def set_config(payload: ConfigPayload):
    if processor:
        processor.mirror_camera = payload.mirror_camera
        processor.swap_direction = payload.swap_direction
        processor.face_conf_threshold = payload.face_conf_threshold
    return {"status": "ok"}


@app.post("/api/reset")
def reset_stats():
    """Reseta as estatisticas (apaga o CSV e limpa o cache)."""
    storage.init_storage()
    return {"status": "ok"}


@app.post("/api/shutdown")
def shutdown_server(background_tasks: BackgroundTasks):
    """Encerra o servidor e libera a camera."""
    print("\n[SMART-COUNTER] Desligamento solicitado pelo Dashboard...")
    
    def kill_server():
        import time
        time.sleep(1)
        os._exit(0)
        
    background_tasks.add_task(kill_server)
    return {"status": "shutting_down"}


if __name__ == "__main__":
    parse_camera_arg()

    print()
    print("=" * 55)
    print("  SMART COUNTER AI - Servidor Iniciando...")
    print(f"  Camera: {CAMERA_SOURCE}")
    print("  Dashboard: http://localhost:8000")
    print()
    print("  Uso:")
    print("    python -m backend.main              (webcam padrao)")
    print("    python -m backend.main --camera 1   (Nikon via HDMI)")
    print("    python -m backend.main --camera 2   (outro dispositivo)")
    print("=" * 55)
    print()
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
