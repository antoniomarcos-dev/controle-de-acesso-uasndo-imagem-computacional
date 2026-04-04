"""
Motor de Visão Computacional — Orquestração Assíncrona.

Pipeline:
1. YOLOv8n detecta pessoas (classe 0) e veículos (classes 2,3,5,7) com rastreamento
2. Crops de pessoas → Thread Pool → Face Processor (embedding + idade/gênero + DB match)
3. Bboxes de veículos → Thread Pool → ALPR Processor (crop + OCR + validação)
4. Resultados → Security Engine (check pendências + decisão + alerta)
5. Overlay visual no frame (boxes, labels, alertas)
6. Codificação MJPEG para streaming no dashboard

Arquitetura: O loop de captura NUNCA espera resultado de Face/ALPR.
Os resultados chegam via callback e são sobrepostos no próximo frame.
"""

import cv2
import numpy as np
from ultralytics import YOLO
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

try:
    from backend import storage
    from backend.face_processor import FaceProcessor
    from backend.alpr_processor import ALPRProcessor
    from backend.business_logic import SecurityEngine
except ImportError:
    from . import storage
    from .face_processor import FaceProcessor
    from .alpr_processor import ALPRProcessor
    from .business_logic import SecurityEngine


class VideoProcessor:
    """
    Motor de Visão Computacional para segurança integrada.

    Pipeline com processamento paralelo:
    - Thread Principal: Captura + YOLO + Overlay + Encoding
    - Thread Pool 1: Análise facial (DeepFace embedding + matching)
    - Thread Pool 2: ALPR (EasyOCR + validação de placas)
    - Security Engine: Checkagem de pendências e decisão de ação
    """

    # Classes YOLO para veículos
    VEHICLE_CLASSES = {2: 'car', 3: 'motorcycle', 5: 'bus', 7: 'truck'}

    def __init__(self, camera_id=0):
        self.camera_id = camera_id
        self.line_x = None
        self.tracked_objects = {}
        self.demographics = {}
        self.running = False
        self.thread = None
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.fps = 0

        # Configuração dinâmica
        self.mirror_camera = False
        self.swap_direction = False
        self.face_conf_threshold = 0.35

        # ── YOLOv8 Nano ──
        print("[VISION] Carregando YOLOv8n...")
        self.yolo = YOLO('yolov8n.pt')

        # ── Módulos de Processamento ──
        self.face_processor = FaceProcessor(
            match_threshold=float(os.getenv("FACE_MATCH_THRESHOLD", "0.68"))
        )
        self.alpr_processor = ALPRProcessor(
            ocr_threshold=float(os.getenv("PLATE_OCR_THRESHOLD", "0.45"))
        )
        self.security_engine = SecurityEngine()

        # ── Thread Pools para processamento paralelo ──
        self.face_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face")
        self.alpr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpr")

        # ── Resultados assíncronos (atualizados pelas threads) ──
        self._async_results_lock = threading.Lock()
        self._pending_face_results = {}     # track_id -> FaceResult (mais recente)
        self._pending_plate_results = []    # [PlateResult, ...]
        self._active_alerts = []            # [{nivel, descricao, timestamp, ttl}, ...]
        self._alert_display_ttl = 8         # Segundos para mostrar alerta na tela

        # ── Controle de throttle ──
        self._face_in_progress = set()      # track_ids sendo processados
        self._alpr_in_progress = False       # Flag de ALPR em andamento
        self._last_alpr_time = 0             # Timestamp do último ALPR

        # ── DeepFace para demografia legada ──
        self.has_demographics = True
        try:
            from deepface import DeepFace
            print("[VISION] DeepFace ativado!")
        except ImportError:
            self.has_demographics = False
            print("[VISION] AVISO: DeepFace ausente. Execute: pip install deepface tf-keras")

        # Legado
        self.AGE_BUCKETS = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        self.GENDER_LABELS = ['Male', 'Female']

        # Localização do ponto de monitoramento
        self._location = os.getenv("SYSTEM_LOCATION", "Portaria Principal")

    # ══════════════════════════════════════════════
    #  Callbacks de Processamento Assíncrono
    # ══════════════════════════════════════════════

    def _on_face_result(self, track_id, future):
        """Callback quando a análise facial termina."""
        try:
            face_result = future.result()
            if face_result is None:
                return

            with self._async_results_lock:
                self._pending_face_results[track_id] = face_result
                self._face_in_progress.discard(track_id)

            # Se identificou alguém, acionar o security engine
            if face_result.pessoa_id is not None:
                storage.update_ultima_aparicao(face_result.pessoa_id)
                result = self.security_engine.processar_deteccao(
                    pessoa_id=face_result.pessoa_id,
                    genero=face_result.genero,
                    idade_cat=face_result.idade_categoria,
                    confianca_facial=face_result.confianca,
                    localizacao=self._location
                )
                if result and result['nivel'] != 'verde':
                    self._add_display_alert(result['nivel'], result['descricao'])

        except Exception as e:
            print(f"[VISION] Erro no callback facial: {e}")
            with self._async_results_lock:
                self._face_in_progress.discard(track_id)

    def _on_alpr_result(self, future):
        """Callback quando a leitura de placas termina."""
        try:
            plate_results = future.result()
            with self._async_results_lock:
                self._alpr_in_progress = False

            if not plate_results:
                return

            for pr in plate_results:
                with self._async_results_lock:
                    self._pending_plate_results.append(pr)
                    # Manter apenas as 10 últimas
                    if len(self._pending_plate_results) > 10:
                        self._pending_plate_results = self._pending_plate_results[-10:]

                # Acionar security engine para cada placa
                result = self.security_engine.processar_deteccao(
                    placa=pr.placa,
                    localizacao=self._location
                )
                if result and result['nivel'] != 'verde':
                    self._add_display_alert(result['nivel'], result['descricao'])

        except Exception as e:
            print(f"[VISION] Erro no callback ALPR: {e}")
            with self._async_results_lock:
                self._alpr_in_progress = False

    def _add_display_alert(self, nivel, descricao):
        """Adiciona alerta para exibição no overlay do vídeo."""
        with self._async_results_lock:
            self._active_alerts.append({
                "nivel": nivel,
                "descricao": descricao,
                "timestamp": time.time()
            })
            # Limpar alertas expirados
            cutoff = time.time() - self._alert_display_ttl
            self._active_alerts = [a for a in self._active_alerts if a['timestamp'] > cutoff]

    # ══════════════════════════════════════════════
    #  Helpers de Demografia Legada
    # ══════════════════════════════════════════════

    def _predict_demographics(self, image_crop):
        """Usa DeepFace para estimar gênero e idade."""
        if image_crop.size == 0:
            return None, None
        try:
            from deepface import DeepFace
            results = DeepFace.analyze(
                img_path=image_crop,
                actions=['age', 'gender'],
                enforce_detection=False,
                silent=True
            )
            res = results[0] if isinstance(results, list) else results
            dom_g = res.get('dominant_gender')
            if dom_g == 'Man':
                gender = 'Male'
            elif dom_g == 'Woman':
                gender = 'Female'
            else:
                gender = 'Unknown'
            deep_age = res.get('age', 0)
            if deep_age <= 12:
                age_cat = '(8-12)'
            elif deep_age <= 32:
                age_cat = '(25-32)'
            elif deep_age <= 53:
                age_cat = '(38-43)'
            else:
                age_cat = '(60-100)'
            return gender, age_cat
        except Exception:
            return None, None

    @staticmethod
    def _mode(lst):
        """Retorna o elemento mais frequente de uma lista."""
        if not lst:
            return "Unknown"
        return max(set(lst), key=lst.count)

    # ══════════════════════════════════════════════
    #  Processamento de Frame
    # ══════════════════════════════════════════════

    def process_frame(self, frame, frame_count=0):
        """Pipeline completo: YOLO → Track → Face/ALPR async → Overlay."""
        if getattr(self, 'mirror_camera', False):
            frame = cv2.flip(frame, 1)

        h, w = frame.shape[:2]

        if self.line_x is None:
            self.line_x = w // 2

        clean_frame = frame.copy()

        # ── YOLO + ByteTrack (pessoas + veículos) ──
        results = self.yolo.track(
            frame, persist=True,
            classes=[0, 2, 3, 5, 7],  # pessoa + car + motorcycle + bus + truck
            verbose=False, tracker="bytetrack.yaml",
            conf=0.4, iou=0.5, imgsz=480
        )

        person_boxes = []
        vehicle_boxes = []

        if results and results[0].boxes and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            ids = results[0].boxes.id.int().cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            classes = results[0].boxes.cls.int().cpu().numpy()

            for box, tid, conf, cls in zip(boxes, ids, confs, classes):
                x1, y1, x2, y2 = map(int, box)
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                if cls == 0:
                    # ═══ PESSOA ═══
                    person_boxes.append((box, tid, conf))

                    if tid not in self.tracked_objects:
                        self.tracked_objects[tid] = {'positions': [], 'counted': False}
                    if tid not in self.demographics:
                        self.demographics[tid] = {'genders': [], 'ages': [], 'final_g': None, 'final_a': None}

                    self.tracked_objects[tid]['positions'].append(cx)
                    demo = self.demographics[tid]

                    # ── Análise demográfica (legado + novo) ──
                    if self.has_demographics and demo['final_g'] is None and len(demo['genders']) < 3 and (frame_count + tid) % 10 == 0:
                        box_h = y2 - y1
                        crop_y2 = y1 + int(box_h * 0.55)
                        upper_body = clean_frame[max(0, y1):min(h, crop_y2), max(0, x1):min(w, x2)]

                        if upper_body.size > 0:
                            gender, age = self._predict_demographics(upper_body)
                            if gender and age:
                                demo['genders'].append(gender)
                                demo['ages'].append(age)

                            # ── Submeter para análise facial avançada (embedding + matching) ──
                            with self._async_results_lock:
                                if tid not in self._face_in_progress:
                                    self._face_in_progress.add(tid)
                                    face_crop = upper_body.copy()
                                    future = self.face_executor.submit(
                                        self.face_processor.process_face, face_crop
                                    )
                                    future.add_done_callback(
                                        lambda f, t=tid: self._on_face_result(t, f)
                                    )

                    # ── Consolidar após 3+ amostras ──
                    if demo['final_g'] is None and len(demo['genders']) >= 3:
                        demo['final_g'] = self._mode(demo['genders'])
                        demo['final_a'] = self._mode(demo['ages'])

                    # ── Lógica da Linha Virtual ──
                    obj = self.tracked_objects[tid]
                    if not obj['counted'] and len(obj['positions']) >= 3:
                        first_x = obj['positions'][0]
                        evento = None

                        is_entry = (first_x < self.line_x and cx >= self.line_x)
                        is_exit = (first_x > self.line_x and cx <= self.line_x)

                        if getattr(self, 'swap_direction', False):
                            is_entry, is_exit = is_exit, is_entry

                        if is_entry:
                            evento = "entry"
                        elif is_exit:
                            evento = "exit"

                        if evento:
                            obj['counted'] = True
                            if demo['final_g'] is None and demo['genders']:
                                demo['final_g'] = self._mode(demo['genders'])
                                demo['final_a'] = self._mode(demo['ages'])

                            f_g = demo['final_g'] or "Unknown"
                            f_a = demo['final_a'] or "Unknown"
                            storage.log_event(evento, tid, f_g, f_a)
                            print(f"[EVENT] {evento.upper()} | ID:{tid} | {f_g} | {f_a}")

                    # ── Overlay de pessoa ──
                    is_counted = obj['counted']
                    color = (80, 200, 120) if is_counted else (200, 180, 60)

                    # Verificar se tem match facial pendente
                    face_match_info = None
                    with self._async_results_lock:
                        if tid in self._pending_face_results:
                            fr = self._pending_face_results[tid]
                            if fr.pessoa_id is not None:
                                face_match_info = fr
                                color = (0, 140, 255)  # Laranja para pessoa identificada

                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
                    # Cantos destacados
                    cl = 15
                    cv2.line(frame, (x1, y1), (x1 + cl, y1), color, 3)
                    cv2.line(frame, (x1, y1), (x1, y1 + cl), color, 3)
                    cv2.line(frame, (x2, y1), (x2 - cl, y1), color, 3)
                    cv2.line(frame, (x2, y1), (x2, y1 + cl), color, 3)
                    cv2.line(frame, (x1, y2), (x1 + cl, y2), color, 3)
                    cv2.line(frame, (x1, y2), (x1, y2 - cl), color, 3)
                    cv2.line(frame, (x2, y2), (x2 - cl, y2), color, 3)
                    cv2.line(frame, (x2, y2), (x2, y2 - cl), color, 3)
                    cv2.circle(frame, (cx, cy), 3, color, -1)

                    # Label
                    g_label = demo.get('final_g') or ("..." if demo['genders'] else "?")
                    a_label = demo.get('final_a') or ""
                    g_display = {"Male": "M", "Female": "F", "Unknown": "?"}.get(g_label, g_label)
                    label = f"#{tid} {g_display}"
                    if a_label:
                        label += f" {a_label}"
                    if face_match_info:
                        label = f"★ {face_match_info.nome}"

                    (tw, th_text), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                    cv2.rectangle(frame, (x1, y1 - th_text - 8), (x1 + tw + 8, y1), color, -1)
                    cv2.putText(frame, label, (x1 + 4, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1, cv2.LINE_AA)

                else:
                    # ═══ VEÍCULO ═══
                    vehicle_boxes.append(box)
                    cls_name = self.VEHICLE_CLASSES.get(int(cls), "veic")

                    # Cor do veículo: azul claro
                    v_color = (200, 160, 50)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), v_color, 1)
                    # Cantos
                    cl = 12
                    cv2.line(frame, (x1, y1), (x1 + cl, y1), v_color, 2)
                    cv2.line(frame, (x1, y1), (x1, y1 + cl), v_color, 2)
                    cv2.line(frame, (x2, y2), (x2 - cl, y2), v_color, 2)
                    cv2.line(frame, (x2, y2), (x2, y2 - cl), v_color, 2)

                    v_label = f"{cls_name} #{tid}"

                    # Verificar se tem placa pendente para este bbox
                    with self._async_results_lock:
                        for pr in self._pending_plate_results:
                            # Verificar overlap com este veículo
                            if (pr.bbox and len(pr.bbox) == 4 and
                                    x1 <= pr.bbox[0] and pr.bbox[2] <= x2):
                                v_label = f"{cls_name} {pr.placa}"
                                v_color = (0, 200, 255)  # Amarelo para placa detectada
                                break

                    (tw, th_text), _ = cv2.getTextSize(v_label, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                    cv2.rectangle(frame, (x1, y2), (x1 + tw + 8, y2 + th_text + 8), v_color, -1)
                    cv2.putText(frame, v_label, (x1 + 4, y2 + th_text + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)

        # ── Submeter veículos para ALPR (throttled) ──
        current_time = time.time()
        if vehicle_boxes and not self._alpr_in_progress and (current_time - self._last_alpr_time > 2.0):
            with self._async_results_lock:
                self._alpr_in_progress = True
                self._last_alpr_time = current_time
            frame_copy = clean_frame.copy()
            vb_copy = [b.copy() for b in vehicle_boxes]
            future = self.alpr_executor.submit(
                self.alpr_processor.detect_plates, frame_copy, vb_copy
            )
            future.add_done_callback(self._on_alpr_result)

        # ── Desenhar linha virtual ──
        overlay = frame.copy()
        cv2.rectangle(overlay, (self.line_x - 1, 0), (self.line_x + 1, h), (0, 220, 220), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        # Setas de direção
        arrow_x_right = self.line_x + 20
        arrow_x_left = self.line_x - 20
        swap = getattr(self, 'swap_direction', False)

        label_right = "SAI" if swap else "ENTRA"
        color_right = (100, 130, 255) if swap else (80, 200, 120)
        cv2.arrowedLine(frame, (arrow_x_right, 30), (arrow_x_right + 18, 30), color_right, 1, tipLength=0.4)
        cv2.putText(frame, label_right, (arrow_x_right + 22, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color_right, 1, cv2.LINE_AA)

        label_left = "ENTRA" if swap else "SAI"
        color_left = (80, 200, 120) if swap else (100, 130, 255)
        cv2.arrowedLine(frame, (arrow_x_left, 30), (arrow_x_left - 18, 30), color_left, 1, tipLength=0.4)
        cv2.putText(frame, label_left, (arrow_x_left - 50, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color_left, 1, cv2.LINE_AA)

        # ── Painel de status ──
        stats = storage.get_current_stats()
        modo = self.security_engine.get_modo().value.upper()
        gate = self.security_engine.get_gate_status()

        info_lines = [
            f"Entradas: {stats['entries']}",
            f"Saidas:   {stats['exits']}",
            f"Presentes: {stats['current']}",
            f"Modo: {modo}",
        ]
        if modo == "EVENTO":
            gate_label = "ABERTA" if gate == "aberta" else "BLOQUEADA"
            info_lines.append(f"Cancela: {gate_label}")

        for i, line in enumerate(info_lines):
            y_pos = 22 + i * 18
            cv2.putText(frame, line, (w - 170, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

        # ── Banner de alertas ativos ──
        with self._async_results_lock:
            cutoff = time.time() - self._alert_display_ttl
            active_alerts = [a for a in self._active_alerts if a['timestamp'] > cutoff]

        for i, alert in enumerate(active_alerts[-3:]):  # Máximo 3 alertas na tela
            nivel = alert['nivel']
            desc = alert['descricao'][:80]  # Truncar

            if nivel == 'vermelho':
                bg_color = (0, 0, 200)
                text_color = (255, 255, 255)
            elif nivel == 'amarelo':
                bg_color = (0, 180, 255)
                text_color = (0, 0, 0)
            else:
                bg_color = (0, 180, 0)
                text_color = (255, 255, 255)

            banner_y = h - 50 - (i * 35)
            cv2.rectangle(frame, (10, banner_y - 22), (w - 10, banner_y + 5), bg_color, -1)
            cv2.putText(frame, f"  {desc}", (15, banner_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, text_color, 1, cv2.LINE_AA)

        return frame

    # ══════════════════════════════════════════════
    #  Loop de Captura
    # ══════════════════════════════════════════════

    def update_camera(self, new_camera_id):
        """Reinicia o loop de captura com uma nova câmera."""
        if self.camera_id == new_camera_id:
            return
        print(f"[VISION] Trocando câmera de {self.camera_id} para {new_camera_id}...")
        self.camera_id = new_camera_id
        if self.running:
            self.running = False
            if self.thread:
                self.thread.join(timeout=3)
            # Limpa o último frame para não piscar
            with self.frame_lock:
                self.latest_frame = None
            self.start()

    def capture_loop(self):
        self.running = True
        cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print("[VISION] Fallback para backend padrão de captura...")
            cap = cv2.VideoCapture(self.camera_id)

        if not cap.isOpened():
            print(f"[VISION] ERRO: Câmera {self.camera_id} indisponível.")
            self.running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        # Carregar faces cadastradas do banco
        try:
            faces_data = storage.get_all_pessoas_with_embeddings()
            self.face_processor.load_known_faces(faces_data)
        except Exception as e:
            print(f"[VISION] Aviso ao carregar faces do banco: {e}")

        print("[VISION] Pipeline de visão computacional iniciado!")
        print(f"[VISION] Modo: {self.security_engine.get_modo().value}")
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

            elapsed = time.time() - t_start
            if elapsed >= 1.0:
                self.fps = frame_count / elapsed
                frame_count = 0
                t_start = time.time()

            # FPS no canto
            cv2.putText(out_frame, f"{self.fps:.0f} FPS", (out_frame.shape[1] - 80, out_frame.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1, cv2.LINE_AA)

            # Codificar e distribuir
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
        self.face_executor.shutdown(wait=False)
        self.alpr_executor.shutdown(wait=False)
