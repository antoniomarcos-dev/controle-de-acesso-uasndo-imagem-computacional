# Evolução: Smart Counter AI → Sistema de Segurança Integrada

## Visão Geral

Evolução do sistema atual de contagem inteligente de pessoas para uma plataforma robusta de segurança pública e privada, integrando:
- **ALPR** (Automatic License Plate Recognition) com YOLOv8 + EasyOCR
- **Reconhecimento Facial Avançado** com DeepFace (identidade via embeddings + idade/gênero)
- **Banco de Dados PostgreSQL** expandido (pessoas, veículos, alertas, registros de acesso)
- **Lógica de Negócio** com verificação de pendências judiciais e veiculares
- **Modos de Operação** (Cidade vs Evento) com cancela virtual

---

## User Review Required

> [!IMPORTANT]
> **Banco de Dados**: O schema proposto adiciona 4 novas tabelas ao PostgreSQL (`pessoas`, `veiculos`, `registros_acesso`, `alertas_justica`). A tabela `events` existente será **mantida** para compatibilidade, e os novos registros utilizarão `registros_acesso`. Confirme se deseja manter a tabela `events` legada ou migrar tudo.

> [!WARNING]
> **Performance**: Processar YOLO (pessoas) + YOLO (placas) + DeepFace (identidade) + EasyOCR simultaneamente é muito pesado. A arquitetura proposta usa `ThreadPoolExecutor` com filas assíncronas para isolar cada pipeline do loop de captura de vídeo. Mesmo assim, em CPU pura, espere ~5-8 FPS. Para produção, recomenda-se GPU (CUDA).

> [!IMPORTANT]
> **Modelo de placas**: O YOLOv8 genérico não detecta placas nativamente. Será necessário baixar um modelo YOLO treinado especificamente para placas Mercosul, ou treinar um. Inicialmente usaremos uma abordagem de detecção por região de interesse (ROI) nos veículos detectados pelo YOLO genérico (classes `car`, `truck`, `bus`, `motorcycle`), com crop + EasyOCR. Para produção, um modelo especializado é recomendado.

---

## Estrutura de Pastas Proposta

```
projeto/
├── .env                          # Credenciais do PostgreSQL
├── requirements.txt              # Dependências atualizadas
├── yolov8n.pt                    # Modelo YOLO existente
│
├── backend/
│   ├── __init__.py
│   ├── main.py                   # [MODIFY] FastAPI — novos endpoints + modos
│   ├── storage.py                # [MODIFY] PostgreSQL — novo schema expandido
│   ├── video_processor.py        # [MODIFY] Pipeline principal — orquestração async
│   ├── alpr_processor.py         # [NEW] Módulo ALPR (detecção + OCR de placas)
│   ├── face_processor.py         # [NEW] Módulo facial avançado (embedding + identidade)
│   ├── business_logic.py         # [NEW] Check de pendências + sistema de alertas
│   └── models.py                 # [NEW] Pydantic models para API
│
├── frontend/
│   ├── index.html                # [MODIFY] Dashboard expandido com painel de alertas
│   ├── css/style.css             # [MODIFY] Estilos para novos componentes
│   └── js/app.js                 # [MODIFY] Lógica para alertas + modo de operação
│
├── scripts/
│   ├── setup_postgres.py         # [MODIFY] Criar novo schema completo
│   └── download_models.py        # Existente
│
├── data/                         # Dados legados
│   └── faces/                    # [NEW] Fotos de referência para reconhecimento
│
└── models/                       # Modelos de IA
    └── (modelos Caffe existentes)
```

---

## Proposed Changes

### 1. Schema PostgreSQL Expandido

#### [MODIFY] [setup_postgres.py](file:///e:/Ceres%20Conecta%20Hub/projeto/scripts/setup_postgres.py)

O script será atualizado para criar o schema completo:

