"""Testes do correlator."""
import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources.correlator import (
    gerar_link_waze, gerar_link_gmaps, correlacionar_trecho,
    correlacionar_todos, _extrair_coordenadas,
)


@pytest.fixture
def trecho_exemplo():
    return {
        "nome": "SP-Registro",
        "origem": "São Paulo, SP",
        "destino": "Registro, SP",
        "rodovia": "BR-116",
        "sentido": "Sul",
        "concessionaria": "Arteris",
        "segmentos": [],
    }


@pytest.fixture
def trecho_com_segmentos():
    return {
        "nome": "SP-Curitiba",
        "origem": "São Paulo",
        "destino": "Curitiba",
        "rodovia": "BR-116",
        "sentido": "Sul",
        "concessionaria": "Arteris",
        "segmentos": [{
            "rodovia": "BR-116",
            "pontos_referencia": [
                {"km": 280, "lat": -23.5505, "lng": -46.6333, "local": "SP"},
                {"km": 520, "lat": -25.4200, "lng": -48.5800, "local": "Curitiba"},
            ],
        }],
    }


def test_link_waze_sem_coordenadas(trecho_exemplo):
    url = gerar_link_waze(trecho_exemplo["origem"], trecho_exemplo["destino"])
    assert "waze.com/ul" in url
    assert "navigate=yes" in url


def test_link_waze_com_coordenadas():
    coords = (-23.5505, -46.6333, -25.4200, -48.5800)
    url = gerar_link_waze("São Paulo", "Curitiba", coordenadas=coords)
    assert "waze.com/ul" in url
    assert "-25.42" in url
    assert "navigate=yes" in url
    assert "from=" not in url


def test_link_gmaps_sem_coordenadas(trecho_exemplo):
    url = gerar_link_gmaps(trecho_exemplo["origem"], trecho_exemplo["destino"])
    assert "google.com/maps/dir" in url
    assert "maps.app.goo.gl" not in url


def test_link_gmaps_com_coordenadas():
    coords = (-23.5505, -46.6333, -25.4200, -48.5800)
    url = gerar_link_gmaps("São Paulo", "Curitiba", coordenadas=coords)
    assert "google.com/maps/dir" in url
    assert "-23.5505" in url
    assert "-25.42" in url
    assert "travelmode=driving" in url
    assert "maps.app.goo.gl" not in url


def test_extrair_coordenadas_com_segmentos(trecho_com_segmentos):
    coords = _extrair_coordenadas(trecho_com_segmentos)
    assert coords is not None
    assert coords == (-23.5505, -46.6333, -25.4200, -48.5800)


def test_extrair_coordenadas_sem_segmentos(trecho_exemplo):
    coords = _extrair_coordenadas(trecho_exemplo)
    assert coords is None


def test_correlacionar_links_com_coordenadas(trecho_com_segmentos):
    r = correlacionar_trecho(trecho_com_segmentos)
    assert "waze.com/ul" in r["link_waze"]
    assert "google.com/maps/dir" in r["link_gmaps"]
    assert "-25.42" in r["link_waze"]
    assert "-23.5505" in r["link_gmaps"]


def test_correlacionar_sem_dados(trecho_exemplo):
    r = correlacionar_trecho(trecho_exemplo)
    assert r["status"] == "Sem dados"
    assert r["trecho"] == "SP-Registro"


def test_correlacionar_com_gmaps(trecho_exemplo):
    gmaps = {"trecho": "SP-Registro", "status": "Normal", "duracao_normal_min": 120,
             "duracao_transito_min": 130, "atraso_min": 10, "distancia_km": 200}
    r = correlacionar_trecho(trecho_exemplo, gmaps_data=gmaps)
    assert r["status"] == "Normal"
    assert "Google Maps" in r["fontes_utilizadas"]


def test_correlacionar_prioridade_interdicao(trecho_exemplo):
    inc = [
        {"categoria": "Engarrafamento", "severidade_id": 1, "descricao": "Trânsito"},
        {"categoria": "Interdição", "severidade_id": 4, "descricao": "Via fechada"},
    ]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    assert r["ocorrencia_principal"] == "Interdição"
    assert r["ocorrencia"].startswith("Interdição")


def test_correlacionar_todos_vazio():
    assert correlacionar_todos([]) == []


