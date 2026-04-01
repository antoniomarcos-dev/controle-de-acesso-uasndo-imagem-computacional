import cv2
import numpy as np
from ultralytics import YOLO
import os
import threading
import time

try:
    from backend import storage
except ImportError:
    from . import storage


class VideoProcessor:
    """
    Motor de Visão Computacional para contagem inteligente de pessoas.
    
    Pipeline:
    1. YOLOv8n detecta pessoas (classe 0) com rastreamento BoT-SORT persistente.
    2. Para cada pessoa rastreada, extrai a região superior do corpo.
    3. O OpenCV DNN Face Detector localiza o rosto nessa região.
    4. Redes Caffe (Levi-Hassner) estimam gênero e faixa etária a partir do crop facial.
    5. A lógica de linha virtual determina entrada/saída pela direção do movimento.
    6. Salva evento no CSV com a moda das predições demográficas acumuladas.
    """

    def __init__(self, camera_id=0):
        self.camera_id = camera_id
        self.line_x = None          # Linha vertical calculada no primeiro frame (50% da largura)
        self.tracked_objects = {}   # track_id -> {'positions': [cx...], 'counted': bool}
        self.demographics = {}     # track_id -> {'genders': [], 'ages': [], 'final_g': None, 'final_a': None}
        self.running = False
        self.thread = None
        self.latest_frame = None   # Para streaming opcional no futuro
        self.frame_lock = threading.Lock()
        self.fps = 0

        # Propriedades de Configuracao Dinamica
        self.mirror_camera = False
        self.swap_direction = False
        self.face_conf_threshold = 0.35

        # ── YOLOv8 Nano ──────────────────────────────────────
        print("[SMART-COUNTER] Carregando YOLOv8n...")
        self.yolo = YOLO('yolov8n.pt')

        # ── Modelos Demográficos (DeepFace) ─────────────────
        print("[SMART-COUNTER] Preparando DeepFace para extração de gênero/idade...")
        try:
            from deepface import DeepFace
            self.has_demographics = True
            print("[SMART-COUNTER] DeepFace ativado com sucesso!")
        except ImportError:
            self.has_demographics = False
            print("[SMART-COUNTER] AVISO: DeepFace ausente. Execute: pip install deepface tf-keras")

        # Mantemos apenas para legado de labels, a conversão é feita no storage
        self.AGE_BUCKETS = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        self.GENDER_LABELS = ['Male', 'Female']

    # ── Predição de Idade e Gênero (Via DeepFace) ────────────────
    def _predict_demographics(self, image_crop):
        """Usa DeepFace para estimar gênero e idade acurados de um corte."""
        if image_crop.size == 0:
            return None, None

        try:
            from deepface import DeepFace
            # analyze tenta detectar o rosto dentro do crop e analisa
            results = DeepFace.analyze(
                img_path=image_crop,
                actions=['age', 'gender'],
                enforce_detection=False,
                silent=True
            )
            
            res = results[0] if isinstance(results, list) else results
            
            # Mapeia "Woman"/"Man" para nosso padrao
            dom_g = res.get('dominant_gender')
            if dom_g == 'Man':
                gender = 'Male'
            elif dom_g == 'Woman':
                gender = 'Female'
            else:
                gender = 'Unknown'

            deep_age = res.get('age', 0)
            
            # Transforma idade precisa nos buckets do legacy
            if deep_age <= 12:
                age_cat = '(8-12)'
            elif deep_age <= 32:
                age_cat = '(25-32)'
            elif deep_age <= 53:
                age_cat = '(38-43)'
            else:
                age_cat = '(60-100)'

            return gender, age_cat
        except Exception as e:
            # Em caso de falha oculta da IA, descarta no quadro atual
            return None, None

    # ── Modo Estatístico (Moda) ──────────────────────────────────
    @staticmethod
    def _mode(lst):
        """Retorna o elemento mais frequente de uma lista."""
        if not lst:
            return "Unknown"
        return max(set(lst), key=lst.count)

    # ── Processamento de Frame ───────────────────────────────────
    def process_frame(self, frame, frame_count=0):
        """Pipeline completo: YOLO → Track → Demographics → Line Crossing."""
        # Aplicar espelhamento se configurado no painel
        if getattr(self, 'mirror_camera', False):
            frame = cv2.flip(frame, 1)

        h, w = frame.shape[:2]

        # Linha virtual VERTICAL posicionada a 50% da largura (centro da porta)
        if self.line_x is None:
            self.line_x = w // 2

        # Cópia limpa ANTES de desenhar qualquer sobreposição
        # (fundamental para que o face detector não confunda anotações com rostos)
        clean_frame = frame.copy()

        # ── YOLO + ByteTrack Tracking (Otimizado p/ CPU) ──
        results = self.yolo.track(
            frame, persist=True, classes=[0],
            verbose=False, tracker="bytetrack.yaml",
            conf=0.4, iou=0.5, imgsz=480
        )

        if results and results[0].boxes and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            ids   = results[0].boxes.id.int().cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()

            for box, tid, conf in zip(boxes, ids, confs):
                x1, y1, x2, y2 = map(int, box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                # ── Inicializar rastreamento ──
                if tid not in self.tracked_objects:
                    self.tracked_objects[tid] = {'positions': [], 'counted': False}
                if tid not in self.demographics:
                    self.demographics[tid] = {'genders': [], 'ages': [], 'final_g': None, 'final_a': None}

                self.tracked_objects[tid]['positions'].append(cx)
                demo = self.demographics[tid]

                # ── Estimar dados demográficos (máx 3 tentativas, com escalonamento por ID) ──
                # Usa (frame_count + tid) % 10 == 0 para diluir a carga pesada entre os frames,
                # e exige menos tentativas (3) para não segurar o processador.
                if self.has_demographics and demo['final_g'] is None and len(demo['genders']) < 3 and (frame_count + tid) % 10 == 0:
                    box_h = y2 - y1
                    # Pega os 55% superiores do corpo (onde fica o rosto)
                    crop_y2 = y1 + int(box_h * 0.55)
                    upper_body = clean_frame[max(0, y1):min(h, crop_y2), max(0, x1):min(w, x2)]

                    if upper_body.size > 0:
                        gender, age = self._predict_demographics(upper_body)
                        if gender and age:
                            demo['genders'].append(gender)
                            demo['ages'].append(age)

                # ── Consolidar após 3+ amostras (ou menos se a pessoa cruzar a linha em breve) ──
                if demo['final_g'] is None and len(demo['genders']) >= 3:
                    demo['final_g'] = self._mode(demo['genders'])
                    demo['final_a'] = self._mode(demo['ages'])

                # ── Lógica da Linha Virtual (VERTICAL) ──
                obj = self.tracked_objects[tid]
                if not obj['counted'] and len(obj['positions']) >= 3:
                    first_x = obj['positions'][0]
                    evento = None

                    # Logica base da Direcao
                    is_entry = (first_x < self.line_x and cx >= self.line_x)  # Esq -> Dir = Entra
                    is_exit = (first_x > self.line_x and cx <= self.line_x)   # Dir -> Esq = Sai

                    # Inverter logica se habilitado no painel
                    if getattr(self, 'swap_direction', False):
                        is_entry, is_exit = is_exit, is_entry

                    if is_entry:
                        evento = "entry"
                    elif is_exit:
                        evento = "exit"

                    if evento:
                        obj['counted'] = True
                        # Se não consolidou ainda, tenta com o que tem
                        if demo['final_g'] is None and demo['genders']:
                            demo['final_g'] = self._mode(demo['genders'])
                            demo['final_a'] = self._mode(demo['ages'])

                        f_g = demo['final_g'] or "Unknown"
                        f_a = demo['final_a'] or "Unknown"
                        storage.log_event(evento, tid, f_g, f_a)
                        print(f"[EVENT] {evento.upper()} | ID:{tid} | {f_g} | {f_a}")

                # ── Desenhar overlay visual LIMPO ──
                is_counted = obj['counted']
                color = (80, 200, 120) if is_counted else (200, 180, 60)  # verde se contado, amarelo se não

                # Retângulo fino com cantos arredondados simulados
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
                # Pequenos cantos destacados
                corner_len = 15
                cv2.line(frame, (x1, y1), (x1 + corner_len, y1), color, 3)
                cv2.line(frame, (x1, y1), (x1, y1 + corner_len), color, 3)
                cv2.line(frame, (x2, y1), (x2 - corner_len, y1), color, 3)
                cv2.line(frame, (x2, y1), (x2, y1 + corner_len), color, 3)
                cv2.line(frame, (x1, y2), (x1 + corner_len, y2), color, 3)
                cv2.line(frame, (x1, y2), (x1, y2 - corner_len), color, 3)
                cv2.line(frame, (x2, y2), (x2 - corner_len, y2), color, 3)
                cv2.line(frame, (x2, y2), (x2, y2 - corner_len), color, 3)

                # Ponto central
                cv2.circle(frame, (cx, cy), 3, color, -1)

                # Label compacta
                g_label = demo.get('final_g') or ("..." if demo['genders'] else "?")
                a_label = demo.get('final_a') or ""
                # Traduzir
                g_display = {"Male": "M", "Female": "F", "Unknown": "?"}.get(g_label, g_label)
                label = f"#{tid} {g_display}"
                if a_label:
                    label += f" {a_label}"

                # Fundo do label
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 8, y1), color, -1)
                cv2.putText(frame, label, (x1 + 4, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)

        # ── Desenhar linha virtual VERTICAL elegante ──
        overlay = frame.copy()
        cv2.rectangle(overlay, (self.line_x - 1, 0), (self.line_x + 1, h), (0, 220, 220), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        # Setas indicando direção horizontal (ajustaveis pelo painel)
        arrow_x_right = self.line_x + 20
        arrow_x_left = self.line_x - 20
        swap = getattr(self, 'swap_direction', False)

        # Seta para direita
        label_right = "SAI" if swap else "ENTRA"
        color_right = (100, 130, 255) if swap else (80, 200, 120)
        cv2.arrowedLine(frame, (arrow_x_right, 30), (arrow_x_right + 18, 30), color_right, 1, tipLength=0.4)
        cv2.putText(frame, label_right, (arrow_x_right + 22, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color_right, 1, cv2.LINE_AA)

        # Seta para esquerda
        label_left = "ENTRA" if swap else "SAI"
        color_left = (80, 200, 120) if swap else (100, 130, 255)
        cv2.arrowedLine(frame, (arrow_x_left, 30), (arrow_x_left - 18, 30), color_left, 1, tipLength=0.4)
        cv2.putText(frame, label_left, (arrow_x_left - 50, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color_left, 1, cv2.LINE_AA)

        # ── Painel de status discreto no canto ──
        stats = storage.get_current_stats()
        info_lines = [
            f"Entradas: {stats['entries']}",
            f"Saidas:   {stats['exits']}",
            f"Presentes: {stats['current']}",
        ]
        for i, line in enumerate(info_lines):
            y_pos = 22 + i * 18
            cv2.putText(frame, line, (w - 160, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

        return frame

    # ── Loop de Captura ──────────────────────────────────────────
    def capture_loop(self):
        self.running = True
        cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print("[SMART-COUNTER] Fallback para backend padrão de captura...")
            cap = cv2.VideoCapture(self.camera_id)

        if not cap.isOpened():
            print(f"[SMART-COUNTER] ERRO: Câmera {self.camera_id} indisponível.")
            self.running = False
            return

        # Configurar resolução ágil para CPU (480p)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        print("[SMART-COUNTER] Pipeline de visão computacional iniciado!")
        frame_count = 0
        total_frames = 0
        t_start = time.time()

        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            out_frame = self.process_frame(frame, total_frames)
            frame_count += 1
            total_frames += 1

            # Calcular FPS real
            elapsed = time.time() - t_start
            if elapsed >= 1.0:
                self.fps = frame_count / elapsed
                frame_count = 0
                t_start = time.time()

            # FPS no canto
            cv2.putText(out_frame, f"{self.fps:.0f} FPS", (out_frame.shape[1] - 80, out_frame.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1, cv2.LINE_AA)

            # Codificar frame e atualizar buffer de streaming seguro
            ret_encode, buffer = cv2.imencode('.jpg', out_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ret_encode:
                with self.frame_lock:
                    self.latest_frame = buffer.tobytes()

        cap.release()
        cv2.destroyAllWindows()

    def start(self):
        if not self.running:
            self.thread = threading.Thread(target=self.capture_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