```sql
-- Tabela de Pessoas (cadastro + biometria)
CREATE TABLE IF NOT EXISTS pessoas (
    id SERIAL PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    cpf VARCHAR(14) UNIQUE,
    foto_hash VARCHAR(128),
    embedding FLOAT8[],           -- Vetor facial (512 dims)
    idade_estimada INTEGER,
    genero VARCHAR(20),
    status_judicial VARCHAR(50) DEFAULT 'limpo',
    tem_mandado BOOLEAN DEFAULT FALSE,
    foto_referencia_path TEXT,
    ultima_aparicao TIMESTAMP,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de Veículos
CREATE TABLE IF NOT EXISTS veiculos (
    id SERIAL PRIMARY KEY,
    placa VARCHAR(10) UNIQUE NOT NULL,
    modelo VARCHAR(100),
    cor VARCHAR(50),
    proprietario_id INTEGER REFERENCES pessoas(id),
    pendencias_detran TEXT,
    ipva_atrasado BOOLEAN DEFAULT FALSE,
    status_roubo BOOLEAN DEFAULT FALSE,
    licenciamento_atrasado BOOLEAN DEFAULT FALSE,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de Registros de Acesso (unifica pessoas + veículos)
CREATE TABLE IF NOT EXISTS registros_acesso (
    id SERIAL PRIMARY KEY,
    tipo VARCHAR(20) NOT NULL,        -- 'entrada' | 'saida' | 'passagem'
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    localizacao VARCHAR(255),
    modo_operacao VARCHAR(20),         -- 'cidade' | 'evento'
    pessoa_id INTEGER REFERENCES pessoas(id),
    veiculo_id INTEGER REFERENCES veiculos(id),
    nivel_alerta VARCHAR(20) DEFAULT 'verde',  -- 'verde' | 'amarelo' | 'vermelho'
    detalhes_alerta TEXT,
    confianca_facial FLOAT,
    placa_detectada VARCHAR(10),
    acao_tomada VARCHAR(50)             -- 'liberado' | 'bloqueado' | 'apenas_registro'
);

-- Tabela de Alertas Judiciais
CREATE TABLE IF NOT EXISTS alertas_justica (
    id SERIAL PRIMARY KEY,
    pessoa_id INTEGER REFERENCES pessoas(id),
    veiculo_id INTEGER REFERENCES veiculos(id),
    tipo_alerta VARCHAR(50) NOT NULL,  -- 'mandado_prisao' | 'veiculo_roubado' | 'ipva_atrasado' | 'licenciamento'
    nivel VARCHAR(20) NOT NULL,        -- 'vermelho' | 'amarelo'
    descricao TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolvido BOOLEAN DEFAULT FALSE,
    registrado_por VARCHAR(100) DEFAULT 'sistema_automatico'
);

-- Índices de Performance
CREATE INDEX IF NOT EXISTS idx_pessoas_cpf ON pessoas(cpf);
CREATE INDEX IF NOT EXISTS idx_pessoas_embedding ON pessoas USING gin(embedding);
CREATE INDEX IF NOT EXISTS idx_veiculos_placa ON veiculos(placa);
CREATE INDEX IF NOT EXISTS idx_registros_timestamp ON registros_acesso(timestamp);
CREATE INDEX IF NOT EXISTS idx_alertas_nivel ON alertas_justica(nivel);
CREATE INDEX IF NOT EXISTS idx_alertas_resolvido ON alertas_justica(resolvido);
```

---

### 2. Módulo ALPR (Reconhecimento de Placas)

#### [NEW] [alpr_processor.py](file:///e:/Ceres%20Conecta%20Hub/projeto/backend/alpr_processor.py)

Pipeline de leitura de placas:

1. **Detecção de Veículos**: YOLOv8 detecta classes `car(2)`, `motorcycle(3)`, `bus(5)`, `truck(7)`
2. **Extração de Região**: Crop da parte inferior do veículo (onde a placa normalmente está)
3. **OCR**: EasyOCR lê o texto da placa no crop
4. **Validação**: Regex valida formato Mercosul (`ABC1D23` / `ABC1234`)
5. **Consulta**: Busca no PostgreSQL por pendências

```python
class ALPRProcessor:
    def __init__(self):
        self.ocr_reader = easyocr.Reader(['pt', 'en'], gpu=False)
        self.plate_pattern = re.compile(r'^[A-Z]{3}\d[A-Z0-9]\d{2}$')
    
    def detect_plates(self, frame, vehicle_boxes) -> list[PlateResult]:
        """Para cada veículo detectado, extrai e lê a placa."""
        ...
    
    def _extract_plate_region(self, frame, box) -> np.ndarray:
        """Crop inteligente da região inferior do bbox do veículo."""
        ...
    
    def _validate_plate(self, text: str) -> str | None:
        """Valida contra regex do formato Mercosul."""
        ...
```

---

### 3. Módulo Facial Avançado

#### [NEW] [face_processor.py](file:///e:/Ceres%20Conecta%20Hub/projeto/backend/face_processor.py)

Pipeline de reconhecimento facial:

