"""
Módulo de Análise Facial Avançada.

Pipeline:
1. Recebe crop de rosto (ou corpo superior)
2. Gera embedding facial 512D via ArcFace/DeepFace
3. Estima idade e gênero
4. Compara embedding contra banco de dados de pessoas cadastradas
5. Retorna identidade (se houver match) + dados demográficos
"""

import numpy as np
import threading
import time

try:
    from backend.models import FaceResult
except ImportError:
    from .models import FaceResult


class FaceProcessor:
    """Motor de reconhecimento e análise facial avançada."""

    AGE_BUCKETS = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']

    def __init__(self, match_threshold=0.68):
        self.match_threshold = match_threshold
        self._initialized = False
        self._init_lock = threading.Lock()

        # Cache de embeddings conhecidos (carregados do DB)
        self.known_faces = {}       # pessoa_id -> {'nome': str, 'embedding': np.array}
        self.known_lock = threading.Lock()

        # Últimos matches para exibir no dashboard
        self.last_matches = []      # [{pessoa_id, nome, confianca, timestamp}, ...]
        self.matches_lock = threading.Lock()

    def _lazy_init(self):
        """Inicializa DeepFace sob demanda."""
        if self._initialized:
            return True
        with self._init_lock:
            if self._initialized:
                return True
            try:
                from deepface import DeepFace
                # Pré-aquecer modelos carregando uma análise dummy
                print("[FACE] Inicializando DeepFace (ArcFace + análise demográfica)...")
                dummy = np.zeros((100, 100, 3), dtype=np.uint8)
                try:
                    DeepFace.represent(
                        img_path=dummy,
                        model_name='ArcFace',
                        enforce_detection=False,
                        detector_backend='skip'
                    )
                except Exception:
                    pass  # Esperado com imagem dummy
                self._initialized = True
                print("[FACE] DeepFace pronto!")
                return True
            except ImportError:
                print("[FACE] AVISO: DeepFace não instalado. Execute: pip install deepface tf-keras")
                return False
            except Exception as e:
                print(f"[FACE] ERRO ao inicializar DeepFace: {e}")
                return False

    def process_face(self, face_crop):
        """
        Processa um crop facial: gera embedding + estima dados demográficos.

        Args:
            face_crop: Imagem BGR do rosto (numpy array)

        Returns:
            FaceResult com embedding, idade, gênero e match (se encontrado)
        """
        if face_crop is None or face_crop.size == 0:
            return None

        if not self._lazy_init():
            return None

        try:
            from deepface import DeepFace

            # ── 1. Gerar embedding (ArcFace) ──
            embedding = None
            try:
                embed_result = DeepFace.represent(
                    img_path=face_crop,
                    model_name='ArcFace',
                    enforce_detection=False,
                    detector_backend='skip'
                )
                if embed_result and len(embed_result) > 0:
                    embedding = embed_result[0].get('embedding')
            except Exception:
                pass

            # ── 2. Análise demográfica (idade + gênero) ──
            genero = None
            idade = None
            idade_cat = None
            try:
                analysis = DeepFace.analyze(
                    img_path=face_crop,
                    actions=['age', 'gender'],
                    enforce_detection=False,
                    silent=True
                )
                res = analysis[0] if isinstance(analysis, list) else analysis

                # Gênero
                dom_g = res.get('dominant_gender')
                if dom_g == 'Man':
                    genero = 'Male'
                elif dom_g == 'Woman':
                    genero = 'Female'
                else:
                    genero = 'Unknown'

                # Idade
                idade = res.get('age', 0)
                idade_cat = self._classify_age(idade)
            except Exception:
                pass

            # ── 3. Buscar match no banco cadastrado ──
            pessoa_id = None
            pessoa_nome = None
            confianca = 0.0

            if embedding is not None:
                pessoa_id, pessoa_nome, confianca = self.find_match(embedding)

                if pessoa_id is not None:
                    print(f"[FACE] ★ MATCH: {pessoa_nome} (ID:{pessoa_id}, conf:{confianca:.1%})")
                    with self.matches_lock:
                        self.last_matches.append({
                            "pessoa_id": pessoa_id,
                            "nome": pessoa_nome,
                            "confianca": round(confianca * 100, 1),
                            "timestamp": time.strftime("%H:%M:%S")
                        })
                        if len(self.last_matches) > 20:
                            self.last_matches = self.last_matches[-20:]

            return FaceResult(
                pessoa_id=pessoa_id,
                nome=pessoa_nome,
                confianca=round(confianca, 4),
                genero=genero,
                idade=idade,
                idade_categoria=idade_cat,
                embedding=embedding
            )

        except Exception as e:
            print(f"[FACE] Erro no processamento: {e}")
            return None

    def find_match(self, embedding, threshold=None):
        """
        Compara um embedding contra o banco de faces cadastradas.

        Args:
            embedding: Lista/array de floats (vetor 512D)
            threshold: Limiar de similaridade (menor = mais rígido)

        Returns:
            (pessoa_id, nome, confiança) ou (None, None, 0.0) se sem match
        """
        if threshold is None:
            threshold = self.match_threshold

        if embedding is None:
            return None, None, 0.0
        if isinstance(embedding, (list, tuple)) and len(embedding) == 0:
            return None, None, 0.0

        target = np.array(embedding)
        best_match_id = None
        best_match_nome = None
        best_distance = float('inf')

        with self.known_lock:
            for pid, data in self.known_faces.items():
                known_emb = data['embedding']
                if known_emb is None:
                    continue

                # Distância cosseno
                distance = self._cosine_distance(target, known_emb)
                if distance < best_distance:
                    best_distance = distance
                    best_match_id = pid
                    best_match_nome = data['nome']

        # ArcFace threshold típico: ~0.68 (distância cosseno)
        if best_distance < threshold and best_match_id is not None:
            confidence = max(0, 1.0 - best_distance)
            return best_match_id, best_match_nome, confidence

        return None, None, 0.0

    def load_known_faces(self, faces_data):
        """
        Carrega embeddings do banco de dados para cache em memória.

        Args:
            faces_data: Lista de dicts com {id, nome, embedding}
        """
        with self.known_lock:
            self.known_faces.clear()
            loaded = 0
            for face in faces_data:
                pid = face['id']
                emb = face.get('embedding')
                if emb is not None:
                    self.known_faces[pid] = {
                        'nome': face.get('nome', f'ID-{pid}'),
                        'embedding': np.array(emb)
                    }
                    loaded += 1
            print(f"[FACE] {loaded} faces cadastradas carregadas para reconhecimento")

    @staticmethod
    def _cosine_distance(a, b):
        """Calcula distância cosseno entre dois vetores."""
        a = np.array(a, dtype=np.float64)
        b = np.array(b, dtype=np.float64)
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 1.0
        similarity = dot / (norm_a * norm_b)
        return 1.0 - similarity

    @staticmethod
    def _classify_age(age_val):
        """Converte idade numérica para categoria."""
        if age_val is None:
            return 'Desconhecido'
        if age_val <= 12:
            return '(8-12)'
        elif age_val <= 32:
            return '(25-32)'
        elif age_val <= 53:
            return '(38-43)'
        else:
            return '(60-100)'

    def get_last_matches(self):
        """Retorna últimos matches faciais (para API do dashboard)."""
        with self.matches_lock:
            return list(self.last_matches)
