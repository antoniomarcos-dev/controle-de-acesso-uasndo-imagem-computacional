"""
Módulo para integração com APIs externas de consulta de veículos.

Lida com a comunicação com APIs como APIBrasil, Sinesp (via Wrappers), PlacaAPI, etc.
Por padrão, este módulo contém um cliente genérico que deve ser abstraído via variáveis
de ambiente no arquivo .env. Se a API estiver offline ou sem credenciais, ele retorna
falha silenciosamente e o fluxo segue com o banco local.
"""

import os
import requests
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

# Configurações da API externa (Definir no .env)
# Exemplo: https://api.invertexto.com/v1/fipe-placa?token={token}&plate={placa}
API_URL_TEMPLATE = os.getenv("VEHICLE_API_URL", "")
API_TOKEN = os.getenv("VEHICLE_API_TOKEN", "")
API_TIMEOUT = 5.0  # Segundos máximos de espera para não travar o fluxo ao vivo

# Headers customizados (se a API exigir)
API_HEADERS = {}
if API_TOKEN and "Bearer" in API_TOKEN:
    API_HEADERS["Authorization"] = API_TOKEN


def consultar_placa_externa(placa: str):
    """
    Realiza a consulta HTTP na API externa.
    Retorna um dicionário padronizado ou None em caso de falha.
    """
    if not API_URL_TEMPLATE:
        # Modo silencioso: Sem API configurada, não fazemos nada.
        return None

    # Substitui os placeholders na URL
    url = API_URL_TEMPLATE.format(placa=placa, token=urllib.parse.quote(API_TOKEN))

    try:
        response = requests.get(url, headers=API_HEADERS, timeout=API_TIMEOUT)
        
        if response.status_code == 200:
            data = response.json()
            return _normalizar_resposta(data, placa)
        elif response.status_code == 404:
            print(f"[API Veículos] Placa {placa} não encontrada na base nacional.")
        else:
            print(f"[API Veículos] Erro HTTP {response.status_code}: {response.text}")
    
    except requests.exceptions.Timeout:
        print(f"[API Veículos] Timeout na consulta da placa {placa}. API demorou demais.")
    except Exception as e:
        print(f"[API Veículos] Erro de conexão ao consultar {placa}: {e}")
    
    return None


def _normalizar_resposta(json_data, placa_orig):
    """
    Normaliza as respostas de diferentes possíveis APIs do mercado para um padrão único:
    {
        "placa": str,
        "marca_modelo": str,
        "cor": str,
        "ano": str,
        "situacao": str (ROUBO_FURTO, LEGAL),
        "roubo_furtado": bool,
        "ipva_atrasado": bool, # Se fornecido,
        "licenciamento_atrasado": bool
    }
    """
    # ── Exemplo baseando-se em API comum do mercado (Invertexto/Sinesp Wrapper) ──
    # Para adaptar à sua API real, basta mapear os campos JSON corretos abaixo
    
    # Campo Modelo (Pode vir como 'modelo', 'marcaModelo', 'veiculo', etc)
    modelo = json_data.get('modelo') or json_data.get('marcaModelo') or json_data.get('marca') or "Desconhecido"
    
    # Cor
    cor = json_data.get('cor') or "Desconhecida"
    
    # Situação / Roubo
    sit_str = str(json_data.get('situacao', '')).strip().upper()
    roubo_furtado = False
    if "ROUBO" in sit_str or "FURTO" in sit_str or json_data.get('roubado', False):
        roubo_furtado = True

    # Débitos (Muitas APIs gratuitas não dão isso, assumimos False se não vier explícito)
    ipva_atrasado = False
    licenciamento_atrasado = False
    if 'ipva' in json_data and json_data['ipva'] == 'Atrasado':
        ipva_atrasado = True
        
    return {
        "placa": placa_orig,
        "marca_modelo": modelo,
        "cor": cor,
        "roubo_furtado": roubo_furtado,
        "ipva_atrasado": ipva_atrasado,
        "licenciamento_atrasado": licenciamento_atrasado,
        "raw": json_data # Mantém os dados brutos como segurança
    }