def test_observacao_limpa_inicio_fim_e_alerta(trecho_exemplo):
    inc = [{
        "categoria": "Interdição",
        "severidade_id": 4,
        "descricao": "[BR-116] BR-116, KM 318.0 | Início: Fechado | entre Resende e Rio de Janeiro - Fim: Fechado | Alertas de segurança",
        "km_estimado": 318.0,
        "trecho_especifico": "Resende -> Rio de Janeiro",
        "localizacao_precisa": "BR-116, KM 318.0, Resende -> Rio de Janeiro",
    }]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    obs = r["descricao"].lower()
    assert "inicio:" not in obs
    assert "fim:" not in obs
    assert "alerta" not in obs


def test_observacao_lista_todas_ocorrencias_no_trecho(trecho_exemplo):
    inc = [
        {
            "categoria": "Interdição",
            "severidade_id": 4,
            "descricao": "Via fechada para limpeza de pista",
            "km_estimado": 318.0,
            "trecho_especifico": "Resende -> Rio de Janeiro",
            "localizacao_precisa": "BR-116, KM 318.0, Resende -> Rio de Janeiro",
        },
        {
            "categoria": "Engarrafamento",
            "severidade_id": 2,
            "descricao": "Lentidão entre os kms 320 e 323",
            "km_estimado": 321.0,
            "trecho_especifico": "Resende -> Rio de Janeiro",
            "localizacao_precisa": "BR-116, KM 321.0, Resende -> Rio de Janeiro",
        },
    ]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    obs = r["descricao"]
    assert "Interdição" in obs
    assert "Engarrafamento" in obs
    assert "KM 318" in obs
    assert "KM 321" in obs


def test_multiplas_ocorrencias_no_campo_ocorrencia(trecho_exemplo):
    """Verifica que multiplas ocorrencias aparecem no campo ocorrencia."""
    inc = [
        {"categoria": "Interdição", "severidade_id": 4, "descricao": "Via bloqueada",
         "km_estimado": 350.0, "trecho_especifico": "", "localizacao_precisa": ""},
        {"categoria": "Colisão", "severidade_id": 3, "descricao": "Acidente com 2 veiculos",
         "km_estimado": 50.0, "trecho_especifico": "", "localizacao_precisa": ""},
        {"categoria": "Obras na Pista", "severidade_id": 2, "descricao": "Sinalizacao",
         "km_estimado": 200.0, "trecho_especifico": "", "localizacao_precisa": ""},
    ]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    assert "Interdição" in r["ocorrencia"]
    assert "Colisão" in r["ocorrencia"]
    assert "Obras" in r["ocorrencia"]
    assert r["ocorrencia_principal"] == "Interdição"


def test_ocorrencia_unica_sem_ponto_virgula(trecho_exemplo):
    """Com apenas 1 ocorrencia, campo ocorrencia nao tem ';'."""
    inc = [{"categoria": "Colisão", "severidade_id": 3, "descricao": "Acidente",
            "km_estimado": 100.0, "trecho_especifico": "", "localizacao_precisa": ""}]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    assert r["ocorrencia"] == "Colisão"
    assert ";" not in r["ocorrencia"]
    assert r["ocorrencia_principal"] == "Colisão"


def test_ocorrencias_duplicadas_dedup(trecho_exemplo):
    """Duas colisoes em KMs diferentes nao duplicam a categoria."""
    inc = [
        {"categoria": "Colisão", "severidade_id": 3, "descricao": "Acidente A",
         "km_estimado": 50.0, "trecho_especifico": "", "localizacao_precisa": ""},
        {"categoria": "Colisão", "severidade_id": 2, "descricao": "Acidente B",
         "km_estimado": 150.0, "trecho_especifico": "", "localizacao_precisa": ""},
    ]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    assert r["ocorrencia"].count("Colisão") == 1
    assert r["ocorrencia_principal"] == "Colisão"


def test_status_promotion_com_multiplas_ocorrencias(trecho_exemplo):
    """Status promotion usa ocorrencia principal (mais severa), nao concatenada."""
    inc = [
        {"categoria": "Interdição", "severidade_id": 4, "descricao": "Via bloqueada",
         "km_estimado": 350.0, "trecho_especifico": "", "localizacao_precisa": ""},
        {"categoria": "Engarrafamento", "severidade_id": 2, "descricao": "Lentidao",
         "km_estimado": 50.0, "trecho_especifico": "", "localizacao_precisa": ""},
    ]
    r = correlacionar_trecho(trecho_exemplo, here_incidentes=inc)
    assert r["status"] == "Intenso"
    assert "Interdição" in r["ocorrencia"]
    assert "Engarrafamento" in r["ocorrencia"]
    assert r["ocorrencia_principal"] == "Interdição"
