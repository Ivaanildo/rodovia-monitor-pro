"""
Testes para melhorias #7-#10 do roadmap de precisao.

#7  — Classificacao com delay absoluto (google_maps.py)
#8  — Jam factor por segmento (here_traffic.py + correlator.py)
#9  — Freshness score gradual (advisor.py) — testes em test_advisor.py
#10 — Ramer-Douglas-Peucker downsampling (here_traffic.py)
"""
import logging
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import google_maps as gm
from sources import here_traffic as ht
from sources.correlator import correlacionar_trecho


# ─── #7: Classificacao com delay absoluto ─────────────────────────────


class TestClassificarTransitoDelayAbsoluto:
    """Testa que atraso absoluto influencia classificacao para rotas longas."""

    def test_rota_curta_ratio_moderado(self):
        """Rota curta: 30min normal, 35min transito = ratio 1.17 → Moderado."""
        assert gm.classificar_transito(30 * 60, 35 * 60) == "Moderado"

    def test_rota_longa_ratio_baixo_delay_baixo_normal(self):
        """Rota longa: 300min normal, 305min transito = 5min atraso → Normal."""
        # ratio 1.017, atraso 5min (< 10min threshold)
        assert gm.classificar_transito(300 * 60, 305 * 60) == "Normal"

    def test_rota_longa_delay_moderado_promove(self):
        """Rota longa: 300min normal, 315min transito = 15min atraso → Moderado."""
        # ratio 1.05, atraso 15min (>= 10min, ratio > 1.03) → Moderado
        assert gm.classificar_transito(300 * 60, 315 * 60) == "Moderado"

    def test_rota_longa_delay_intenso_promove(self):
        """Rota longa: 300min normal, 325min transito = 25min atraso → Intenso."""
        # ratio 1.083, atraso 25min (>= 25min, ratio > 1.05) → Intenso
        assert gm.classificar_transito(300 * 60, 325 * 60) == "Intenso"

    def test_ratio_alto_sempre_intenso(self):
        """Ratio > 1.40 deve ser Intenso independente de delay absoluto."""
        # 60min normal, 90min transito = ratio 1.50
        assert gm.classificar_transito(60 * 60, 90 * 60) == "Intenso"

    def test_delay_grande_mas_ratio_minimo_insuficiente(self):
        """Atraso absoluto grande mas ratio < min_razao nao promove.

        Isso evita falsos positivos quando duracoes sao quase identicas
        mas ha ruido na medicao.
        """
        # 1000min normal, 1010min transito = ratio 1.01, atraso 10min
        # ratio 1.01 < 1.03 → nao promove para Moderado
        assert gm.classificar_transito(1000 * 60, 1010 * 60) == "Normal"

    def test_delay_25min_ratio_1_04_nao_intenso(self):
        """Atraso 25min mas ratio < 1.05 nao promove para Intenso."""
        # ratio ~1.042, atraso 25min
        # ratio < 1.05 → nao atinge Intenso por delay absoluto
        # ratio < 1.15 → nao atinge Moderado por ratio
        # atraso >= 10 e ratio > 1.03 → Moderado por delay absoluto
        assert gm.classificar_transito(600 * 60, 625 * 60) == "Moderado"

    def test_duracao_normal_zero(self):
        """Duracao normal zero retorna Sem dados."""
        assert gm.classificar_transito(0, 100) == "Sem dados"

    def test_sem_atraso_normal(self):
        """Sem atraso (mesma duracao) retorna Normal."""
        assert gm.classificar_transito(100 * 60, 100 * 60) == "Normal"

    def test_thresholds_atraso_abs_existem(self):
        """Verifica que as constantes de threshold existem e sao validas."""
        assert "Moderado" in gm.THRESHOLDS_ATRASO_ABS
        assert "Intenso" in gm.THRESHOLDS_ATRASO_ABS
        assert gm.THRESHOLDS_ATRASO_ABS["Moderado"]["min_atraso_min"] < (
            gm.THRESHOLDS_ATRASO_ABS["Intenso"]["min_atraso_min"]
        )


# ─── #8: Jam factor por segmento ──────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = ""
        self.response = self

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, responses=None):
        self.responses = responses or []
        self._call_idx = 0

    def get(self, url, params=None, timeout=0, headers=None):
        if self._call_idx < len(self.responses):
            resp = self.responses[self._call_idx]
            self._call_idx += 1
            return resp
        return _FakeResponse({"results": []})


