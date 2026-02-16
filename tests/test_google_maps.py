"""Testes unitarios para configuracao e payload do Google Maps."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import google_maps as gm


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = ""

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.last_url = None
        self.last_headers = None
        self.last_json = None

    def post(self, url, headers=None, json=None, timeout=0):
        self.last_url = url
        self.last_headers = headers or {}
        self.last_json = json or {}
        return _FakeResponse(self.payload)


def test_resolver_config_google_padrao_usa_optimal():
    cfg = gm._resolver_config_google({})
    assert cfg["routing_preference_padrao"] == "TRAFFIC_AWARE_OPTIMAL"


def test_routes_v2_inclui_traffic_on_polyline(monkeypatch):
    payload = {
        "routes": [{
            "duration": "120s",
            "staticDuration": "100s",
            "distanceMeters": 1000,
            "warnings": [],
            "travelAdvisory": {"speedReadingIntervals": []},
        }]
    }
    sess = _FakeSession(payload)
    monkeypatch.setattr(gm, "_get_sessao", lambda: sess)

    ok, data = gm._consultar_routes_v2(
        api_key="abc",
        origem="Sao Paulo",
        destino="Rio de Janeiro",
        routing_preference="TRAFFIC_AWARE_OPTIMAL",
        traffic_model="BEST_GUESS",
    )

    assert ok is True
    assert data["duracao_transito_s"] == 120
    assert "extraComputations" in sess.last_json
    assert "TRAFFIC_ON_POLYLINE" in sess.last_json["extraComputations"]
    assert "routes.travelAdvisory.speedReadingIntervals" in sess.last_headers.get("X-Goog-FieldMask", "")
