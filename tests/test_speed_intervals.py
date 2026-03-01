"""
Testes para processamento de speedReadingIntervals do Google Routes API v2.

Valida _analisar_speed_intervals(), _construir_resumo_velocidade() e a
integracao com correlacionar_trecho() / _gerar_observacao_detalhada().
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.correlator import (
    _analisar_speed_intervals,
    _construir_resumo_velocidade,
    correlacionar_trecho,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def trecho_base():
    """Trecho minimo valido com segmentos e coordenadas."""
    return {
        "nome": "SP-Registro",
        "origem": "Sao Paulo, SP",
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
def gmaps_normal_sem_speed():
    return {
        "trecho": "SP-Registro",
        "status": "Normal",
        "duracao_normal_min": 120,
        "duracao_transito_min": 125,
        "atraso_min": 5,
        "distancia_km": 200,
    }


@pytest.fixture
def gmaps_com_speed_normal():
    """Google Maps data com speed intervals todos NORMAL."""
    return {
        "trecho": "SP-Registro",
        "status": "Normal",
        "duracao_normal_min": 120,
        "duracao_transito_min": 125,
        "atraso_min": 5,
        "distancia_km": 200,
        "traffic_on_polyline": [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 50, "speed": "NORMAL"},
            {"startPolylinePointIndex": 50, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ],
    }


@pytest.fixture
def gmaps_com_speed_jam_final():
    """Google Maps data com congestionamento no trecho final."""
    return {
        "trecho": "SP-Registro",
        "status": "Moderado",
        "duracao_normal_min": 120,
        "duracao_transito_min": 145,
        "atraso_min": 25,
        "distancia_km": 200,
        "traffic_on_polyline": [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 60, "speed": "NORMAL"},
            {"startPolylinePointIndex": 60, "endPolylinePointIndex": 80, "speed": "SLOW"},
            {"startPolylinePointIndex": 80, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ],
    }


@pytest.fixture
def gmaps_com_speed_slow_central():
    """Google Maps data com lentidao no trecho central."""
    return {
        "trecho": "SP-Registro",
        "status": "Moderado",
        "duracao_normal_min": 120,
        "duracao_transito_min": 140,
        "atraso_min": 20,
        "distancia_km": 200,
        "traffic_on_polyline": [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 30, "speed": "NORMAL"},
            {"startPolylinePointIndex": 30, "endPolylinePointIndex": 70, "speed": "SLOW"},
            {"startPolylinePointIndex": 70, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ],
    }


@pytest.fixture
def here_inc_colisao():
    return [
        {
            "categoria": "Colisao",
            "severidade_id": 3,
            "descricao": "Colisao entre veiculos",
            "fonte": "HERE",
            "km_estimado": 350,
            "trecho_especifico": "Juquitiba",
            "localizacao_precisa": "KM 350 - Juquitiba",
        }
    ]


@pytest.fixture
def here_fluxo_jam7():
    return {
        "status": "Intenso",
        "jam_factor": 7,
        "velocidade_atual_kmh": 30,
        "velocidade_livre_kmh": 100,
    }


# ── Testes unitarios: _analisar_speed_intervals ────────────────────────────


class TestAnalisarSpeedIntervals:
    """Testes unitarios para _analisar_speed_intervals()."""

    def test_none_retorna_none(self):
        assert _analisar_speed_intervals(None) is None

    def test_lista_vazia_retorna_none(self):
        assert _analisar_speed_intervals([]) is None

    def test_tipo_invalido_string(self):
        assert _analisar_speed_intervals("invalid") is None

    def test_tipo_invalido_int(self):
        assert _analisar_speed_intervals(42) is None

    def test_elementos_invalidos_retorna_none(self):
        """Lista com apenas elementos nao-dict retorna None."""
        assert _analisar_speed_intervals([42, "abc", None]) is None

    def test_tudo_normal(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 50, "speed": "NORMAL"},
            {"startPolylinePointIndex": 50, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 100.0
        assert result["pct_slow"] == 0.0
        assert result["pct_jam"] == 0.0
        assert result["tem_congestionamento"] is False
        assert result["zonas_congestionamento"] == []
        assert "fluxo livre" in result["resumo"]

    def test_tudo_jam(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_jam"] == 100.0
        assert result["pct_normal"] == 0.0
        assert result["tem_congestionamento"] is True
        assert len(result["zonas_congestionamento"]) >= 1

    def test_tudo_slow(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "SLOW"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_slow"] == 100.0
        assert result["pct_jam"] == 0.0
        assert result["tem_congestionamento"] is True

    def test_mix_normal_slow_70_30(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 70, "speed": "NORMAL"},
            {"startPolylinePointIndex": 70, "endPolylinePointIndex": 100, "speed": "SLOW"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 70.0
        assert result["pct_slow"] == 30.0
        assert result["pct_jam"] == 0.0
        assert result["tem_congestionamento"] is True

    def test_mix_normal_jam_50_50(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 50, "speed": "NORMAL"},
            {"startPolylinePointIndex": 50, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 50.0
        assert result["pct_jam"] == 50.0
        assert result["tem_congestionamento"] is True

    def test_tres_velocidades(self):
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 40, "speed": "NORMAL"},
            {"startPolylinePointIndex": 40, "endPolylinePointIndex": 70, "speed": "SLOW"},
            {"startPolylinePointIndex": 70, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 40.0
        assert result["pct_slow"] == 30.0
        assert result["pct_jam"] == 30.0
        total = result["pct_normal"] + result["pct_slow"] + result["pct_jam"]
        assert abs(total - 100.0) < 0.5

    def test_zona_inicial(self):
        """SLOW concentrado em 0-33% -> trecho inicial."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 30, "speed": "SLOW"},
            {"startPolylinePointIndex": 30, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert "trecho inicial" in result["zonas_congestionamento"]
        assert "trecho final" not in result["zonas_congestionamento"]

    def test_zona_central(self):
        """SLOW concentrado em 33-66% -> trecho central."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 40, "speed": "NORMAL"},
            {"startPolylinePointIndex": 40, "endPolylinePointIndex": 60, "speed": "SLOW"},
            {"startPolylinePointIndex": 60, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert "trecho central" in result["zonas_congestionamento"]

    def test_zona_final(self):
        """TRAFFIC_JAM concentrado em 66-100% -> trecho final."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 70, "speed": "NORMAL"},
            {"startPolylinePointIndex": 70, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert "trecho final" in result["zonas_congestionamento"]

    def test_zonas_multiplas(self):
        """SLOW em inicio e JAM no final -> 2+ zonas."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 20, "speed": "SLOW"},
            {"startPolylinePointIndex": 20, "endPolylinePointIndex": 70, "speed": "NORMAL"},
            {"startPolylinePointIndex": 70, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert "trecho inicial" in result["zonas_congestionamento"]
        assert "trecho final" in result["zonas_congestionamento"]
        assert len(result["zonas_congestionamento"]) >= 2

    def test_proto3_start_ausente(self):
        """startPolylinePointIndex ausente defaults para 0 (proto3)."""
        intervals = [
            {"endPolylinePointIndex": 50, "speed": "SLOW"},
            {"startPolylinePointIndex": 50, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_slow"] == 50.0

    def test_speed_desconhecido_tratado_como_normal(self):
        """Speed desconhecido (ex: SPEED_UNSPECIFIED) tratado como NORMAL."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 50, "speed": "SPEED_UNSPECIFIED"},
            {"startPolylinePointIndex": 50, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 100.0
        assert result["tem_congestionamento"] is False

    def test_segmento_zero_ignorado(self):
        """Segmento com start==end e ignorado."""
        intervals = [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 0, "speed": "TRAFFIC_JAM"},
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ]
        result = _analisar_speed_intervals(intervals)
        assert result is not None
        assert result["pct_normal"] == 100.0
        assert result["pct_jam"] == 0.0


# ── Testes unitarios: _construir_resumo_velocidade ─────────────────────────


class TestConstruirResumoVelocidade:
    """Testes unitarios para _construir_resumo_velocidade()."""

    def test_tudo_normal(self):
        resumo = _construir_resumo_velocidade(0, 0, [])
        assert "fluxo livre" in resumo

    def test_jam_intenso(self):
        """pct_jam >= 20 -> 'Congestionamento intenso'."""
        resumo = _construir_resumo_velocidade(10, 25, ["trecho final"])
        assert "Congestionamento intenso" in resumo
        assert "trecho final" in resumo

    def test_jam_leve(self):
        """0 < pct_jam < 20 -> 'Congestionamento' sem 'intenso'."""
        resumo = _construir_resumo_velocidade(10, 5, ["trecho central"])
        assert "Congestionamento" in resumo
        assert "intenso" not in resumo

    def test_slow_sem_jam(self):
        """pct_jam == 0, pct_slow > 0 -> 'Transito lento'."""
        resumo = _construir_resumo_velocidade(30, 0, ["trecho inicial"])
        assert "Transito lento" in resumo
        assert "trecho inicial" in resumo

    def test_sem_zonas(self):
        """Sem zonas especificas -> 'rota'."""
        resumo = _construir_resumo_velocidade(20, 0, [])
        assert "rota" in resumo

    def test_multiplas_zonas(self):
        """Multiplas zonas unidas por 'e'."""
        resumo = _construir_resumo_velocidade(15, 10, ["trecho inicial", "trecho final"])
        assert "trecho inicial e trecho final" in resumo

    def test_percentuais_no_resumo(self):
        resumo = _construir_resumo_velocidade(30, 10, ["trecho central"])
        assert "30% lento" in resumo
        assert "10% parado" in resumo


# ── Testes parametrizados ──────────────────────────────────────────────────


@pytest.mark.parametrize("intervals,zonas_esperadas", [
    # Caso 1: SLOW no inicio apenas
    (
        [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 20, "speed": "SLOW"},
            {"startPolylinePointIndex": 20, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ],
        ["trecho inicial"],
    ),
    # Caso 2: JAM no meio
    (
        [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 40, "speed": "NORMAL"},
            {"startPolylinePointIndex": 40, "endPolylinePointIndex": 60, "speed": "TRAFFIC_JAM"},
            {"startPolylinePointIndex": 60, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ],
        ["trecho central"],
    ),
    # Caso 3: SLOW espalhado em tudo
    (
        [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "SLOW"},
        ],
        ["trecho inicial", "trecho final"],
    ),
    # Caso 4: Tudo normal
    (
        [
            {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "NORMAL"},
        ],
        [],
    ),
])
def test_zonas_parametrizado(intervals, zonas_esperadas):
    result = _analisar_speed_intervals(intervals)
    assert result is not None
    for zona in zonas_esperadas:
        assert zona in result["zonas_congestionamento"]
    if not zonas_esperadas:
        assert result["zonas_congestionamento"] == []


@pytest.mark.parametrize("pct_slow,pct_jam,prefixo_esperado", [
    (30, 0, "Transito lento"),
    (10, 5, "Congestionamento"),
    (10, 25, "Congestionamento intenso"),
    (0, 0, "fluxo livre"),
])
def test_resumo_prefixo_parametrizado(pct_slow, pct_jam, prefixo_esperado):
    resumo = _construir_resumo_velocidade(pct_slow, pct_jam, ["trecho central"])
    assert prefixo_esperado in resumo


# ── Testes integracao: correlacionar_trecho com speed intervals ────────────


class TestCorrelacaoComSpeedIntervals:
    """Testes de integracao end-to-end com correlacionar_trecho()."""

    def test_gmaps_jam_final_descricao_menciona_zona(
        self, trecho_base, gmaps_com_speed_jam_final
    ):
        """Speed intervals com JAM no final -> descricao inclui zona."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_jam_final,
        )
        desc = result["descricao"].lower()
        assert "trecho" in desc
        # Deve mencionar alguma zona (central ou final)
        assert "trecho central" in desc or "trecho final" in desc

    def test_gmaps_tudo_normal_descricao_sem_zonas(
        self, trecho_base, gmaps_com_speed_normal
    ):
        """Speed intervals tudo NORMAL -> descricao nao menciona lentidao/zona."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_normal,
        )
        desc = result["descricao"].lower()
        assert "lentidao" not in desc

    def test_gmaps_sem_traffic_on_polyline(
        self, trecho_base, gmaps_normal_sem_speed
    ):
        """gmaps_data sem traffic_on_polyline -> sem erro, comportamento preservado."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_normal_sem_speed,
        )
        assert result["status"] == "Normal"
        assert result["descricao"]  # tem descricao

    def test_gmaps_traffic_on_polyline_vazio(self, trecho_base):
        """traffic_on_polyline como lista vazia -> graceful."""
        gmaps = {
            "trecho": "SP-Registro",
            "status": "Normal",
            "duracao_normal_min": 120,
            "duracao_transito_min": 125,
            "atraso_min": 5,
            "distancia_km": 200,
            "traffic_on_polyline": [],
        }
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps,
        )
        assert result["status"] == "Normal"
        assert "lentidao" not in result["descricao"].lower()

    def test_here_incidente_mais_speed_intervals(
        self, trecho_base, gmaps_com_speed_jam_final, here_inc_colisao
    ):
        """HERE incidente + Google speed intervals -> ambos na descricao."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_jam_final,
            here_incidentes=here_inc_colisao,
        )
        desc = result["descricao"].lower()
        # Deve ter o incidente
        assert "colisao" in desc or "here" in desc
        # Deve ter a zona do Google
        assert "google" in desc or "lentidao" in desc

    def test_here_flow_jam_mais_speed_intervals(
        self, trecho_base, gmaps_com_speed_jam_final, here_fluxo_jam7
    ):
        """HERE flow jam + speed intervals -> zona adicionada na descricao."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_jam_final,
            here_fluxo=here_fluxo_jam7,
        )
        desc = result["descricao"].lower()
        # Status final vem do HERE Flow
        assert result["status"] == "Intenso"
        # Descricao deve incluir zona do Google ou intenso
        assert "trecho" in desc

    def test_atraso_google_com_zona_central(
        self, trecho_base, gmaps_com_speed_slow_central
    ):
        """Atraso Google >10min + speed central -> '(trecho central)' na descricao."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_slow_central,
        )
        desc = result["descricao"].lower()
        assert "trecho" in desc

    def test_speed_intervals_nao_altera_status(
        self, trecho_base, gmaps_com_speed_jam_final
    ):
        """Speed intervals com JAM NAO devem alterar o status."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_jam_final,
        )
        # Status vem do gmaps_data["status"] = "Moderado", nao dos intervals
        # Pode ser promovido por ocorrencia mas nao por speed intervals
        assert result["status"] in ("Moderado", "Intenso")

    def test_speed_intervals_nao_altera_ocorrencia(
        self, trecho_base, gmaps_com_speed_jam_final
    ):
        """Speed intervals NAO criam ocorrencia por si so."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_com_speed_jam_final,
        )
        # Com atraso >= 15 min, pode inferir "Engarrafamento" do atraso,
        # mas isso e pelo mecanismo existente, nao pelos speed intervals
        # O speed interval NAO adiciona ocorrencia nova
        if result["ocorrencia"]:
            assert result["ocorrencia"] == "Engarrafamento"

    def test_sem_gmaps_data(self, trecho_base):
        """Sem dados Google -> speed intervals nao processados, sem erro."""
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=None,
        )
        assert result["status"] == "Sem dados"

    def test_gmaps_erro_speed_intervals_ignorados(self, trecho_base):
        """Google com status Erro -> speed intervals ignorados."""
        gmaps_erro = {
            "trecho": "SP-Registro",
            "status": "Erro",
            "duracao_normal_min": 0,
            "duracao_transito_min": 0,
            "atraso_min": 0,
            "distancia_km": 0,
            "traffic_on_polyline": [
                {"startPolylinePointIndex": 0, "endPolylinePointIndex": 100, "speed": "TRAFFIC_JAM"},
            ],
        }
        result = correlacionar_trecho(
            trecho_config=trecho_base,
            gmaps_data=gmaps_erro,
        )
        # Status Erro nao processa speed intervals
        assert "lentidao" not in result["descricao"].lower()
