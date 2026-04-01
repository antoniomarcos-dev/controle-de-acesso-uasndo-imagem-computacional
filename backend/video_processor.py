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

        # ── Modelos Demográficos (OpenCV DNN) ─────────────────
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        models_dir = os.path.join(base_dir, 'models')
        self.has_demographics = False

        face_pb    = os.path.join(models_dir, "opencv_face_detector_uint8.pb")
        face_pbtxt = os.path.join(models_dir, "opencv_face_detector.pbtxt")
        age_model  = os.path.join(models_dir, "age_net.caffemodel")
        age_proto  = os.path.join(models_dir, "age_deploy.prototxt")
        gen_model  = os.path.join(models_dir, "gender_net.caffemodel")
        gen_proto  = os.path.join(models_dir, "gender_deploy.prototxt")

        needed = [face_pb, face_pbtxt, age_model, age_proto, gen_model, gen_proto]
        if all(os.path.exists(f) for f in needed):
            print("[SMART-COUNTER] Modelos demográficos encontrados. Carregando...")
            self.face_net   = cv2.dnn.readNet(face_pb, face_pbtxt)
            self.age_net    = cv2.dnn.readNet(age_model, age_proto)
            self.gender_net = cv2.dnn.readNet(gen_model, gen_proto)
            self.has_demographics = True
            print("[SMART-COUNTER] Modelos demográficos carregados com sucesso!")
        else:
            missing = [os.path.basename(f) for f in needed if not os.path.exists(f)]
            print(f"[SMART-COUNTER] AVISO: Modelos demográficos ausentes: {missing}")
            print("[SMART-COUNTER] Rode: python scripts/download_models.py")

        self.AGE_BUCKETS = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        self.GENDER_LABELS = ['Male', 'Female']
        self.MEAN_VALUES = (78.4263377603, 87.7689143744, 114.895847746)

    # ── Detecção de Rosto ────────────────────────────────────────
    def _detect_faces(self, image, conf=0.45):
        """Detecta rostos no recorte do corpo usando o SSD Face Detector."""
        h, w = image.shape[:2]
        if h < 20 or w < 20:
            return []

        blob = cv2.dnn.blobFromImage(image, 1.0, (300, 300), [104, 117, 123], swapRB=False, crop=False)
        self.face_net.setInput(blob)
        detections = self.face_net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence > conf:
                x1 = max(0, int(detections[0, 0, i, 3] * w) - 15)
                y1 = max(0, int(detections[0, 0, i, 4] * h) - 15)
                x2 = min(w, int(detections[0, 0, i, 5] * w) + 15)
                y2 = min(h, int(detections[0, 0, i, 6] * h) + 15)
                if (x2 - x1) > 10 and (y2 - y1) > 10:
                    faces.append((x1, y1, x2, y2, confidence))
        return faces

    # ── Predição de Idade e Gênero ───────────────────────────────
    def _predict_demographics(self, face_img):
        """Prediz gênero e faixa etária a partir de um crop facial."""
        if face_img.size == 0 or face_img.shape[0] < 10 or face_img.shape[1] < 10:
            return None, None

        try:
            blob = cv2.dnn.blobFromImage(face_img, 1.0, (227, 227), self.MEAN_VALUES, swapRB=False)

            self.gender_net.setInput(blob)
            g_preds = self.gender_net.forward()
            gender = self.GENDER_LABELS[g_preds[0].argmax()]

            self.age_net.setInput(blob)
            a_preds = self.age_net.forward()
            age = self.AGE_BUCKETS[a_preds[0].argmax()]

            return gender, age
        except Exception as e:
            print(f"[SMART-COUNTER] Erro na predição demográfica: {e}")
            return None, None

    # ── Modo Estatístico (Moda) ──────────────────────────────────
    @staticmethod
    def _mode(lst):
        """Retorna o elemento mais frequente de uma lista."""
        if not lst:
            return "Unknown"
        return max(set(lst), key=lst.count)

    # ── Processamento de Frame ───────────────────────────────────
    def process_frame(self, frame):
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

        # ── YOLO + BoT-SORT Tracking ─────────────────────────
        results = self.yolo.track(
            frame, persist=True, classes=[0],
            verbose=False, tracker="botsort.yaml",
            conf=0.4, iou=0.5
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

                # ── Estimar dados demográficos (até 8 tentativas, no frame LIMPO) ──
                if self.has_demographics and demo['final_g'] is None and len(demo['genders']) < 8:
                    box_h = y2 - y1
                    # Pega os 55% superiores do corpo (onde fica o rosto)
                    crop_y2 = y1 + int(box_h * 0.55)
                    upper_body = clean_frame[max(0, y1):min(h, crop_y2), max(0, x1):min(w, x2)]

                    if upper_body.size > 0:
                        conf_thresh = getattr(self, 'face_conf_threshold', 0.35)
                        faces = self._detect_faces(upper_body, conf=conf_thresh)
                        if faces:
                            # Pega o rosto com maior confiança
                            best = max(faces, key=lambda f: f[4])
                            fx1, fy1, fx2, fy2, _ = best
                            face_crop = upper_body[fy1:fy2, fx1:fx2]
                            gender, age = self._predict_demographics(face_crop)
                            if gender and age:
                                demo['genders'].append(gender)
                                demo['ages'].append(age)

                # ── Consolidar após 3+ amostras ──
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

        # Configurar resolução adequada (720p para balancear qualidade/performance)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        print("[SMART-COUNTER] Pipeline de visão computacional iniciado!")
        frame_count = 0
        t_start = time.time()

        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            out_frame = self.process_frame(frame)
            frame_count += 1

            # Calcular FPS real
            elapsed = time.time() - t_start
            if elapsed >= 1.0:
                self.fps = frame_count / elapsed
                frame_count = 0
                t_start = time.time()

            # FPS no canto
            cv2.putText(out_frame, f"{self.fps:.0f} FPS", (out_frame.shape[1] - 80, out_frame.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1, cv2.LINE_AA)

            cv2.imshow("Smart Counter AI - Camera View", out_frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                break

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