class TestJamFactorPorSegmento:
    """Testa que dados por segmento sao preservados no resultado."""

    def test_jam_factor_max_exposto(self, monkeypatch):
        """Resultado deve conter jam_factor_max com o maximo dos segmentos."""
        # 3 segmentos: jam 0.5, 8.0, 1.0 → media 3.17, max 8.0
        flow_results = {
            "results": [
                {"currentFlow": {"speed": 30, "freeFlow": 33, "jamFactor": 0.5}},
                {"currentFlow": {"speed": 2, "freeFlow": 33, "jamFactor": 8.0}},
                {"currentFlow": {"speed": 28, "freeFlow": 33, "jamFactor": 1.0}},
            ]
        }
        fake_session = _FakeSession(responses=[_FakeResponse(flow_results)])
        monkeypatch.setattr(ht, "_get_sessao", lambda: fake_session)
        monkeypatch.setattr(ht, "_parse_ou_geocode", lambda *a: (-23.0, -46.0))
        monkeypatch.setattr(ht, "_obter_corridor_ou_none", lambda *a, **k: (None, None))

        result = ht.consultar_fluxo_trafego.__wrapped__(
            "key", "A", "B", trecho_nome="Teste",
        )
        assert result["jam_factor_max"] == 8.0
        assert result["segmentos_total"] == 3
        assert result["segmentos_congestionados"] == 1  # jam >= 5
        assert result["pct_congestionado"] > 30

    def test_segmentos_todos_livres(self, monkeypatch):
        """Quando todos os segmentos estao livres, segmentos_congestionados = 0."""
        flow_results = {
            "results": [
                {"currentFlow": {"speed": 30, "freeFlow": 33, "jamFactor": 0.5}},
                {"currentFlow": {"speed": 29, "freeFlow": 33, "jamFactor": 1.0}},
            ]
        }
        fake_session = _FakeSession(responses=[_FakeResponse(flow_results)])
        monkeypatch.setattr(ht, "_get_sessao", lambda: fake_session)
        monkeypatch.setattr(ht, "_parse_ou_geocode", lambda *a: (-23.0, -46.0))
        monkeypatch.setattr(ht, "_obter_corridor_ou_none", lambda *a, **k: (None, None))

        result = ht.consultar_fluxo_trafego.__wrapped__(
            "key", "A", "B", trecho_nome="Teste",
        )
        assert result["jam_factor_max"] == 1.0
        assert result["segmentos_congestionados"] == 0
        assert result["pct_congestionado"] == 0.0


class TestCorrelatorStatusPromocaoSegmento:
    """Testa que o correlator promove status baseado em jam_factor_max."""

    def _trecho(self):
        return {
            "nome": "BR-381 BH-SP",
            "origem": "BH",
            "destino": "SP",
            "rodovia": "BR-381",
            "sentido": "Sul",
            "concessionaria": "",
            "segmentos": [],
        }

    def test_media_normal_max_severo_promove_intenso(self):
        """Media jam 1.37 (Normal) mas max=8 com >=2 segs → Intenso."""
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 1.4,
            "jam_factor_max": 8.0,
            "segmentos_congestionados": 3,
            "pct_congestionado": 15.0,
            "velocidade_atual_kmh": 90,
            "velocidade_livre_kmh": 110,
        }
        r = correlacionar_trecho(self._trecho(), here_fluxo=here_fluxo)
        assert r["status"] == "Intenso"
        # Observacao reflete o status promovido
        assert "intenso" in r["descricao"].lower()

    def test_media_normal_max_5_promove_moderado(self):
        """Media jam 1.5 (Normal) mas max=5.5 com 1 seg → Moderado."""
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 1.5,
            "jam_factor_max": 5.5,
            "segmentos_congestionados": 1,
            "pct_congestionado": 5.0,
            "velocidade_atual_kmh": 95,
            "velocidade_livre_kmh": 110,
        }
        r = correlacionar_trecho(self._trecho(), here_fluxo=here_fluxo)
        assert r["status"] == "Moderado"
        assert "moderado" in r["descricao"].lower()

    def test_sem_dados_segmento_nao_promove(self):
        """Sem dados de segmento, comportamento original mantido."""
        here_fluxo = {
            "status": "Normal",
            "jam_factor": 1.4,
            "velocidade_atual_kmh": 90,
            "velocidade_livre_kmh": 110,
        }
        r = correlacionar_trecho(self._trecho(), here_fluxo=here_fluxo)
        assert r["status"] == "Normal"

    def test_media_ja_intenso_nao_duplica_promove(self):
        """Se status ja e Intenso pela media, nao altera."""
        here_fluxo = {
            "status": "Intenso",
            "jam_factor": 7.0,
            "jam_factor_max": 9.0,
            "segmentos_congestionados": 5,
            "pct_congestionado": 50.0,
            "velocidade_atual_kmh": 20,
            "velocidade_livre_kmh": 110,
        }
        r = correlacionar_trecho(self._trecho(), here_fluxo=here_fluxo)
        assert r["status"] == "Intenso"


