"""Testes unitarios do modulo km_calculator."""

import os
import sys

import pytest

# Adiciona o diretorio pai ao path para imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sources.km_calculator as km_mod
from sources.km_calculator import (
    calcular_bearing,
    detectar_sentido,
    enriquecer_incidente,
    estimar_km,
    haversine,
    identificar_trecho_local,
)


class TestHaversine:
    """Testes para a funcao haversine."""

    def test_haversine_mesmo_ponto(self):
        assert haversine(-23.5, -46.6, -23.5, -46.6) == 0.0

    def test_haversine_distancias_conhecidas(self):
        # Sao Paulo (-23.55, -46.63) ate Santos (-23.96, -46.33) ~55 km em linha reta
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
        # Exatamente em um ponto de referencia
        r = estimar_km(-23.5, -46.6, pontos_referencia)
        assert r["km_estimado"] is not None
        assert r["confianca"] >= 0.2
        assert 95 <= r["km_estimado"] <= 105 or r["confianca"] < 0.6

    def test_muito_longe_dos_pontos(self, pontos_referencia):
        # Ponto muito distante (>50 km)
        r = estimar_km(-20.0, -40.0, pontos_referencia)
        assert r["confianca"] <= 0.3

    def test_ignora_pontos_invalidos(self):
        pontos = [
            {"km": 100, "lat": -23.5, "lng": -46.6, "local": "A"},
            {"km": "x", "lat": -24.0, "lng": -47.2, "local": "invalido"},
            {"km": 200, "lat": -24.5, "lng": -47.8, "local": "B"},
            {"km": 250, "lng": -48.4, "local": "sem-lat"},
        ]
        r = estimar_km(-23.8, -46.9, pontos)
        assert r["km_estimado"] is not None
        assert r["confianca"] > 0

    def test_prioriza_pares_vizinhos_do_ponto_mais_proximo(self, monkeypatch):
        incidente = (9.0, 9.0)
        pontos = [
            {"km": 0, "lat": 0.0, "lng": 0.0, "local": "P0"},
            {"km": 100, "lat": 1.0, "lng": 1.0, "local": "P1"},
            {"km": 200, "lat": 2.0, "lng": 2.0, "local": "P2"},
            {"km": 300, "lat": 3.0, "lng": 3.0, "local": "P3"},
        ]

        dist_incidente = {
            (0.0, 0.0): 30.0,
            (1.0, 1.0): 1.0,
            (2.0, 2.0): 2.0,
            (3.0, 3.0): 40.0,
        }
        dist_pares = {
            frozenset(((0.0, 0.0), (1.0, 1.0))): 10.0,
            frozenset(((1.0, 1.0), (2.0, 2.0))): 10.0,
            frozenset(((2.0, 2.0), (3.0, 3.0))): 42.0,
        }

        def fake_haversine(lat1, lng1, lat2, lng2):
            p1 = (lat1, lng1)
            p2 = (lat2, lng2)
            if p1 == incidente:
                return dist_incidente[p2]
            if p2 == incidente:
                return dist_incidente[p1]
            return dist_pares[frozenset((p1, p2))]

        monkeypatch.setattr(km_mod, "haversine", fake_haversine)
        r = km_mod.estimar_km(incidente[0], incidente[1], pontos)

        # Se usasse todos os pares, cairia no par (P2, P3) -> ~204.8.
        # Com pares vizinhos de P1, deve ficar entre P1 e P2 -> ~133.3.
        assert 130 <= r["km_estimado"] <= 140

    def test_penaliza_confianca_em_gap_grande(self):
        pontos_gap_pequeno = [
            {"km": 100, "lat": 0.0, "lng": 0.0, "local": "A"},
            {"km": 120, "lat": 0.0, "lng": 0.18, "local": "B"},
        ]
        pontos_gap_grande = [
            {"km": 100, "lat": 0.0, "lng": 0.0, "local": "A"},
            {"km": 300, "lat": 0.0, "lng": 0.18, "local": "B"},
        ]

        r1 = estimar_km(0.0, 0.09, pontos_gap_pequeno)
        r2 = estimar_km(0.0, 0.09, pontos_gap_grande)
        assert r1["confianca"] > r2["confianca"]


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
        assert "Canan" in r

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
            {"km": 285, "lat": -23.6150, "lng": -46.7840, "local": "Taboao da Serra"},
            {"km": 289, "lat": -23.6500, "lng": -46.8520, "local": "Embu das Artes"},
            {"km": 291, "lat": -23.7160, "lng": -46.8500, "local": "Itapecerica da Serra"},
            {"km": 305, "lat": -23.8530, "lng": -46.9440, "local": "Sao Lourenco da Serra"},
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

    def test_remove_sufixo_inicio_fim_com_acento(self):
        pontos = [
            {"km": 300, "lat": -22.0, "lng": -44.0, "local": "Sao Paulo - In\u00edcio"},
            {"km": 318, "lat": -22.9, "lng": -43.2, "local": "Jaguar\u00e9 - Fim"},
        ]
        r = identificar_trecho_local(310.5, pontos)
        assert "In\u00edcio" not in r
        assert "Fim" not in r
        assert "Sao Paulo" in r
        assert "Jaguar\u00e9" in r

    def test_gap_metropolitano_por_hints_acentuados(self):
        pontos = [
            {"km": 100, "lat": -23.5, "lng": -46.6, "local": "S\u00e3o Paulo - In\u00edcio"},
            {"km": 132, "lat": -23.6, "lng": -46.7, "local": "Jaguar\u00e9 - Fim"},
        ]
        r = identificar_trecho_local(112, pontos)
        assert "proximo a" in r


class TestEnriquecerIncidente:
    """Testes para enriquecer_incidente."""

    def test_segmentos_vazios(self):
        inc = {"latitude": -23.5, "longitude": -46.6}
        r = enriquecer_incidente(inc, [])
        assert "km_estimado" in r
        assert r["km_estimado"] is None

    def test_incidente_sem_coordenadas(self):
        inc = {"descricao": "teste"}
        r = enriquecer_incidente(inc, [])
        assert "km_estimado" in r
        assert r.get("km_estimado") is None

    def test_enriquecimento_com_segmento(self, trecho_exemplo):
        segmentos = trecho_exemplo["segmentos"]
        inc = {"latitude": -23.7, "longitude": -47.0, "descricao": "Acidente"}
        r = enriquecer_incidente(inc, segmentos)
        assert "km_estimado" in r
        assert "confianca_localizacao" in r
