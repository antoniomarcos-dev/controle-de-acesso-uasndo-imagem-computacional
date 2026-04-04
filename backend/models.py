"""
Modelos Pydantic para a API do Sistema de Segurança Integrada.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ══════════════════════════════════════════════
#  Enums
# ══════════════════════════════════════════════

class ModoOperacao(str, Enum):
    CIDADE = "cidade"
    EVENTO = "evento"


class NivelAlerta(str, Enum):
    VERDE = "verde"
    AMARELO = "amarelo"
    VERMELHO = "vermelho"


class TipoAlerta(str, Enum):
    MANDADO_PRISAO = "mandado_prisao"
    VEICULO_ROUBADO = "veiculo_roubado"
    IPVA_ATRASADO = "ipva_atrasado"
    LICENCIAMENTO = "licenciamento"


class AcaoTomada(str, Enum):
    LIBERADO = "liberado"
    BLOQUEADO = "bloqueado"
    APENAS_REGISTRO = "apenas_registro"


# ══════════════════════════════════════════════
#  Payloads de Entrada (Request)
# ══════════════════════════════════════════════

class ConfigPayload(BaseModel):
    mirror_camera: bool
    swap_direction: bool
    face_conf_threshold: float
    camera_source: Optional[int] = None


class ModoPayload(BaseModel):
    modo: ModoOperacao


class PessoaCreate(BaseModel):
    nome: str = Field(..., min_length=2, max_length=255)
    cpf: Optional[str] = Field(None, max_length=14)
    genero: Optional[str] = None
    idade_estimada: Optional[int] = None
    status_judicial: str = "limpo"
    tem_mandado: bool = False
    foto_referencia_path: Optional[str] = None


class VeiculoCreate(BaseModel):
    placa: str = Field(..., min_length=7, max_length=10)
    modelo: Optional[str] = None
    cor: Optional[str] = None
    proprietario_id: Optional[int] = None
    pendencias_detran: Optional[str] = None
    ipva_atrasado: bool = False
    status_roubo: bool = False
    licenciamento_atrasado: bool = False


class GateOverride(BaseModel):
    acao: AcaoTomada
    motivo: Optional[str] = "Override manual do operador"


# ══════════════════════════════════════════════
#  Payloads de Saída (Response)
# ══════════════════════════════════════════════

class PessoaResponse(BaseModel):
    id: int
    nome: str
    cpf: Optional[str]
    genero: Optional[str]
    idade_estimada: Optional[int]
    status_judicial: str
    tem_mandado: bool
    ultima_aparicao: Optional[datetime]

    class Config:
        from_attributes = True


class VeiculoResponse(BaseModel):
    id: int
    placa: str
    modelo: Optional[str]
    cor: Optional[str]
    proprietario_id: Optional[int]
    ipva_atrasado: bool
    status_roubo: bool
    licenciamento_atrasado: bool

    class Config:
        from_attributes = True


class AlertaResponse(BaseModel):
    id: int
    tipo_alerta: str
    nivel: str
    descricao: Optional[str]
    timestamp: datetime
    pessoa_id: Optional[int]
    veiculo_id: Optional[int]
    resolvido: bool

    class Config:
        from_attributes = True


class RegistroAcessoResponse(BaseModel):
    id: int
    tipo: str
    timestamp: datetime
    localizacao: Optional[str]
    modo_operacao: Optional[str]
    pessoa_id: Optional[int]
    veiculo_id: Optional[int]
    placa_detectada: Optional[str]
    nivel_alerta: str
    detalhes_alerta: Optional[str]
    acao_tomada: Optional[str]

    class Config:
        from_attributes = True


class AlertResult(BaseModel):
    """Resultado de uma verificação de pendências."""
    nivel: NivelAlerta = NivelAlerta.VERDE
    tipo: Optional[TipoAlerta] = None
    descricao: str = "Nenhuma pendência encontrada"
    pessoa_id: Optional[int] = None
    veiculo_id: Optional[int] = None
    pessoa_nome: Optional[str] = None
    placa: Optional[str] = None


class PlateResult(BaseModel):
    """Resultado de leitura de uma placa."""
    placa: str
    confianca: float
    bbox: List[int] = []  # [x1, y1, x2, y2] da placa no frame


class FaceResult(BaseModel):
    """Resultado de processamento facial."""
    pessoa_id: Optional[int] = None
    nome: Optional[str] = None
    confianca: float = 0.0
    genero: Optional[str] = None
    idade: Optional[int] = None
    idade_categoria: Optional[str] = None
    embedding: Optional[List[float]] = None

    class Config:
        # Embedding pode ser grande, não serializar por padrão
        json_schema_extra = {
            "exclude": {"embedding"}
        }
