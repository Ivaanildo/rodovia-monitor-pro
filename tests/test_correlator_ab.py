"""
Testes A/B do correlator — 20 cenarios de validacao.

Cada cenario valida o comportamento esperado apos os patches
das analises correlatorAnalise01.md e correlatorAnalise02.md.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.correlator import (
    correlacionar_trecho,
    correlacionar_todos,
    _extrair_coordenadas,
    _decidir_ocorrencia,
    _normalizar_chave,
    _avaliar_confianca,
    gerar_link_waze,
    gerar_link_gmaps,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def trecho_base():
    """Trecho minimo valido com segmentos e coordenadas."""
    return {
        "nome": "SP-Registro",
        "origem": "São Paulo, SP",
        "destino": "Registro, SP",
        "rodovia": "BR-116",
        "tipo": "federal",
        "sentido": "Sul",
        "concessionaria": "Arteris",
        "segmentos": [
            {
                "rodovia": "BR-116",
                "pontos_referencia": [
                    {"km": 280, "lat": -23.5505, "lng": -46.6333, "local": "SP"},
                    {"km": 520, "lat": -25.4200, "lng": -48.5800, "local": "Registro"},
                ],
            }
        ],
    }


@pytest.fixture
def trecho_sem_segmentos():
    """Trecho sem segmentos — coordenadas indisponiveis."""
    return {
        "nome": "SP-Registro",
        "origem": "São Paulo, SP",
        "destino": "Registro, SP",
        "rodovia": "BR-116",
        "segmentos": [],
    }


@pytest.fixture
def gmaps_atraso_20():
    return {
        "trecho": "SP-Registro",
        "status": "Moderado",
        "duracao_normal_min": 120,
        "duracao_transito_min": 140,
        "atraso_min": 20,
        "distancia_km": 200,
    }


@pytest.fixture
def gmaps_atraso_25():
    return {
        "trecho": "SP-Registro",
        "status": "Intenso",
        "duracao_normal_min": 120,
        "duracao_transito_min": 145,
        "atraso_min": 25,
        "distancia_km": 200,
    }


@pytest.fixture
def here_fluxo_jam7():
    return {
        "status": "Intenso",
        "jam_factor": 7,
        "velocidade_atual_kmh": 25,
        "velocidade_livre_kmh": 80,
    }


@pytest.fixture
def here_fluxo_jam8():
    return {
        "status": "Intenso",
        "jam_factor": 8,
        "velocidade_atual_kmh": 10,
        "velocidade_livre_kmh": 80,
    }


@pytest.fixture
def here_inc_colisao():
    return [
        {
            "categoria": "Colisão",
            "severidade_id": 3,
            "descricao": "Colisão entre veículos no KM 320",
            "km_estimado": 320.0,
            "trecho_especifico": "SP -> Registro",
            "localizacao_precisa": "BR-116, KM 320, Juquitiba",
        }
    ]


@pytest.fixture
def here_inc_interdicao():
    return [
        {
            "categoria": "Interdição",
            "severidade_id": 4,
            "descricao": "Via totalmente interditada por deslizamento",
            "bloqueio_escopo": "total",
            "causa_detectada": "risco",
            "km_estimado": 350.0,
            "trecho_especifico": "Juquitiba -> Miracatu",
            "localizacao_precisa": "BR-116, KM 350, Miracatu",
        }
    ]


# ── Cenario 1: Dados completos (HERE + Google) ─────────────────────────────


def test_01_dados_completos(trecho_base, gmaps_atraso_20, here_fluxo_jam7, here_inc_colisao):
    r = correlacionar_trecho(
        trecho_base,
        gmaps_data=gmaps_atraso_20,
        here_incidentes=here_inc_colisao,
        here_fluxo=here_fluxo_jam7,
    )
    assert r["status"] == "Intenso"
    assert "Colisão" in r["ocorrencia"]
    assert r["confianca"] == "Alta"
    assert len(set(r["fontes_utilizadas"])) >= 3


# ── Cenario 2: So Google Maps (atraso >= 15 → infere Engarrafamento) ───────


def test_02_so_google_infere_engarrafamento(trecho_base, gmaps_atraso_25):
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps_atraso_25)
    assert r["atraso_min"] == 25
    assert r["ocorrencia"] == "Engarrafamento"
    assert r["confianca"] in ("Media", "Alta")


# ── Cenario 3: So HERE Flow (jam=8) ────────────────────────────────────────


def test_03_so_here_flow(trecho_base, here_fluxo_jam8):
    r = correlacionar_trecho(trecho_base, here_fluxo=here_fluxo_jam8)
    assert r["status"] == "Intenso"
    assert r["ocorrencia"] == "Engarrafamento"
    assert r["confianca"] == "Media"


# ── Cenario 4: So HERE Incidents (interdicao sev=4) ────────────────────────


def test_04_so_here_incidents(trecho_base, here_inc_interdicao):
    r = correlacionar_trecho(trecho_base, here_incidentes=here_inc_interdicao)
    assert r["ocorrencia"] == "Interdição"
    assert r["status"] == "Intenso"


def test_04b_acidente_com_desvio_permanece_colisao(trecho_base):
    inc = [
        {
            "categoria": "Colisão",
            "severidade_id": 3,
            "descricao": "Acidente com desvio operacional e trafego fluindo",
            "bloqueio_escopo": "nenhum",
            "causa_detectada": "acidente",
            "km_estimado": 345.0,
            "trecho_especifico": "Juquitiba -> Miracatu",
            "localizacao_precisa": "BR-116, KM 345, Juquitiba",
        }
    ]
    r = correlacionar_trecho(trecho_base, here_incidentes=inc)
    assert r["ocorrencia"] == "Colisão"
    assert r["ocorrencia_principal"] == "Colisão"
    assert "Interdição" not in r["descricao"]


# ── Cenario 5: Nenhuma fonte ───────────────────────────────────────────────


def test_05_nenhuma_fonte(trecho_base):
    r = correlacionar_trecho(trecho_base)
    assert r["status"] == "Sem dados"
    assert "Nenhuma" in r["fontes_utilizadas"]


# ── Cenario 6: Google com erro ─────────────────────────────────────────────


def test_06_google_erro(trecho_base, here_fluxo_jam7):
    gmaps_erro = {"trecho": "SP-Registro", "status": "Erro"}
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps_erro, here_fluxo=here_fluxo_jam7)
    assert "Google Maps" not in r["fontes_utilizadas"]
    assert "HERE Flow" in r["fontes_utilizadas"]


# ── Cenario 7: KeyError em coordenadas (segmentos sem lat/lng) ─────────────


def test_07_coordenadas_sem_latlng():
    trecho = {
        "nome": "Test",
        "origem": "A",
        "destino": "B",
        "rodovia": "BR-1",
        "segmentos": [
            {"pontos_referencia": [{"km": 100, "local": "X"}]}
        ],
    }
    coords = _extrair_coordenadas(trecho)
    assert coords is None


def test_07b_coordenadas_tipo_invalido():
    trecho = {
        "nome": "Test",
        "origem": "A",
        "destino": "B",
        "segmentos": [
            {"pontos_referencia": [{"lat": "abc", "lng": -46.0}]}
        ],
    }
    coords = _extrair_coordenadas(trecho)
    assert coords is None


# ── Cenario 8: KeyError em duracao Google Maps ──────────────────────────────


def test_08_gmaps_sem_duracao(trecho_base):
    gmaps_incompleto = {
        "trecho": "SP-Registro",
        "status": "Moderado",
        "atraso_min": 15,
    }
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps_incompleto)
    assert r["atraso_min"] == 15
    assert r["duracao_normal_min"] == 0


# ── Cenario 9: Nome com acento/espaco (normalizacao do index) ──────────────


def test_09_normalizacao_index():
    trechos = [
        {
            "nome": "São Paulo - Campinas",
            "origem": "São Paulo",
            "destino": "Campinas",
            "rodovia": "SP-348",
            "segmentos": [],
        }
    ]
    gmaps = [
        {
            "trecho": "Sao Paulo - Campinas ",
            "status": "Moderado",
            "atraso_min": 10,
            "duracao_normal_min": 60,
            "duracao_transito_min": 70,
            "distancia_km": 100,
        }
    ]
    resultados = correlacionar_todos(trechos, gmaps_resultados=gmaps)
    assert len(resultados) == 1
    assert "Google Maps" in resultados[0]["fontes_utilizadas"]


# ── Cenario 10: Dedupe de fontes ───────────────────────────────────────────


def test_10_dedupe_fontes(trecho_base, gmaps_atraso_20, here_fluxo_jam7):
    r = correlacionar_trecho(
        trecho_base,
        gmaps_data=gmaps_atraso_20,
        here_fluxo=here_fluxo_jam7,
    )
    assert len(r["fontes_utilizadas"]) == len(set(r["fontes_utilizadas"]))


# ── Cenario 11: Promocao de status (Colisao + Normal → Intenso) ────────────


def test_11_promocao_status(trecho_base, here_inc_colisao):
    r = correlacionar_trecho(trecho_base, here_incidentes=here_inc_colisao)
    assert r["ocorrencia"] == "Colisão"
    assert r["status"] == "Intenso"


# ── Cenario 12: Jam factor limiar (4, 5, 6) ────────────────────────────────


@pytest.mark.parametrize("jam,espera_engarrafamento", [
    (4, False),
    (5, True),
    (6, True),
])
def test_12_jam_factor_limiar(trecho_base, jam, espera_engarrafamento):
    fluxo = {"status": "Moderado" if jam < 6 else "Intenso", "jam_factor": jam}
    r = correlacionar_trecho(trecho_base, here_fluxo=fluxo)
    if espera_engarrafamento:
        assert r["ocorrencia"] == "Engarrafamento"
    else:
        assert r["ocorrencia"] == ""


# ── Cenario 13: Score parcial (categoria "Obras de Recapeamento") ──────────


def test_13_score_parcial_categorias(trecho_base):
    inc = [
        {"categoria": "Obras de Recapeamento", "severidade_id": 2, "descricao": "Obras"},
        {"categoria": "Engarrafamento", "severidade_id": 2, "descricao": "Trânsito"},
    ]
    r = correlacionar_trecho(trecho_base, here_incidentes=inc)
    assert "Obras" in r["ocorrencia"]


# ── Cenario 14: Atraso Google < 15 → NAO infere engarrafamento ─────────────


def test_14_atraso_google_abaixo_limiar(trecho_base):
    gmaps = {
        "trecho": "SP-Registro",
        "status": "Normal",
        "duracao_normal_min": 120,
        "duracao_transito_min": 130,
        "atraso_min": 10,
        "distancia_km": 200,
    }
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps)
    assert r["ocorrencia"] == ""


# ── Cenario 15: Atraso Google >= 15 → infere engarrafamento ────────────────


def test_15_atraso_google_acima_limiar(trecho_base):
    gmaps = {
        "trecho": "SP-Registro",
        "status": "Moderado",
        "duracao_normal_min": 120,
        "duracao_transito_min": 135,
        "atraso_min": 15,
        "distancia_km": 200,
    }
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps)
    assert r["ocorrencia"] == "Engarrafamento"


# ── Cenario 16: Links sem coordenadas ──────────────────────────────────────


def test_16_links_sem_coordenadas():
    url_waze = gerar_link_waze("São Paulo", "Registro")
    url_gmaps = gerar_link_gmaps("São Paulo", "Registro")

    assert "waze.com/ul" in url_waze
    assert "navigate=yes" in url_waze
    assert "from=" not in url_waze

    assert "google.com/maps/dir" in url_gmaps
    assert "travelmode=driving" in url_gmaps
    assert "layer=traffic" in url_gmaps


# ── Cenario 17: Links com coordenadas ──────────────────────────────────────


def test_17_links_com_coordenadas():
    coords = (-23.5505, -46.6333, -25.4200, -48.5800)
    url_waze = gerar_link_waze("SP", "Registro", coordenadas=coords)
    url_gmaps = gerar_link_gmaps("SP", "Registro", coordenadas=coords)

    assert "waze.com/ul" in url_waze
    assert "ll=-25.42,-48.58" in url_waze
    assert "from=" not in url_waze

    assert "google.com/maps/dir" in url_gmaps
    assert "-23.5505" in url_gmaps
    assert "layer=traffic" in url_gmaps


# ── Cenario 18: Config incompleto (sem "nome") ─────────────────────────────


def test_18_config_sem_nome():
    trecho_invalido = {"origem": "A", "destino": "B", "rodovia": "BR-1", "segmentos": []}
    r = correlacionar_trecho(trecho_invalido)
    assert r["status"] == "Sem dados"
    assert r["trecho"] == ""


# ── Cenario 19: Confianca so Google com atraso → Media ─────────────────────


def test_19_confianca_google_com_atraso(trecho_base, gmaps_atraso_25):
    r = correlacionar_trecho(trecho_base, gmaps_data=gmaps_atraso_25)
    assert r["confianca"] in ("Media", "Alta")
    assert r["confianca"] != "Baixa"


# ── Cenario 20: Descricao compacta (limites de caracteres) ─────────────────


def test_20_descricao_sem_ruido(trecho_base):
    """Verifica que ruido (inicio:, fim:, alertas) e removido mesmo sem truncamento."""
    inc = [
        {
            "categoria": "Interdição",
            "severidade_id": 4,
            "descricao": (
                "[BR-116] BR-116, KM 318.0 | "
                "Início: Via bloqueada por deslizamento de terra | "
                "entre Resende e Rio de Janeiro - "
                "Fim: Bloqueio total | "
                "Alertas de segurança ativados para a região"
            ),
            "bloqueio_escopo": "total",
            "causa_detectada": "risco",
            "km_estimado": 318.0,
            "trecho_especifico": "Resende -> Rio de Janeiro",
            "localizacao_precisa": "BR-116, KM 318.0",
        }
    ]
    r = correlacionar_trecho(trecho_base, here_incidentes=inc)
    assert "inicio:" not in r["descricao"].lower()
    assert "alerta" not in r["descricao"].lower()
    assert "BR-116" in r["descricao"] or "Interdição" in r["descricao"]
