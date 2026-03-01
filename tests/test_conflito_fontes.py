"""Testes da deteccao de conflito entre fontes HERE vs Google (#4)."""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources.correlator import (
    correlacionar_trecho, _detectar_conflito_fontes,
)


@pytest.fixture
def trecho():
    return {
        "nome": "SP-Registro",
        "origem": "São Paulo, SP",
        "destino": "Registro, SP",
        "rodovia": "BR-116",
        "sentido": "Sul",
        "concessionaria": "Arteris",
        "segmentos": [],
    }


# ===== Testes unitarios de _detectar_conflito_fontes =====

class TestDetectarConflitoFontes:
    def test_sem_conflito_ambos_normal(self):
        assert _detectar_conflito_fontes("Normal", "Normal") is None

    def test_sem_conflito_diff_1_nivel(self):
        """Normal vs Moderado = diferenca 1, sem conflito."""
        assert _detectar_conflito_fontes("Normal", "Moderado") is None

    def test_sem_conflito_ambos_intenso(self):
        assert _detectar_conflito_fontes("Intenso", "Intenso") is None

    def test_sem_conflito_uma_fonte_sem_dados(self):
        """Se uma fonte nao tem dados, nao ha conflito."""
        assert _detectar_conflito_fontes("Sem dados", "Normal") is None
        assert _detectar_conflito_fontes("Normal", "Sem dados") is None
        assert _detectar_conflito_fontes("Sem dados", "Sem dados") is None

    def test_conflito_here_normal_google_intenso(self):
        """HERE Normal vs Google Intenso = diferenca 2 niveis."""
        result = _detectar_conflito_fontes("Normal", "Intenso")
        assert result is not None
        assert result["grau"] == "moderado"
        assert "Normal" in result["detalhe"]
        assert "Intenso" in result["detalhe"]

    def test_conflito_here_normal_google_parado(self):
        """HERE Normal vs Google Parado = diferenca 3 niveis = alto."""
        result = _detectar_conflito_fontes("Normal", "Parado")
        assert result is not None
        assert result["grau"] == "alto"

    def test_conflito_here_parado_google_normal(self):
        """HERE Parado vs Google Normal = alto."""
        result = _detectar_conflito_fontes("Parado", "Normal")
        assert result is not None
        assert result["grau"] == "alto"

    def test_conflito_here_normal_google_normal_mas_atraso_alto(self):
        """HERE Normal + Google Normal mas atraso >= 15min = alto."""
        result = _detectar_conflito_fontes(
            "Normal", "Normal", atraso_min=20,
        )
        # Ambos Normal -> diff=0, sem conflito mesmo com atraso
        assert result is None

    def test_conflito_here_normal_com_atraso_significativo(self):
        """HERE Normal + atraso >= 15min no Google eleva para alto."""
        result = _detectar_conflito_fontes(
            "Normal", "Intenso", atraso_min=20,
        )
        assert result is not None
        assert result["grau"] == "alto"
        assert "atraso" in result["detalhe"]

    def test_conflito_google_normal_com_jam_alto(self):
        """Google Normal + HERE com jam_factor >= 5 = alto."""
        result = _detectar_conflito_fontes(
            "Intenso", "Normal", jam_factor=7,
        )
        assert result is not None
        assert result["grau"] == "alto"
        assert "Jam Factor" in result["detalhe"]

    def test_conflito_moderado_vs_parado(self):
        """Moderado vs Parado = diferenca 2 niveis = moderado."""
        result = _detectar_conflito_fontes("Moderado", "Parado")
        assert result is not None
        assert result["grau"] == "moderado"


# ===== Testes de integracao em correlacionar_trecho =====

class TestConflitoCorrrelacionar:
    def test_conflito_flag_quando_fontes_divergem(self, trecho):
        """Resultado inclui conflito_fontes=True quando HERE Normal + Google Intenso."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Intenso",
            "duracao_normal_min": 120,
            "duracao_transito_min": 180,
            "atraso_min": 60,
            "distancia_km": 200,
        }
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 0,
            "velocidade_atual_kmh": 80,
            "velocidade_livre_kmh": 80,
        }
        r = correlacionar_trecho(
            trecho, gmaps_data=gmaps, here_fluxo=here_fluxo,
        )
        assert r["conflito_fontes"] is True
        assert r["conflito_grau"] == "alto"

    def test_sem_conflito_quando_concordam(self, trecho):
        """Sem conflito quando ambas fontes dizem Normal."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Normal",
            "duracao_normal_min": 120,
            "duracao_transito_min": 125,
            "atraso_min": 5,
            "distancia_km": 200,
        }
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 1,
            "velocidade_atual_kmh": 75,
            "velocidade_livre_kmh": 80,
        }
        r = correlacionar_trecho(
            trecho, gmaps_data=gmaps, here_fluxo=here_fluxo,
        )
        assert r["conflito_fontes"] is False
        assert r["conflito_detalhe"] == ""

    def test_confianca_rebaixada_com_conflito(self, trecho):
        """Conflito rebaixa confianca: Alta -> Media."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Intenso",
            "duracao_normal_min": 120,
            "duracao_transito_min": 180,
            "atraso_min": 60,
            "distancia_km": 200,
        }
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 3,
            "velocidade_atual_kmh": 60,
            "velocidade_livre_kmh": 80,
        }
        r = correlacionar_trecho(
            trecho, gmaps_data=gmaps, here_fluxo=here_fluxo,
        )
        # Com 2 fontes (jam>0 + atraso>0) normalmente seria Alta,
        # mas conflito rebaixa para Media
        assert r["confianca"] == "Media"

    def test_sem_conflito_sem_dados_google(self, trecho):
        """Sem conflito quando Google nao tem dados."""
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 1,
            "velocidade_atual_kmh": 80,
            "velocidade_livre_kmh": 80,
        }
        r = correlacionar_trecho(trecho, here_fluxo=here_fluxo)
        assert r["conflito_fontes"] is False

    def test_sem_conflito_sem_dados_here(self, trecho):
        """Sem conflito quando HERE nao tem dados."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Normal",
            "duracao_normal_min": 120,
            "duracao_transito_min": 125,
            "atraso_min": 5,
            "distancia_km": 200,
        }
        r = correlacionar_trecho(trecho, gmaps_data=gmaps)
        assert r["conflito_fontes"] is False

    def test_acao_revalidar_com_conflito_sem_ocorrencia(self, trecho):
        """Sem ocorrencia + conflito -> acao de revalidacao."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Intenso",
            "duracao_normal_min": 120,
            "duracao_transito_min": 180,
            "atraso_min": 60,
            "distancia_km": 200,
        }
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 0,
            "velocidade_atual_kmh": 80,
            "velocidade_livre_kmh": 80,
        }
        r = correlacionar_trecho(
            trecho, gmaps_data=gmaps, here_fluxo=here_fluxo,
        )
        assert "revalidar" in r["acao_recomendada"].lower() or "monitorar" in r["acao_recomendada"].lower()
