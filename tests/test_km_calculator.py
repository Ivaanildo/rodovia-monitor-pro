"""
Testes unitários do módulo km_calculator.
"""
import pytest
import sys
import os

# Adiciona o diretório pai ao path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.km_calculator import (
    haversine,
    calcular_bearing,
    estimar_km,
    detectar_sentido,
    identificar_trecho_local,
    enriquecer_incidente,
)


class TestHaversine:
    """Testes para a função haversine."""

    def test_haversine_mesmo_ponto(self):
        assert haversine(-23.5, -46.6, -23.5, -46.6) == 0.0

    def test_haversine_distancias_conhecidas(self):
        # São Paulo (-23.55, -46.63) até Santos (-23.96, -46.33) ~55 km em linha reta
        d = haversine(-23.55, -46.63, -23.96, -46.33)
        assert 50 < d < 60

    def test_haversine_simetria(self):
        d1 = haversine(-23.5, -46.6, -24.0, -47.0)
        d2 = haversine(-24.0, -47.0, -23.5, -46.6)
        assert abs(d1 - d2) < 0.01


class TestCalcularBearing:
    """Testes para calcular_bearing."""

    def test_bearing_norte(self):
        # De um ponto para outro ao norte
        b = calcular_bearing(0, 0, 1, 0)
        assert 0 <= b <= 360
        assert b < 45 or b > 315  # Aproximadamente norte

    def test_bearing_retorna_0_360(self):
        b = calcular_bearing(-23.5, -46.6, -24.0, -47.0)
        assert 0 <= b <= 360


class TestEstimarKm:
    """Testes para estimar_km."""

    def test_sem_pontos_referencia(self):
        r = estimar_km(-23.5, -46.6, [])
        assert r["km_estimado"] is None
        assert r["confianca"] == 0.0

    def test_poucos_pontos(self, pontos_referencia):
        r = estimar_km(-23.5, -46.6, pontos_referencia[:1])
        assert r["km_estimado"] is None
        assert r["confianca"] == 0.0

    def test_coordenadas_nulas(self, pontos_referencia):
        r = estimar_km(None, -46.6, pontos_referencia)
        assert r["km_estimado"] is None
        assert r["confianca"] == 0.0

    def test_interpolacao_no_ponto(self, pontos_referencia):
        # Exatamente em um ponto de referência
        r = estimar_km(-23.5, -46.6, pontos_referencia)
        assert r["km_estimado"] is not None
        assert r["confianca"] >= 0.2
        assert 95 <= r["km_estimado"] <= 105 or r["confianca"] < 0.6

    def test_muito_longe_dos_pontos(self, pontos_referencia):
        # Ponto muito distante (>50 km)
        r = estimar_km(-20.0, -40.0, pontos_referencia)
        assert r["confianca"] <= 0.3


class TestDetectarSentido:
    """Testes para detectar_sentido."""

    def test_segmento_vazio(self):
        assert detectar_sentido(-23.5, -46.6, None) is None

    def test_segmento_com_sentido(self):
        seg = {"sentido": "Norte", "rodovia": "BR-116"}
        assert detectar_sentido(-23.5, -46.6, seg) == "Norte"

    def test_segmento_sem_sentido(self):
        seg = {"rodovia": "BR-116"}
        assert detectar_sentido(-23.5, -46.6, seg) is None


class TestIdentificarTrechoLocal:
    """Testes para identificar_trecho_local."""

    def test_lista_vazia(self):
        assert identificar_trecho_local(150, []) == ""

    def test_km_nulo(self, pontos_referencia):
        assert identificar_trecho_local(None, pontos_referencia) == ""

    def test_antes_do_primeiro(self, pontos_referencia):
        r = identificar_trecho_local(50, pontos_referencia)
        assert "Juquitiba" in r

    def test_depois_do_ultimo(self, pontos_referencia):
        r = identificar_trecho_local(300, pontos_referencia)
        assert "Cananéia" in r

    def test_entre_dois_pontos(self, pontos_referencia):
        r = identificar_trecho_local(175, pontos_referencia)
        assert "->" in r
        assert "Miracatu" in r
        assert "Registro" in r

    def test_gap_alto_retorna_proximo(self):
        pontos = [
            {"km": 100, "lat": -23.5, "lng": -46.6, "local": "Juquitiba"},
            {"km": 220, "lat": -24.5, "lng": -47.8, "local": "Registro"},
        ]
        r = identificar_trecho_local(140, pontos)
        assert "proximo a" in r
        assert "Juquitiba" in r
        assert "->" not in r

    def test_br116_km_290_aponta_itapecerica(self):
        pontos = [
            {"km": 285, "lat": -23.6150, "lng": -46.7840, "local": "Taboão da Serra"},
            {"km": 289, "lat": -23.6500, "lng": -46.8520, "local": "Embu das Artes"},
            {"km": 291, "lat": -23.7160, "lng": -46.8500, "local": "Itapecerica da Serra"},
            {"km": 305, "lat": -23.8530, "lng": -46.9440, "local": "São Lourenço da Serra"},
            {"km": 320, "lat": -23.9250, "lng": -47.0690, "local": "Juquitiba"},
        ]
        r = identificar_trecho_local(290.3, pontos)
        assert "Itapecerica da Serra" in r
        assert "Juquitiba" not in r

    def test_remove_sufixo_inicio_fim(self):
        pontos = [
            {"km": 300, "lat": -22.0, "lng": -44.0, "local": "Resende - Inicio"},
            {"km": 330, "lat": -22.9, "lng": -43.2, "local": "Rio de Janeiro - Fim"},
        ]
        r = identificar_trecho_local(318, pontos)
        assert "Inicio" not in r
        assert "Fim" not in r
        assert "Resende" in r
        assert "Rio de Janeiro" in r


class TestEnriquecerIncidente:
    """Testes para enriquecer_incidente."""

    def test_segmentos_vazios(self):
        inc = {"latitude": -23.5, "longitude": -46.6}
        r = enriquecer_incidente(inc, [])
        assert "km_estimado" in r
        assert r["km_estimado"] is None

    def test_incidente_sem_coordenadas(self):
        inc = {"descricao": " teste"}
        r = enriquecer_incidente(inc, [])
        assert "km_estimado" in r
        assert r.get("km_estimado") is None

    def test_enriquecimento_com_segmento(self, trecho_exemplo):
        segmentos = trecho_exemplo["segmentos"]
        inc = {"latitude": -23.7, "longitude": -47.0, "descricao": "Acidente"}
        r = enriquecer_incidente(inc, segmentos)
        assert "km_estimado" in r
        assert "confianca_localizacao" in r
