# Fixtures compartilhadas
import pytest

@pytest.fixture
def pontos_referencia():
    return [
        {"km": 100, "lat": -23.5, "lng": -46.6, "local": "Juquitiba"},
        {"km": 150, "lat": -24.0, "lng": -47.2, "local": "Miracatu"},
        {"km": 200, "lat": -24.5, "lng": -47.8, "local": "Registro"},
        {"km": 250, "lat": -25.0, "lng": -48.4, "local": "Cananéia"},
    ]

@pytest.fixture
def trecho_exemplo():
    return {
        "nome": "SP-Registro",
        "origem": "São Paulo, SP",
        "destino": "Registro, SP",
        "rodovia": "BR-116",
        "sentido": "Sul",
        "concessionaria": "Arteris",
        "segmentos": [
            {
                "rodovia": "BR-116",
                "nome_popular": "Régis Bittencourt",
                "sentido": "Sul",
                "pontos_referencia": [
                    {"km": 100, "lat": -23.5, "lng": -46.6, "local": "Juquitiba"},
                    {"km": 200, "lat": -24.5, "lng": -47.8, "local": "Registro"},
                ],
            }
        ],
    }
