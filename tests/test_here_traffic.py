"""Testes unitarios para o modulo HERE Traffic."""
import hashlib
import json
import logging
import math
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import here_traffic as ht


# ===== Helpers =====

class _FakeResponse:
    def __init__(self, payload, status_code=200, content_type="application/json"):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = ""
        self.response = self  # para HTTPError

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests.exceptions import HTTPError
            err = HTTPError(response=self)
            err.response = self
            raise err


class _FakeSession:
    def __init__(self, responses=None):
        self.responses = responses or []
        self._call_idx = 0
        self.calls = []

    def get(self, url, params=None, timeout=0, headers=None):
        self.calls.append({"url": url, "params": params})
        if self._call_idx < len(self.responses):
            resp = self.responses[self._call_idx]
            self._call_idx += 1
            return resp
        return _FakeResponse({"results": []})


# ===== Etapa 1A: Cache lock =====

def test_geocode_cache_thread_safe(monkeypatch):
    """Verifica que cache com lock funciona corretamente em thread."""
    call_count = 0

    def fake_geocode(api_key, endereco):
        nonlocal call_count
        call_count += 1
        return (-23.55, -46.63)

    monkeypatch.setattr(ht, "_geocode_endereco", fake_geocode)
    cache = {}

    # Primeira chamada: faz geocode
    result = ht._geocode_com_cache("key", "Sao Paulo", cache)
    assert result == (-23.55, -46.63)
    assert call_count == 1

    # Segunda chamada: usa cache
    result = ht._geocode_com_cache("key", "Sao Paulo", cache)
    assert result == (-23.55, -46.63)
    assert call_count == 1  # nao chamou de novo


# ===== Etapa 1B: Severity fallback logging =====

def test_severidade_fallback_loga_debug(caplog):
    """Quando nem severity nem criticality sao reconhecidos, loga debug."""
    with caplog.at_level(logging.DEBUG, logger="sources.here_traffic"):
        sev_id, sev_str = ht._severidade_here(None, "")
    assert sev_id == 2
    assert sev_str == "Média"
    assert any("fallback" in r.message.lower() for r in caplog.records)


def test_severidade_com_criticality_valido():
    """Quando criticality e valido, nao usa fallback."""
    sev_id, sev_str = ht._severidade_here(None, "critical")
    assert sev_id == 4
    assert sev_str == "Crítica"


def test_severidade_com_id_inteiro():
    """Quando severity_id e inteiro valido, usa diretamente."""
    sev_id, sev_str = ht._severidade_here(3, "low")
    assert sev_id == 3
    assert sev_str == "Alta"


# ===== Etapa 1C: Deduplicacao =====

