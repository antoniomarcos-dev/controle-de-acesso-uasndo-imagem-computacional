"""
Módulo ALPR — Automatic License Plate Recognition.

Pipeline:
1. Recebe frame + bounding boxes de veículos (do YOLO principal)
2. Extrai região inferior de cada veículo (onde a placa normalmente está)
3. Aplica pré-processamento para melhorar contraste
4. EasyOCR lê o texto
5. Regex valida formato Mercosul (ABC1D23 / ABC1234)
"""

import cv2
import numpy as np
import re
import threading
import time

try:
    from backend.models import PlateResult
except ImportError:
    from .models import PlateResult


class ALPRProcessor:
    """Motor de reconhecimento automático de placas veiculares."""

    # Padrões de placa brasileira
    PLATE_MERCOSUL = re.compile(r'^[A-Z]{3}\d[A-Z0-9]\d{2}$')   # ABC1D23
    PLATE_ANTIGA = re.compile(r'^[A-Z]{3}\d{4}$')                 # ABC1234

    def __init__(self, ocr_threshold=0.45):
        self.ocr_threshold = ocr_threshold
        self.reader = None
        self._init_lock = threading.Lock()
        self._initialized = False

        # Cache de placas recentes para evitar leituras duplicadas
        self._plate_cache = {}        # placa -> timestamp da última detecção
        self._cache_ttl = 30          # Segundos para considerar placa "recente"

        # Últimas placas detectadas (para exibir no dashboard)
        self.last_plates = []         # [{placa, confianca, timestamp}, ...]
        self.plates_lock = threading.Lock()

    def _lazy_init(self):
        """Inicializa o EasyOCR sob demanda (demora ~5s no primeiro uso)."""
        if self._initialized:
            return True
        with self._init_lock:
            if self._initialized:
                return True
            try:
                import easyocr
                print("[ALPR] Inicializando EasyOCR (primeira execução pode demorar)...")
                self.reader = easyocr.Reader(
                    ['pt', 'en'],
                    gpu=False,
                    verbose=False
                )
                self._initialized = True
                print("[ALPR] EasyOCR pronto!")
                return True
            except ImportError:
                print("[ALPR] AVISO: EasyOCR não instalado. Execute: pip install easyocr")
                return False
            except Exception as e:
                print(f"[ALPR] ERRO ao inicializar EasyOCR: {e}")
                return False

    def detect_plates(self, frame, vehicle_boxes):
        """
        Para cada bounding box de veículo, tenta extrair e ler a placa.

        Args:
            frame: Frame completo (numpy array BGR)
            vehicle_boxes: Lista de [x1, y1, x2, y2] de veículos detectados

        Returns:
            Lista de PlateResult com placas válidas detectadas
        """
        if not self._lazy_init() or self.reader is None:
            return []

        results = []
        current_time = time.time()

        for box in vehicle_boxes:
            x1, y1, x2, y2 = map(int, box)

            # ── Extrair região da placa (40% inferior do veículo) ──
            plate_region = self._extract_plate_region(frame, x1, y1, x2, y2)
            if plate_region is None or plate_region.size == 0:
                continue

            # ── Pré-processar para OCR ──
            processed = self._preprocess_for_ocr(plate_region)

            # ── Executar OCR ──
            try:
                ocr_results = self.reader.readtext(
                    processed,
                    detail=1,
                    paragraph=False,
                    allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
                    min_size=10,
                    width_ths=0.8
                )
            except Exception:
                continue

            # ── Processar resultados do OCR ──
            for (bbox_ocr, text, confidence) in ocr_results:
                if confidence < self.ocr_threshold:
                    continue

                # Limpar e validar
                cleaned = self._clean_plate_text(text)
                validated = self._validate_plate(cleaned)

                if validated:
                    # Verificar cache (evitar duplicatas)
                    if validated in self._plate_cache:
                        last_seen = self._plate_cache[validated]
                        if current_time - last_seen < self._cache_ttl:
                            continue

                    self._plate_cache[validated] = current_time

                    # Calcular posição absoluta da placa no frame
                    plate_h = y2 - y1
                    plate_y_offset = y1 + int(plate_h * 0.6)
                    abs_bbox = [x1, plate_y_offset, x2, y2]

                    result = PlateResult(
                        placa=validated,
                        confianca=round(confidence, 3),
                        bbox=abs_bbox
                    )
                    results.append(result)

                    # Atualizar lista de últimas placas para o dashboard
                    with self.plates_lock:
                        self.last_plates.append({
                            "placa": validated,
                            "confianca": round(confidence * 100, 1),
                            "timestamp": time.strftime("%H:%M:%S")
                        })
                        # Manter apenas as últimas 20
                        if len(self.last_plates) > 20:
                            self.last_plates = self.last_plates[-20:]

                    print(f"[ALPR] ✓ Placa detectada: {validated} (conf: {confidence:.1%})")

        # Limpar cache antigo periodicamente
        self._cleanup_cache(current_time)

        return results

    def _extract_plate_region(self, frame, x1, y1, x2, y2):
        """Extrai os 40% inferiores do bounding box do veículo (onde a placa normalmente está)."""
        h, w = frame.shape[:2]

        # Garantir coordenadas dentro do frame
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)

        box_h = y2 - y1
        box_w = x2 - x1

        if box_h < 30 or box_w < 50:
            return None

        # Pegar 40% inferior (região da placa)
        crop_y1 = y1 + int(box_h * 0.6)
        plate_crop = frame[crop_y1:y2, x1:x2]

        return plate_crop

    def _preprocess_for_ocr(self, region):
        """Pré-processamento do crop da placa para melhorar leitura do OCR."""
        # Redimensionar para tamanho mínimo
        h, w = region.shape[:2]
        if w < 200:
            scale = 200 / w
            region = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Converter para grayscale
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

        # Equalização de histograma adaptativa (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        enhanced = clahe.apply(gray)

        # Leve desfoque para suavizar ruído
        blurred = cv2.GaussianBlur(enhanced, (3, 3), 0)

        # Binarização adaptativa
        binary = cv2.adaptiveThreshold(
            blurred, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11, 2
        )

        return binary

    def _clean_plate_text(self, text):
        """Limpa o texto OCR removendo caracteres inválidos."""
        # Remover espaços, hífens, pontos
        cleaned = re.sub(r'[^A-Za-z0-9]', '', text.upper())
        return cleaned

    def _validate_plate(self, text):
        """Valida contra formatos de placa brasileira (Mercosul e antiga)."""
        if not text or len(text) < 7:
            return None

        # Tentar com os 7 primeiros caracteres
        candidate = text[:7]

        if self.PLATE_MERCOSUL.match(candidate):
            return candidate
        if self.PLATE_ANTIGA.match(candidate):
            return candidate

        return None

    def _cleanup_cache(self, current_time):
        """Remove entradas antigas do cache de placas."""
        expired = [k for k, v in self._plate_cache.items()
                   if current_time - v > self._cache_ttl * 3]
        for k in expired:
            del self._plate_cache[k]

    def get_last_plates(self):
        """Retorna últimas placas detectadas (para API do dashboard)."""
        with self.plates_lock:
            return list(self.last_plates)