# ─── #10: Ramer-Douglas-Peucker ───────────────────────────────────────


class TestRDPSimplify:
    """Testa o algoritmo RDP diretamente."""

    def test_linha_reta_simplifica_para_2(self):
        """Pontos colineares em linha reta → simplifica para 2 pontos."""
        # Pontos no equador em linha reta
        pts = [(0.0, i * 0.01) for i in range(100)]
        result = ht._rdp_simplify(pts, epsilon_m=50)
        assert len(result) == 2
        assert result[0] == pts[0]
        assert result[-1] == pts[-1]

    def test_curva_preservada(self):
        """Ponto fora da linha deve ser preservado (curva)."""
        # Linha reta de (0,0) a (0,1) com ponto desviado em (0.01, 0.5)
        pts = [(0.0, 0.0), (0.01, 0.5), (0.0, 1.0)]
        # 0.01 grau lat ≈ 1.1km → epsilon 500m deve preservar o ponto medio
        result = ht._rdp_simplify(pts, epsilon_m=500)
        assert len(result) == 3

    def test_poucos_pontos_sem_simplificar(self):
        """Com <= 2 pontos, retorna como esta."""
        assert ht._rdp_simplify([(0, 0)], 100) == [(0, 0)]
        assert ht._rdp_simplify([(0, 0), (1, 1)], 100) == [(0, 0), (1, 1)]
        assert ht._rdp_simplify([], 100) == []

    def test_multiplas_curvas_preservadas(self):
        """Polyline em zigzag preserva todos os pontos de inflexao."""
        # Zigzag: cada ponto alternando norte/sul
        pts = [(0.0, 0.0)]
        for i in range(1, 20):
            lat = 0.01 if i % 2 == 1 else 0.0  # ~1.1km desvio
            pts.append((lat, i * 0.01))

        result = ht._rdp_simplify(pts, epsilon_m=500)
        # Com 500m epsilon, desvios de 1.1km devem ser preservados
        assert len(result) > 10

    def test_epsilon_grande_colapsa(self):
        """Epsilon muito grande colapsa tudo para 2 pontos."""
        pts = [(0.0, 0.0), (0.001, 0.5), (0.0, 1.0)]
        # 0.001 grau ≈ 111m, epsilon 50km deve colapsar
        result = ht._rdp_simplify(pts, epsilon_m=50_000)
        assert len(result) == 2


class TestDownsampleRDP:
    """Testa _downsample_polyline com RDP."""

    def test_downsample_preserva_curvas(self, monkeypatch):
        """RDP preserva pontos de inflexao significativos."""
        class _FP:
            @staticmethod
            def encode(pts):
                # Simula encode curto para validar que cabe no limite
                return "B" * min(200, len(pts) * 5)

        monkeypatch.setattr(ht, "fp", _FP())

        # Polyline com 500 pontos em zigzag (curvas significativas)
        pts = []
        for i in range(500):
            lat = 0.01 * (i % 2) * (1 if i % 4 < 2 else -1)
            pts.append((lat, i * 0.001))

        poly_str, result = ht._downsample_polyline(
            pts, 100, "Teste", max_chars=1200,
        )
        assert poly_str is not None
        assert result is not None
        assert len(result) <= 300

    def test_downsample_sem_flexpolyline(self, monkeypatch):
        """Sem flexpolyline, retorna (None, None)."""
        monkeypatch.setattr(ht, "fp", None)
        assert ht._downsample_polyline([(0, 0), (1, 1)], 100, "T") == (None, None)

    def test_downsample_polyline_curta_sem_reducao(self, monkeypatch):
        """Polyline curta que ja cabe nao precisa de simplificacao."""
        class _FP:
            @staticmethod
            def encode(pts):
                return "short"

        monkeypatch.setattr(ht, "fp", _FP())
        pts = [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]
        poly_str, result = ht._downsample_polyline(pts, 100, "T")
        assert poly_str == "short"
        assert len(result) == 3
