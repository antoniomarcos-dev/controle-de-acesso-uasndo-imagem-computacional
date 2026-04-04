"""
Motor de Lógica de Negócio e Segurança.

Responsabilidades:
1. Verificar pendências judiciais de pessoas (mandados de prisão)
2. Verificar restrições de veículos (roubo, IPVA, licenciamento)
3. Decidir ação baseada no modo de operação (Cidade vs Evento)
4. Emitir alertas com níveis de cor (Verde/Amarelo/Vermelho)
5. Persistir alertas no banco de dados
"""

import time
import datetime
import threading

try:
    from backend.models import (
        NivelAlerta, TipoAlerta, AcaoTomada, AlertResult, ModoOperacao
    )
    from backend import storage
except ImportError:
    from .models import (
        NivelAlerta, TipoAlerta, AcaoTomada, AlertResult, ModoOperacao
    )
    from . import storage


# ═══════════════════════════════════════════════
#  Cores ANSI para console
# ═══════════════════════════════════════════════
class _Cor:
    VERMELHO = "\033[91m"
    AMARELO = "\033[93m"
    VERDE = "\033[92m"
    AZUL = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


class SecurityEngine:
    """Motor de decisões de segurança do sistema integrado."""

    def __init__(self):
        self.modo_atual = ModoOperacao.CIDADE
        self._lock = threading.Lock()

        # Estado da cancela virtual (modo evento)
        self.gate_status = "aberta"  # 'aberta' | 'bloqueada'
        self.gate_lock = threading.Lock()

        # Histórico de alertas recentes (para dashboard)
        self.alertas_recentes = []
        self.alertas_lock = threading.Lock()

        # Stats de alertas
        self.stats = {
            "total_verde": 0,
            "total_amarelo": 0,
            "total_vermelho": 0
        }

    # ══════════════════════════════════════════════
    #  Modo de Operação
    # ══════════════════════════════════════════════

    def set_modo(self, modo: ModoOperacao):
        """Altera o modo de operação do sistema."""
        with self._lock:
            old = self.modo_atual
            self.modo_atual = modo
            print(f"\n{_Cor.AZUL}{_Cor.BOLD}[MODO] Alternando: {old.value} → {modo.value}{_Cor.RESET}\n")

    def get_modo(self) -> ModoOperacao:
        """Retorna o modo de operação atual."""
        with self._lock:
            return self.modo_atual

    # ══════════════════════════════════════════════
    #  Verificação de Pendências — Pessoa
    # ══════════════════════════════════════════════

    def check_pessoa(self, pessoa_id):
        """
        Consulta pendências judiciais de uma pessoa no PostgreSQL.

        Returns:
            AlertResult com nível de alerta
        """
        if pessoa_id is None:
            return AlertResult(
                nivel=NivelAlerta.VERDE,
                descricao="Pessoa não identificada no cadastro"
            )

        pessoa_data = storage.get_pessoa_by_id(pessoa_id)
        if pessoa_data is None:
            return AlertResult(
                nivel=NivelAlerta.VERDE,
                descricao="Pessoa sem registro no sistema"
            )

        nome = pessoa_data.get('nome', 'Desconhecido')

        # ── Mandado de Prisão → ALERTA VERMELHO ──
        if pessoa_data.get('tem_mandado', False):
            return AlertResult(
                nivel=NivelAlerta.VERMELHO,
                tipo=TipoAlerta.MANDADO_PRISAO,
                descricao=f"⚠ MANDADO DE PRISÃO ATIVO para {nome}",
                pessoa_id=pessoa_id,
                pessoa_nome=nome
            )

        # ── Status Judicial não-limpo → ALERTA AMARELO ──
        status = pessoa_data.get('status_judicial', 'limpo')
        if status != 'limpo':
            return AlertResult(
                nivel=NivelAlerta.AMARELO,
                tipo=TipoAlerta.MANDADO_PRISAO,
                descricao=f"Pendência judicial ({status}) para {nome}",
                pessoa_id=pessoa_id,
                pessoa_nome=nome
            )

        # ── Tudo limpo → VERDE ──
        return AlertResult(
            nivel=NivelAlerta.VERDE,
            descricao=f"✓ {nome} — Nenhuma pendência encontrada",
            pessoa_id=pessoa_id,
            pessoa_nome=nome
        )

    # ══════════════════════════════════════════════
    #  Verificação de Pendências — Veículo
    # ══════════════════════════════════════════════

    def check_veiculo(self, placa):
        """
        Consulta restrições de um veículo no PostgreSQL.

        Returns:
            AlertResult com nível de alerta
        """
        if not placa:
            return AlertResult(
                nivel=NivelAlerta.VERDE,
                descricao="Placa não detectada"
            )

        veiculo_data = storage.get_veiculo_by_placa(placa)
        if veiculo_data is None:
            return AlertResult(
                nivel=NivelAlerta.VERDE,
                descricao=f"Placa {placa} não encontrada no cadastro",
                placa=placa
            )

        vid = veiculo_data.get('id')

        # ── Veículo Roubado → ALERTA VERMELHO ──
        if veiculo_data.get('status_roubo', False):
            return AlertResult(
                nivel=NivelAlerta.VERMELHO,
                tipo=TipoAlerta.VEICULO_ROUBADO,
                descricao=f"⚠ VEÍCULO COM RESTRIÇÃO DE ROUBO: {placa}",
                veiculo_id=vid,
                placa=placa
            )

        # ── IPVA Atrasado → ALERTA AMARELO ──
        if veiculo_data.get('ipva_atrasado', False):
            return AlertResult(
                nivel=NivelAlerta.AMARELO,
                tipo=TipoAlerta.IPVA_ATRASADO,
                descricao=f"IPVA atrasado para veículo {placa}",
                veiculo_id=vid,
                placa=placa
            )

        # ── Licenciamento Atrasado → ALERTA AMARELO ──
        if veiculo_data.get('licenciamento_atrasado', False):
            return AlertResult(
                nivel=NivelAlerta.AMARELO,
                tipo=TipoAlerta.LICENCIAMENTO,
                descricao=f"Licenciamento vencido para veículo {placa}",
                veiculo_id=vid,
                placa=placa
            )

        # ── Limpo → VERDE ──
        return AlertResult(
            nivel=NivelAlerta.VERDE,
            descricao=f"✓ Veículo {placa} — Sem pendências",
            veiculo_id=vid,
            placa=placa
        )

    # ══════════════════════════════════════════════
    #  Decisão Final + Ação
    # ══════════════════════════════════════════════

    def decidir_acao(self, alert_pessoa, alert_veiculo):
        """
        Decide a ação com base nos alertas e no modo de operação.

        Args:
            alert_pessoa: AlertResult da verificação de pessoa
            alert_veiculo: AlertResult da verificação de veículo

        Returns:
            (acao: AcaoTomada, nivel_final: NivelAlerta, descricao: str)
        """
        # Determinar nível máximo
        niveis = {NivelAlerta.VERDE: 0, NivelAlerta.AMARELO: 1, NivelAlerta.VERMELHO: 2}

        nivel_p = niveis.get(alert_pessoa.nivel, 0) if alert_pessoa else 0
        nivel_v = niveis.get(alert_veiculo.nivel, 0) if alert_veiculo else 0

        nivel_max = max(nivel_p, nivel_v)

        if nivel_max == 2:
            nivel_final = NivelAlerta.VERMELHO
        elif nivel_max == 1:
            nivel_final = NivelAlerta.AMARELO
        else:
            nivel_final = NivelAlerta.VERDE

        # Montar descrição
        partes = []
        if alert_pessoa and alert_pessoa.nivel != NivelAlerta.VERDE:
            partes.append(alert_pessoa.descricao)
        if alert_veiculo and alert_veiculo.nivel != NivelAlerta.VERDE:
            partes.append(alert_veiculo.descricao)
        descricao = " | ".join(partes) if partes else "Sem pendências"

        # Decidir ação baseada no modo
        modo = self.get_modo()

        if modo == ModoOperacao.CIDADE:
            acao = AcaoTomada.APENAS_REGISTRO
        elif modo == ModoOperacao.EVENTO:
            if nivel_final == NivelAlerta.VERMELHO:
                acao = AcaoTomada.BLOQUEADO
                self._set_gate("bloqueada")
            elif nivel_final == NivelAlerta.AMARELO:
                # No amarelo, registra mas libera (decisão do operador)
                acao = AcaoTomada.LIBERADO
                self._set_gate("aberta")
            else:
                acao = AcaoTomada.LIBERADO
                self._set_gate("aberta")
        else:
            acao = AcaoTomada.APENAS_REGISTRO

        return acao, nivel_final, descricao

    def processar_deteccao(self, pessoa_id=None, placa=None, genero=None,
                           idade_cat=None, confianca_facial=0.0, localizacao=None):
        """
        Pipeline completo: check pendências → decidir ação → emitir alerta → persistir.

        Args:
            pessoa_id: ID da pessoa identificada (ou None)
            placa: Texto da placa detectada (ou None)
            genero: Gênero estimado
            idade_cat: Categoria de idade
            confianca_facial: Confiança do match facial
            localizacao: Local do ponto de monitoramento

        Returns:
            dict com resultado completo do processamento
        """
        # ── Checar pendências ──
        alert_pessoa = self.check_pessoa(pessoa_id)
        alert_veiculo = self.check_veiculo(placa)

        # ── Decidir ação ──
        acao, nivel_final, descricao = self.decidir_acao(alert_pessoa, alert_veiculo)

        # ── Emitir log colorido no console ──
        self._emitir_log_console(nivel_final, descricao, acao, pessoa_id, placa)

        # ── Persistir registro de acesso no banco ──
        veiculo_id = alert_veiculo.veiculo_id if alert_veiculo else None
        storage.log_registro_acesso(
            tipo='passagem',
            modo_operacao=self.get_modo().value,
            pessoa_id=pessoa_id,
            veiculo_id=veiculo_id,
            nivel_alerta=nivel_final.value,
            detalhes_alerta=descricao,
            confianca_facial=confianca_facial,
            placa_detectada=placa,
            acao_tomada=acao.value,
            localizacao=localizacao
        )

        # ── Persistir alerta se não for verde ──
        if nivel_final != NivelAlerta.VERDE:
            tipo_alerta = None
            if alert_pessoa and alert_pessoa.tipo:
                tipo_alerta = alert_pessoa.tipo.value
            elif alert_veiculo and alert_veiculo.tipo:
                tipo_alerta = alert_veiculo.tipo.value

            storage.log_alerta(
                pessoa_id=pessoa_id,
                veiculo_id=veiculo_id,
                tipo_alerta=tipo_alerta or 'pendencia_generica',
                nivel=nivel_final.value,
                descricao=descricao
            )

        # ── Atualizar stats e cache de alertas recentes ──
        self.stats[f"total_{nivel_final.value}"] += 1

        with self.alertas_lock:
            self.alertas_recentes.append({
                "nivel": nivel_final.value,
                "descricao": descricao,
                "acao": acao.value,
                "pessoa_id": pessoa_id,
                "placa": placa,
                "timestamp": time.strftime("%H:%M:%S")
            })
            if len(self.alertas_recentes) > 50:
                self.alertas_recentes = self.alertas_recentes[-50:]

        return {
            "nivel": nivel_final.value,
            "acao": acao.value,
            "descricao": descricao,
            "pessoa_id": pessoa_id,
            "veiculo_id": veiculo_id,
            "placa": placa
        }

    # ══════════════════════════════════════════════
    #  Cancela Virtual (Modo Evento)
    # ══════════════════════════════════════════════

    def _set_gate(self, status):
        """Atualiza estado da cancela virtual."""
        with self.gate_lock:
            self.gate_status = status

    def get_gate_status(self):
        """Retorna estado atual da cancela."""
        with self.gate_lock:
            return self.gate_status

    def override_gate(self, acao, motivo="Override manual"):
        """Override manual da cancela pelo operador."""
        if acao == AcaoTomada.LIBERADO:
            self._set_gate("aberta")
        elif acao == AcaoTomada.BLOQUEADO:
            self._set_gate("bloqueada")

        print(f"\n{_Cor.AZUL}[GATE] Override manual: {acao.value} — {motivo}{_Cor.RESET}\n")

    # ══════════════════════════════════════════════
    #  Log no Console
    # ══════════════════════════════════════════════

    def _emitir_log_console(self, nivel, descricao, acao, pessoa_id=None, placa=None):
        """Emite log colorido no console."""
        ts = time.strftime("%H:%M:%S")
        modo = self.get_modo().value.upper()

        if nivel == NivelAlerta.VERMELHO:
            cor = _Cor.VERMELHO
            emoji = "🔴"
            header = "ALERTA VERMELHO"
        elif nivel == NivelAlerta.AMARELO:
            cor = _Cor.AMARELO
            emoji = "🟡"
            header = "ALERTA AMARELO"
        else:
            cor = _Cor.VERDE
            emoji = "🟢"
            header = "LOG VERDE"

        print(f"\n{cor}{_Cor.BOLD}{'═' * 60}")
        print(f"  {emoji} {header} [{modo}] — {ts}")
        print(f"{'═' * 60}{_Cor.RESET}")
        print(f"{cor}  {descricao}")
        if pessoa_id:
            print(f"  Pessoa ID: {pessoa_id}")
        if placa:
            print(f"  Placa: {placa}")
        print(f"  Ação: {acao.value}")
        print(f"{cor}{'─' * 60}{_Cor.RESET}\n")

    # ══════════════════════════════════════════════
    #  API do Dashboard
    # ══════════════════════════════════════════════

    def get_alertas_recentes(self):
        """Retorna alertas recentes para o dashboard."""
        with self.alertas_lock:
            return list(self.alertas_recentes)

    def get_security_stats(self):
        """Retorna estatísticas de segurança."""
        return {
            "modo": self.get_modo().value,
            "gate_status": self.get_gate_status(),
            "total_verde": self.stats["total_verde"],
            "total_amarelo": self.stats["total_amarelo"],
            "total_vermelho": self.stats["total_vermelho"],
        }