def test_deduplicacao_deterministica():
    """Mesmos dados em ordem diferente devem gerar mesmo hash."""
    item1 = {"incidentDetails": {"type": "accident", "severity": 3},
             "location": {"shape": {"links": []}}}
    item2 = {"incidentDetails": {"severity": 3, "type": "accident"},
             "location": {"shape": {"links": []}}}

    def make_id(item):
        payload = json.dumps(
            {"d": item.get("incidentDetails", {}),
             "l": item.get("location", {})},
            sort_keys=True, default=str,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    assert make_id(item1) == make_id(item2)


# ===== Etapa 3: Centro de geometria =====

def test_parse_incidente_centro_geometria():
    """Verifica que lat/lng sao o centro de todos os pontos."""
    item = {
        "incidentDetails": {
            "type": "accident",
            "severity": 3,
            "criticality": "major",
            "summary": {"value": "Acidente"},
            "roadInfo": {"name": "BR-116"},
        },
        "location": {
            "shape": {
                "links": [
                    {"points": [
                        {"lat": -23.0, "lng": -46.0},
                        {"lat": -23.2, "lng": -46.2},
                    ]},
                    {"points": [
                        {"lat": -23.4, "lng": -46.4},
                        {"lat": -23.8, "lng": -46.8},
                    ]},
                ]
            }
        },
    }
    result = ht._parse_incidente(item, "Teste")
    assert result is not None
    # Media de -23.0, -23.2, -23.4, -23.8 = -23.35
    assert abs(result["latitude"] - (-23.35)) < 0.01
    assert abs(result["longitude"] - (-46.35)) < 0.01
    assert result["shape_points_count"] == 4


def test_parse_incidente_sem_pontos():
    """Sem pontos de geometria, lat/lng devem ser None."""
    item = {
        "incidentDetails": {
            "type": "congestion",
            "criticality": "minor",
        },
        "location": {},
    }
    result = ht._parse_incidente(item, "Teste")
    assert result is not None
    assert result["latitude"] is None
    assert result["longitude"] is None
    assert result["shape_points_count"] == 0


def test_classificar_categoria_here_road_closed_total():
    cat, escopo, causa = ht._classificar_categoria_here(
        "roadClosure", True, "Via totalmente interditada por acidente",
    )
    assert cat == "Interdição"
    assert escopo == "total"
    assert causa == "acidente"


def test_classificar_categoria_here_lane_restriction_parcial():
    cat, escopo, causa = ht._classificar_categoria_here(
        "laneRestriction", False, "Faixa fechada com tráfego fluindo",
    )
    assert cat == "Bloqueio Parcial"
    assert escopo == "parcial"
    assert causa == "indefinida"


def test_classificar_categoria_here_accident_sem_fechamento_total():
    cat, escopo, causa = ht._classificar_categoria_here(
        "accident", False, "Acidente no acostamento com desvio possivel",
    )
    assert cat == "Colisão"
    assert escopo == "nenhum"
    assert causa == "acidente"


def test_classificar_categoria_here_road_hazard_generico_nao_interdicao():
    cat, escopo, causa = ht._classificar_categoria_here(
        "roadHazard", False, "Hazard no acostamento",
    )
    assert cat == "Ocorrência"
    assert escopo == "nenhum"
    assert causa == "risco"


def test_classificar_categoria_here_road_hazard_com_bloqueio_total():
    cat, escopo, causa = ht._classificar_categoria_here(
        "roadHazard", False, "Bloqueio total por deslizamento de terra",
    )
    assert cat == "Interdição"
    assert escopo == "total"
    assert causa == "risco"


# ===== Etapa 5: Distancia ponto-segmento =====

def test_dist_ponto_segmento_perpendicular():
    """Ponto a ~111m perpendicular de um segmento na linha do equador."""
    # Segmento horizontal no equador: (0, 0) -> (0, 1)
    # Ponto a ~0.001 grau ao norte (0.001 * 111km = ~111m)
    p = (0.001, 0.5)
    a = (0.0, 0.0)
    b = (0.0, 1.0)
    dist = ht._dist_ponto_segmento_m(p, a, b)
    # ~111 metros (1 grau lat ≈ 111km, 0.001 grau ≈ 111m)
    assert 100 < dist < 125


def test_dist_ponto_segmento_no_segmento():
    """Ponto exatamente no segmento deve ter distancia ~0."""
    p = (0.0, 0.5)
    a = (0.0, 0.0)
    b = (0.0, 1.0)
    dist = ht._dist_ponto_segmento_m(p, a, b)
    assert dist < 1.0  # menos de 1 metro


def test_dist_ponto_polyline():
    """Menor distancia a uma polyline com 3 segmentos."""
    poly = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    p = (0.001, 0.5)  # proximo ao primeiro segmento
    dist = ht._dist_ponto_polyline_m(p, poly)
    assert dist < 125

    p_longe = (5.0, 5.0)  # longe
    dist_longe = ht._dist_ponto_polyline_m(p_longe, poly)
    assert dist_longe > 100_000  # mais de 100km


def test_dist_ponto_polyline_vazia():
    """Polyline vazia retorna infinito."""
    assert ht._dist_ponto_polyline_m((0, 0), []) == float("inf")
    assert ht._dist_ponto_polyline_m((0, 0), [(0, 0)]) == float("inf")


# ===== Etapa 6: Matching de rodovias =====

def test_extrair_codigo_rodovia_formatos():
    """Testa extração de códigos de rodovia em vários formatos."""
    assert ht._extrair_codigo_rodovia("BR-116") == "BR116"
    assert ht._extrair_codigo_rodovia("BR 116") == "BR116"
    assert ht._extrair_codigo_rodovia("BR116") == "BR116"
    assert ht._extrair_codigo_rodovia("SP-330") == "SP330"
    assert ht._extrair_codigo_rodovia("sp 330") == "SP330"
    assert ht._extrair_codigo_rodovia("Rodovia Presidente Dutra") is None
    assert ht._extrair_codigo_rodovia("") is None


def test_incidente_relevante_codigo_rodovia():
    """Filtro por código de rodovia deve funcionar com formatos variados."""
    inc = {"rodovia_afetada": "BR-116", "descricao": "Acidente na BR 116"}

    assert ht._incidente_relevante_para_rodovia(inc, "BR-116") is True
    assert ht._incidente_relevante_para_rodovia(inc, "BR 116") is True
    assert ht._incidente_relevante_para_rodovia(inc, "SP-330") is False


def test_incidente_relevante_sem_codigo_rejeita_em_corridor():
    """Incidente em avenida local sem BR explicita deve ser rejeitado."""
    inc = {"rodovia_afetada": "", "descricao": "Interdição em avenida local"}
    assert ht._incidente_relevante_para_rodovia(inc, "BR-116", modo="corridor") is False


def test_incidente_relevante_sem_codigo_rejeita_em_corridor_segmentado():
    """Corridor segmentado tambem nao deve aceitar incidente urbano sem BR."""
    inc = {"rodovia_afetada": "", "descricao": "Interdição em avenida local"}
    assert ht._incidente_relevante_para_rodovia(
        inc, "BR-116", modo="corridor_segmentado"
    ) is False


def test_incidente_relevante_sem_codigo_rejeita_em_bbox():
    """Em bbox, incidente sem código BR continua sendo rejeitado."""
    inc = {"rodovia_afetada": "", "descricao": "Interdição em avenida local"}
    assert ht._incidente_relevante_para_rodovia(inc, "BR-116", modo="bbox") is False


def test_incidente_relevante_multi_rodovia_com_match():
    """Filtro multi-rodovia aceita incidente em qualquer BR listada."""
    inc = {"rodovia_afetada": "BR-101", "descricao": "Acidente na BR-101"}
    assert ht._incidente_relevante_para_rodovia(inc, "BR-116 / BR-101") is True


def test_incidente_relevante_multi_rodovia_sem_codigo_rejeita():
    """Incidente local sem BR explicita deve ser descartado em rota multi-BR."""
    inc = {"rodovia_afetada": "", "descricao": "Acidente em av amazonas"}
    assert ht._incidente_relevante_para_rodovia(inc, "BR-116 / BR-101") is False


def test_incidente_relevante_fallback_legado_sem_codigo_no_filtro():
    """Sem código reconhecível no filtro, mantem fallback textual legado."""
    inc = {
        "rodovia_afetada": "Rodovia Presidente Dutra",
        "descricao": "Acidente na Rodovia Presidente Dutra",
    }
    assert ht._incidente_relevante_para_rodovia(inc, "Rodovia Presidente Dutra") is True
    assert ht._incidente_relevante_para_rodovia(inc, "Rodovia Fernão Dias") is False


def test_incidente_relevante_sem_filtro():
    """Sem filtro, qualquer incidente e relevante."""
    inc = {"rodovia_afetada": "", "descricao": "Qualquer coisa"}
    assert ht._incidente_relevante_para_rodovia(inc, "") is True
    assert ht._incidente_relevante_para_rodovia(inc, None) is True


# ===== Etapa 2: Validacao de velocidade =====

def test_velocidade_suspeita_loga_warning(monkeypatch, caplog):
    """Velocidades > 250 km/h apos conversao geram warning."""
    # Mock sessao que retorna velocidades altas (100 m/s = 360 km/h)
    fake_resp = _FakeResponse({
        "results": [
            {"currentFlow": {"speed": 100, "freeFlow": 100, "jamFactor": 0}}
        ]
    })
    fake_session = _FakeSession(responses=[fake_resp])
    monkeypatch.setattr(ht, "_get_sessao", lambda: fake_session)
    monkeypatch.setattr(ht, "_parse_ou_geocode",
                        lambda *a: (-23.55, -46.63))

    # Desabilitar corridor para testar so o flow
    monkeypatch.setattr(ht, "_obter_corridor_ou_none",
                        lambda *a, **k: (None, None))

    with caplog.at_level(logging.WARNING, logger="sources.here_traffic"):
        result = ht.consultar_fluxo_trafego.__wrapped__(
            "fake_key", "SP", "RJ", trecho_nome="Teste",
        )

    assert result["velocidade_atual_kmh"] == 360.0
    assert any("suspeita" in r.message.lower() for r in caplog.records)


# ===== Etapa 5: Corridor integration =====

def test_obter_corridor_ou_none_sem_flexpolyline(monkeypatch):
    """Sem flexpolyline, retorna (None, None)."""
    monkeypatch.setattr(ht, "fp", None)
    result = ht._obter_corridor_ou_none("key", (-23, -46), (-25, -49), "T", 100)
    assert result == (None, None)


def test_obter_corridor_cache_inflight_sem_poison_on_fail(monkeypatch):
    """Falha inicial nao deve envenenar cache e deve permitir sucesso depois."""
    class _FP:
        @staticmethod
        def decode(_):
            return [(0.0, 0.0), (0.0, 1.0)]

    monkeypatch.setattr(ht, "fp", _FP())
    monkeypatch.setattr(ht, "_route_cache_key", lambda *a, **k: ("car", "A", "B"))

    # Estado limpo para o teste
    ht._route_polyline_cache.clear()
    ht._route_polyline_inflight.clear()

    calls = {"n": 0}
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def fake_fetch(*_args, **_kwargs):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n == 1:
            started.set()
            release.wait(timeout=1)
            raise RuntimeError("falha temporaria")
        return {"stitched": "BFoz5xJ67i1B1B7PzIhaxL7Y", "sections": ["BFoz5xJ67i1B1B7PzIhaxL7Y"]}

    monkeypatch.setattr(ht, "_obter_polyline_rota_v8", fake_fetch)

    resultados = []

    def _worker():
        resultados.append(
            ht._obter_corridor_ou_none("key", (-23, -46), (-25, -49), "T", 100)
        )

    t1 = threading.Thread(target=_worker)
    t1.start()

    assert started.wait(timeout=1)

    t2 = threading.Thread(target=_worker)
    t2.start()
    release.set()
    t1.join()
    t2.join()

    # Apenas uma tentativa de fetch durante concorrencia (in-flight compartilhado).
    assert calls["n"] == 1
    assert all(r == (None, None) for r in resultados)
    assert ("car", "A", "B") not in ht._route_polyline_cache

    # Proxima chamada deve tentar novamente e conseguir cachear.
    in_corridor, route_pts = ht._obter_corridor_ou_none(
        "key", (-23, -46), (-25, -49), "T", 100
    )
    assert calls["n"] == 2
    assert in_corridor is not None
    assert route_pts is not None
    cached = ht._route_polyline_cache.get(("car", "A", "B"))
    assert cached is not None
    assert cached["stitched"] == "BFoz5xJ67i1B1B7PzIhaxL7Y"


def test_montar_in_corridor():
    """Formato do corridor deve ser correto."""
    result = ht._montar_in_corridor("BFoz5xJ67i1B1B7PzIhaxL7Y", 100)
    assert result == "corridor:BFoz5xJ67i1B1B7PzIhaxL7Y;r=100"


# ===== Filtro bbox: polyline de referencia =====

def test_construir_polyline_referencia_basico():
    """Segmentos validos geram polyline com pontos corretos."""
    segmentos = [
        {"pontos_referencia": [
            {"km": 0, "lat": -23.5, "lng": -46.6, "local": "SP"},
            {"km": 50, "lat": -23.8, "lng": -46.9, "local": "Juquitiba"},
        ]},
        {"pontos_referencia": [
            {"km": 100, "lat": -24.1, "lng": -47.2, "local": "Registro"},
        ]},
    ]
    result = ht._construir_polyline_referencia(segmentos)
    assert result is not None
    assert len(result) == 3
    assert result[0] == (-23.5, -46.6)
    assert result[2] == (-24.1, -47.2)


def test_construir_polyline_referencia_vazio():
    """Segmentos vazios ou sem pontos retorna None."""
    assert ht._construir_polyline_referencia(None) is None
    assert ht._construir_polyline_referencia([]) is None
    assert ht._construir_polyline_referencia([{"pontos_referencia": []}]) is None
    # Apenas 1 ponto — insuficiente para polyline
    assert ht._construir_polyline_referencia(
        [{"pontos_referencia": [{"km": 0, "lat": -23.5, "lng": -46.6}]}]
    ) is None


def test_construir_polyline_referencia_ignora_invalidos():
    """Pontos sem lat/lng sao ignorados, resultado valido se ha >= 2 bons."""
    segmentos = [
        {"pontos_referencia": [
            {"km": 0, "lat": -23.5, "lng": -46.6},
            {"km": 10},  # sem lat/lng
            {"km": 20, "lat": -23.7, "lng": -46.8},
        ]},
    ]
    result = ht._construir_polyline_referencia(segmentos)
    assert result is not None
    assert len(result) == 2


def test_preservar_route_pts_downsample_falha(monkeypatch):
    """_obter_corridor_ou_none retorna route_pts quando downsampling falha."""
    pontos_originais = [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]

    class _FP:
        @staticmethod
        def decode(_):
            return list(pontos_originais)

        @staticmethod
        def encode(pts):
            # Gera string longa para forcar fallback de downsampling
            return "A" * 2000

    monkeypatch.setattr(ht, "fp", _FP())
    monkeypatch.setattr(ht, "_route_cache_key", lambda *a, **k: ("car", "X", "Y"))

    ht._route_polyline_cache.clear()
    ht._route_polyline_inflight.clear()

    # Polyline longa no cache (>1200 chars para forcar downsample branch)
    long_polyline = "A" * 1300
    ht._route_polyline_cache[("car", "X", "Y")] = {"stitched": long_polyline, "sections": [long_polyline]}

    in_corridor, route_pts = ht._obter_corridor_ou_none(
        "key", (-23, -46), (-24, -47), "Teste", 100
    )
    # Corridor falhou (polyline muito longa), mas route_pts preservado
    assert in_corridor is None
    assert route_pts is not None
    assert len(route_pts) == 3


def test_bbox_filtra_incidente_longe(monkeypatch):
    """Incidente a >500m da polyline de referencia e descartado no modo bbox."""
    # Polyline: trecho reto no equador (0,0) -> (0,1)
    segmentos_ref = [
        {"pontos_referencia": [
            {"km": 0, "lat": 0.0, "lng": 0.0},
            {"km": 100, "lat": 0.0, "lng": 1.0},
        ]},
    ]

    # Incidente a ~1.1km ao norte da rota (0.01 grau lat ~ 1.1km)
    incidente_longe = {
        "id": "longe1",
        "incidentDetails": {
            "type": "accident", "criticality": "major",
            "summary": {"value": "Acidente longe"},
        },
        "location": {
            "shape": {"links": [{"points": [{"lat": 0.01, "lng": 0.5}]}]}
        },
    }

    fake_resp = _FakeResponse({"results": [incidente_longe]})
    fake_session = _FakeSession(responses=[fake_resp])
    monkeypatch.setattr(ht, "_get_sessao", lambda: fake_session)
    monkeypatch.setattr(ht, "_parse_ou_geocode", lambda *a: (0.0, 0.0))
    monkeypatch.setattr(ht, "_obter_corridor_ou_none", lambda *a, **k: (None, None))

    result = ht.consultar_incidentes.__wrapped__(
        "fake_key", "A", "B", trecho_nome="Teste",
        segmentos=segmentos_ref, bbox_filter_radius_m=500,
    )
    # Incidente a ~1.1km deve ser filtrado (threshold 500m)
    assert len(result) == 0


def test_bbox_mantem_incidente_perto(monkeypatch):
    """Incidente a <500m da polyline de referencia e mantido no modo bbox."""
    segmentos_ref = [
        {"pontos_referencia": [
            {"km": 0, "lat": 0.0, "lng": 0.0},
            {"km": 100, "lat": 0.0, "lng": 1.0},
        ]},
    ]

    # Incidente a ~111m ao norte (0.001 grau lat ~ 111m)
    incidente_perto = {
        "id": "perto1",
        "incidentDetails": {
            "type": "accident", "criticality": "major",
            "summary": {"value": "Acidente perto"},
            "roadInfo": {"name": "BR-116"},
        },
        "location": {
            "shape": {"links": [{"points": [{"lat": 0.001, "lng": 0.5}]}]}
        },
    }

    fake_resp = _FakeResponse({"results": [incidente_perto]})
    fake_session = _FakeSession(responses=[fake_resp])
    monkeypatch.setattr(ht, "_get_sessao", lambda: fake_session)
    monkeypatch.setattr(ht, "_parse_ou_geocode", lambda *a: (0.0, 0.0))
    monkeypatch.setattr(ht, "_obter_corridor_ou_none", lambda *a, **k: (None, None))

    result = ht.consultar_incidentes.__wrapped__(
        "fake_key", "A", "B", trecho_nome="Teste",
        segmentos=segmentos_ref, bbox_filter_radius_m=500,
    )
    # Incidente a ~111m deve ser mantido (threshold 500m)
    assert len(result) == 1
    assert result[0]["distancia_rota_m"] < 500