1. **Detecção**: Localiza rostos no frame (via DeepFace/RetinaFace)
2. **Embedding**: Gera vetor 512D para cada rosto (ArcFace via DeepFace)
3. **Matching**: Compara embedding contra banco de dados cadastrado
4. **Demografia**: Estima idade e gênero (mantém funcionalidade atual)

```python
class FaceProcessor:
    def __init__(self):
        self.model_name = 'ArcFace'  # Motor de embedding
        self.known_embeddings = {}   # Cache local: pessoa_id -> embedding
    
    def process_face(self, face_crop) -> FaceResult:
        """Gera embedding + estima idade/gênero."""
        ...
    
    def find_match(self, embedding, threshold=0.68) -> tuple[int|None, float]:
        """Compara contra embeddings do banco. Retorna (pessoa_id, confiança)."""
        ...
    
    def load_known_faces_from_db(self):
        """Carrega embeddings do PostgreSQL para cache em memória."""
        ...
```

---

### 4. Lógica de Negócio e Alertas

#### [NEW] [business_logic.py](file:///e:/Ceres%20Conecta%20Hub/projeto/backend/business_logic.py)

Motor de decisões do sistema:

```python
class SecurityEngine:
    MODO_CIDADE = 'cidade'      # Apenas registra passagem + alerta
    MODO_EVENTO = 'evento'      # Libera/bloqueia cancela virtual
    
    def __init__(self, db_connection_factory):
        self.modo_atual = self.MODO_CIDADE
    
    def check_pessoa(self, pessoa_id: int) -> AlertResult:
        """
        Verifica pendências judiciais.
        SELECT tem_mandado, status_judicial FROM pessoas WHERE id = %s
        → Alerta Vermelho se tem_mandado = TRUE
        → Log Verde se limpo
        """
        ...
    
    def check_veiculo(self, placa: str) -> AlertResult:
        """
        Verifica restrições veiculares.
        SELECT status_roubo, ipva_atrasado, licenciamento_atrasado FROM veiculos WHERE placa = %s
        → Alerta Vermelho se status_roubo = TRUE
        → Alerta Amarelo se ipva/licenciamento atrasados
        → Log Verde se limpo
        """
        ...
    
    def decidir_acao(self, alert_pessoa, alert_veiculo) -> str:
        """
        Modo Cidade: Registra + emite log de alerta.
        Modo Evento: Retorna 'liberado'/'bloqueado' baseado nos alertas.
        """
        ...
    
    def emitir_alerta(self, nivel, descricao, pessoa_id=None, veiculo_id=None):
        """Persiste alerta no banco e emite log colorido no console."""
        ...
```

**Níveis de Alerta:**

| Nível | Cor | Condição | Ação |
|-------|-----|----------|------|
| 🔴 Vermelho | `#ef4444` | Mandado de prisão ativo OU veículo roubado | Alerta imediato + bloqueia (modo evento) |
| 🟡 Amarelo | `#f59e0b` | IPVA atrasado OU licenciamento vencido | Alerta moderado + registra |
| 🟢 Verde | `#22c55e` | Nenhuma pendência | Entrada liberdada |

---

### 5. Orquestração Assíncrona (Video Processor)

#### [MODIFY] [video_processor.py](file:///e:/Ceres%20Conecta%20Hub/projeto/backend/video_processor.py)

O processador de vídeo será refatorado para usar **ThreadPoolExecutor** com filas:

```
                    ┌──────────────────────┐
                    │   Captura de Vídeo   │
                    │   (Thread Principal) │
                    └──────────┬───────────┘
                               │ frame
                    ┌──────────▼───────────┐
                    │   YOLO Detection     │
                    │  (pessoas + veículos)│
                    └──────────┬───────────┘
                               │ boxes
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
    ┌─────────────────┐ ┌───────────────┐ ┌────────────────┐
    │  Face Pipeline  │ │ ALPR Pipeline │ │  Drawing/Overlay│
    │  (Thread Pool)  │ │ (Thread Pool) │ │  (Main Thread)  │
    │                 │ │               │ │                 │
    │ DeepFace embed  │ │ Crop + OCR    │ │ Boxes + Labels  │
    │ Age + Gender    │ │ Validate      │ │ Alert Banners   │
    │ DB Match        │ │ DB Lookup     │ │ Status HUD      │
    └────────┬────────┘ └──────┬────────┘ └────────┬────────┘
             │                  │                    │
             └──────────┬───────┘                    │
                        ▼                            │
              ┌─────────────────┐                    │
              │ Security Engine │                    │
              │ Check Pendências│                    │
              │ Decidir Ação    │                    │
              │ Emitir Alerta   │                    │
              └────────┬────────┘                    │
                       │                             │
                       ▼                             ▼
              ┌─────────────────┐         ┌──────────────────┐
              │   PostgreSQL    │         │  Frame Encoding  │
              │  (async write)  │         │  MJPEG Stream    │
              └─────────────────┘         └──────────────────┘
```

A chave de performance é: **o loop de captura NUNCA espera** o resultado de Face/ALPR. Os resultados chegam via callback e são sobrepostos no próximo frame disponível.

---

### 6. Novos Endpoints da API

#### [MODIFY] [main.py](file:///e:/Ceres%20Conecta%20Hub/projeto/backend/main.py)

Novos endpoints:

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/api/stats` | Stats existentes + alertas recentes |
| `GET` | `/api/alerts` | Lista de alertas ativos (vermelho/amarelo) |
| `GET` | `/api/alerts/history` | Histórico de alertas com paginação |
| `POST` | `/api/mode` | Alterna entre modo Cidade ↔ Evento |
| `GET` | `/api/mode` | Retorna modo atual |
| `POST` | `/api/pessoas` | Cadastra pessoa (nome, CPF, foto, status) |
| `GET` | `/api/pessoas` | Lista pessoas cadastradas |
| `POST` | `/api/veiculos` | Cadastra veículo (placa, modelo, status) |
| `GET` | `/api/veiculos` | Lista veículos cadastrados |
| `GET` | `/api/registros` | Registros de acesso com filtros |
| `POST` | `/api/gate/override` | Override manual da cancela (modo evento) |

---

### 7. Dashboard Expandido

#### [MODIFY] [index.html](file:///e:/Ceres%20Conecta%20Hub/projeto/frontend/index.html)

Novos componentes visuais:

1. **Painel de Alertas em Tempo Real**: Banner de alerta colorido (vermelho/amarelo/verde) que aparece quando uma pendência é detectada, com informações da pessoa/veículo.

2. **Indicador de Modo**: Toggle visual entre "Modo Cidade" (ícone de cidade) e "Modo Evento" (ícone de cancela) com cores diferentes.

3. **Seção ALPR**: Card dedicado mostrando última placa detectada, histórico de placas, e indicador de status.

4. **Tabela de Alertas**: Lista rolável de alertas recentes com nível, descrição, timestamp e ação tomada.

5. **Indicador de Cancela** (modo evento): Representação visual da cancela virtual — verde (aberta) ou vermelho (bloqueada).

---

## Open Questions

> [!IMPORTANT]
> **1. Dados de Teste**: Devo criar um script que popula o banco com dados fictícios de pessoas/veículos com pendências, para testar o fluxo completo? (Recomendo fortemente que sim.)

> [!IMPORTANT]
> **2. Tabela `events` legada**: Manter em paralelo com `registros_acesso` ou migrar e remover?

> [!WARNING]
> **3. GPU/CUDA**: O sistema terá acesso a GPU NVIDIA para aceleração? Isso impacta drasticamente a escolha de configuração do EasyOCR e DeepFace. Em CPU pura, cada análise facial leva ~500ms e o OCR ~300ms.

> [!NOTE]
> **4. Face Recognition Dataset**: Você já possui fotos de referência de pessoas (procurados/cadastrados)? Ou devemos implementar um fluxo de cadastro via dashboard?

> [!NOTE]
> **5. Modelo YOLO para Placas**: Usar o YOLOv8 genérico com crop de veículo + EasyOCR (implementação imediata, 70-80% acurácia) ou investir em baixar/treinar um modelo YOLOv8 específico para placas Mercosul (90%+ acurácia, requer dataset)?

---

## Verification Plan

### Automated Tests
1. **Script de seed de dados**: Popular banco com 10 pessoas (3 com mandado) e 10 veículos (2 roubados, 3 IPVA atrasado)
2. **Test com imagem estática**: Alimentar o pipeline com frames de teste contendo rostos e placas conhecidas
3. **Verificar integridade SQL**: Garantir que todas as tabelas, índices e constraints estão corretos
4. **Verificar logs de alerta**: Testar que cada nível de alerta (verde/amarelo/vermelho) produz o output correto

### Manual Verification
1. **Feed ao vivo**: Validar que o streaming MJPEG continua fluído com os módulos paralelos ativos
2. **Dashboard**: Verificar renderização dos novos componentes (painel de alertas, indicador de modo, seção ALPR)
3. **Troca de modo**: Validar comportamento diferente entre Cidade e Evento via frontend
4. **Detecção de placa**: Testar com imagem/vídeo de veículo com placa visível
