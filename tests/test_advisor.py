"""
Testes unitários do módulo advisor (cobertura completa).
"""
import pytest
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.advisor import DataAdvisor, _parse_timestamp


# ─── _parse_timestamp ────────────────────────────────────────────────


class TestParseTimestamp:

    def test_iso_com_z(self):
        """ISO com Z deve parsear como UTC."""
        result = _parse_timestamp("2026-02-16T12:34:56Z")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result == datetime(2026, 2, 16, 12, 34, 56, tzinfo=timezone.utc)

    def test_iso_com_offset(self):
        """ISO com offset deve preservar timezone."""
        result = _parse_timestamp("2026-02-16T12:34:56-03:00")
        assert result is not None
        assert result.tzinfo is not None
        # Converter para UTC: 12:34 -03:00 = 15:34 UTC
        assert result.astimezone(timezone.utc).hour == 15

    def test_iso_com_microsegundos(self):
        """ISO com microsegundos deve parsear corretamente."""
        result = _parse_timestamp("2026-02-16T12:34:56.123456")
        assert result is not None
        assert result.microsecond == 123456
        assert result.tzinfo == timezone.utc

    def test_formato_padrao_producao(self):
        """Formato padrão produção (%Y-%m-%d %H:%M:%S) deve parsear como UTC."""
        result = _parse_timestamp("2026-02-16 12:34:56")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2026
        assert result.second == 56

    def test_formato_brasileiro(self):
        """Formato BR (%d/%m/%Y %H:%M) deve parsear como UTC."""
        result = _parse_timestamp("16/02/2026 12:34")
        assert result is not None
        assert result.tzinfo == timezone.utc
        assert result.day == 16
        assert result.month == 2

    def test_datetime_naive(self):
        """datetime naive deve ser convertido para UTC."""
        dt = datetime(2026, 2, 16, 12, 34, 56)
        result = _parse_timestamp(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 12

    def test_datetime_aware(self):
        """datetime aware deve ser preservado."""
        tz_br = timezone(timedelta(hours=-3))
        dt = datetime(2026, 2, 16, 12, 34, 56, tzinfo=tz_br)
        result = _parse_timestamp(dt)
        assert result.tzinfo == tz_br
        assert result.hour == 12

    def test_string_vazia(self):
        assert _parse_timestamp("") is None

    def test_string_espacos(self):
        assert _parse_timestamp("   ") is None

    def test_none(self):
        assert _parse_timestamp(None) is None

    def test_formato_invalido(self):
        assert _parse_timestamp("not a date") is None
        assert _parse_timestamp("2026/02/16") is None
        assert _parse_timestamp("abc123") is None


# ─── calculate_freshness_score ───────────────────────────────────────


class TestCalculateFreshnessScore:

    def setup_method(self):
        self.adv = DataAdvisor()
        self.now = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)

    def test_agora_score_maximo(self):
        """Dado com 0 minutos deve retornar 1.0."""
        assert self.adv.calculate_freshness_score(self.now, now=self.now) == 1.0

    def test_muito_recente_3min(self):
        """Dado com 3 minutos: score alto (~0.93)."""
        ts = datetime(2026, 2, 16, 11, 57, 0, tzinfo=timezone.utc)
        score = self.adv.calculate_freshness_score(ts, now=self.now)
        assert 0.90 < score < 0.98

    def test_recente_10min(self):
        """Dado com 10 minutos: score ~0.79 (gradual, sem salto)."""
        ts = datetime(2026, 2, 16, 11, 50, 0, tzinfo=timezone.utc)
        score = self.adv.calculate_freshness_score(ts, now=self.now)
        assert 0.70 < score < 0.85

    def test_medio_20min(self):
        """Dado com 20 minutos: score ~0.63."""
        ts = datetime(2026, 2, 16, 11, 40, 0, tzinfo=timezone.utc)
        score = self.adv.calculate_freshness_score(ts, now=self.now)
        assert 0.55 < score < 0.70

    def test_antigo_45min(self):
        """Dado com 45 minutos: score ~0.35."""
        ts = datetime(2026, 2, 16, 11, 15, 0, tzinfo=timezone.utc)
        score = self.adv.calculate_freshness_score(ts, now=self.now)
        assert 0.25 < score < 0.45

    def test_muito_antigo_3h(self):
        """Dado com 180 minutos: score < 0.05 → 0.0."""
        ts = datetime(2026, 2, 16, 9, 0, 0, tzinfo=timezone.utc)  # 3h atras
        assert self.adv.calculate_freshness_score(ts, now=self.now) == 0.0

    def test_string_vazia(self):
        """String vazia deve retornar 0.0."""
        assert self.adv.calculate_freshness_score("") == 0.0

    def test_timestamp_futuro_clamp(self):
        """Timestamp futuro (clock skew) deve clampar para idade 0 → score 1.0."""
        futuro = datetime(2026, 2, 16, 12, 10, 0, tzinfo=timezone.utc)
        assert self.adv.calculate_freshness_score(futuro, now=self.now) == 1.0

    def test_string_format_com_now(self):
        """String timestamp com now injetado deve calcular corretamente."""
        ts = "2026-02-16 11:50:00"  # 10 min atrás
        score = self.adv.calculate_freshness_score(ts, now=self.now)
        assert 0.70 < score < 0.85

    def test_decaimento_gradual_sem_saltos(self):
        """Score deve decair gradualmente sem saltos abruptos.

        Verifica que a diferenca entre minutos consecutivos e pequena
        (< 0.03 por minuto), eliminando o problema dos cutoffs.
        """
        scores = []
        for minutes in range(0, 61):
            ts = self.now - timedelta(minutes=minutes)
            scores.append(self.adv.calculate_freshness_score(ts, now=self.now))

        # Verifica monotonicamente decrescente
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Score nao e decrescente: age={i}min ({scores[i]}) > "
                f"age={i+1}min ({scores[i+1]})"
            )

        # Verifica que nao ha saltos > 0.03 entre minutos consecutivos
        for i in range(len(scores) - 1):
            if scores[i] == 0.0 and scores[i + 1] == 0.0:
                continue  # ambos zero, ok
            diff = scores[i] - scores[i + 1]
            assert diff < 0.03, (
                f"Salto abrupto: age={i}min ({scores[i]}) -> "
                f"age={i+1}min ({scores[i+1]}), diff={diff:.4f}"
            )


# ─── get_best_source ─────────────────────────────────────────────────


class TestGetBestSource:

    def setup_method(self):
        self.adv = DataAdvisor()
        self.now = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)

    def test_dict_vazio(self):
        """Dict vazio deve retornar 'nenhuma'."""
        result = self.adv.get_best_source({})
        assert result["source"] == "nenhuma"
        assert result["confidence"] == 0

    def test_fonte_unica_fresh(self):
        """Fonte única com dado recente deve ser selecionada."""
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        sources = {"here_incident": {"timestamp": ts, "data": {"test": True}}}
        result = self.adv.get_best_source(sources)
        assert result["source"] == "here_incident"
        # freshness ~0.93 * weight 1.0 * 100 → ~93
        assert result["confidence"] > 85

    def test_multiplas_fontes_peso_decide(self):
        """Com freshness igual, fonte com maior peso deve vencer."""
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
        sources = {
            "here_incident": {"timestamp": ts, "data": {}},
            "google_duration": {"timestamp": ts, "data": {}},
        }
        result = self.adv.get_best_source(sources)
        assert result["source"] == "here_incident"  # peso 1.0 > 0.8

    def test_todas_confianca_zero(self):
        """Quando todas as fontes têm confiança 0, deve retornar 'nenhuma'."""
        ts_velho = (self.now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        sources = {
            "here_incident": {"timestamp": ts_velho, "data": {}},
            "google_duration": {"timestamp": ts_velho, "data": {}},
        }
        result = self.adv.get_best_source(sources)
        assert result["source"] == "nenhuma"
        assert result["confidence"] == 0
        # all_scores deve conter as fontes avaliadas
        assert "here_incident" in result["all_scores"]
        assert "google_duration" in result["all_scores"]


# ─── enriquecer_dados ────────────────────────────────────────────────


class TestEnriquecerDados:

    def setup_method(self):
        self.adv = DataAdvisor()
        self.ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def test_campos_presentes(self):
        """Enriquecimento deve adicionar todos os campos esperados."""
        dados = [{"trecho": "SP-Registro"}]
        here = {
            "incidentes": {"SP-Registro": [{"consultado_em": self.ts}]},
            "fluxo": {"SP-Registro": {"status": "OK", "consultado_em": self.ts}},
        }
        gmaps = [{"trecho": "SP-Registro", "status": "Normal", "consultado_em": self.ts}]

        result = self.adv.enriquecer_dados(dados, here, gmaps)

        assert "confianca_pct" in result[0]
        assert "confianca" in result[0]
        assert "fonte_escolhida" in result[0]
        assert "fontes_consultadas" in result[0]
        assert "proxima_atualizacao_min" in result[0]
        assert result[0]["confianca"] in ("Alta", "Média", "Baixa", "Sem dados")

    def test_nomes_campos_corretos(self):
        """Não deve usar fonte_vencedora (campo antigo). Deve usar fonte_escolhida."""
        dados = [{"trecho": "SP-Registro"}]
        here = {
            "incidentes": {"SP-Registro": [{"consultado_em": self.ts}]},
            "fluxo": {},
        }
        gmaps = []

        result = self.adv.enriquecer_dados(dados, here, gmaps)

        assert "fonte_vencedora" not in result[0]
        assert "fonte_escolhida" in result[0]
        assert result[0]["fonte_escolhida"] == "HERE"

    def test_fontes_consultadas_lista_todas(self):
        """fontes_consultadas deve listar todas as fontes, não só a vencedora."""
        dados = [{"trecho": "SP-Registro"}]
        here = {
            "incidentes": {"SP-Registro": [{"consultado_em": self.ts}]},
            "fluxo": {},
        }
        gmaps = [{"trecho": "SP-Registro", "status": "Normal", "consultado_em": self.ts}]

        result = self.adv.enriquecer_dados(dados, here, gmaps)

        assert "Google" in result[0]["fontes_consultadas"]
        assert "HERE" in result[0]["fontes_consultadas"]
        # fonte_escolhida deve ser individual
        assert result[0]["fonte_escolhida"] in ("HERE", "Google")

    def test_gravidade_km_local_aumentam_confianca(self):
        """Mesmo com fonte igual, ocorrencia grave com KM/Local deve subir confianca."""
        base = [{"trecho": "SP-Registro", "status": "Normal", "ocorrencia": ""}]
        grave = [{
            "trecho": "SP-Registro",
            "status": "Intenso",
            "ocorrencia": "Interdição",
            "km_ocorrencia": 318.0,
            "trecho_especifico": "Resende -> Rio de Janeiro",
            "localizacao_precisa": "BR-116, KM 318.0, Resende -> Rio de Janeiro",
        }]

        here_base = {"incidentes": {}, "fluxo": {}}
        here_grave = {
            "incidentes": {"SP-Registro": [{"consultado_em": self.ts, "severidade_id": 4}]},
            "fluxo": {},
        }
        gmaps = [{"trecho": "SP-Registro", "status": "Normal", "consultado_em": self.ts}]

        r_base = self.adv.enriquecer_dados(base, here_base, gmaps)[0]
        r_grave = self.adv.enriquecer_dados(grave, here_grave, gmaps)[0]

        assert r_grave["confianca_pct"] > r_base["confianca_pct"]
        assert r_grave["confianca_operacional_pct"] > r_base["confianca_operacional_pct"]


# ─── calcular_proxima_atualizacao ────────────────────────────────────


class TestCalcularProximaAtualizacao:

    def setup_method(self):
        self.adv = DataAdvisor()
        self.now = datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc)

    def test_sem_timestamp_estatico(self):
        """Sem timestamp, deve usar cálculo estático."""
        assert self.adv.calcular_proxima_atualizacao("here_incident", 30) == 2  # min(2, 30)
        assert self.adv.calcular_proxima_atualizacao("google_duration", 30) == 5  # min(5, 30)

    def test_com_dado_fresh(self):
        """Dado coletado há 1 min, API interval 2 min → faltam ~1 min."""
        ts = (self.now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        result = self.adv.calcular_proxima_atualizacao(
            "here_incident", 30, last_timestamp=ts, now=self.now
        )
        assert result == 1

    def test_respeita_cap_polling(self):
        """Não deve exceder intervalo de polling."""
        ts = self.now.strftime("%Y-%m-%d %H:%M:%S")  # acabou de coletar
        result = self.adv.calcular_proxima_atualizacao(
            "google_duration", 2, last_timestamp=ts, now=self.now
        )
        # API interval 5 min, mas polling cap é 2 → retorna 2
        assert result == 2

    def test_minimo_1_minuto(self):
        """Nunca deve retornar menos que 1 minuto."""
        ts = self.now.strftime("%Y-%m-%d %H:%M:%S")
        result = self.adv.calcular_proxima_atualizacao(
            "here_flow", 30, last_timestamp=ts, now=self.now
        )
        assert result >= 1


# ─── Regressão: dados vazios não devem ter confiança alta ──────────


class TestAdvisorComDadosVazios:
    """Testa que o DataAdvisor não atribui confiança a dados vazios."""

    def setup_method(self):
        self.adv = DataAdvisor()
        self.ts_fresco = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def test_here_flow_sem_dados_nao_conta_como_fonte(self):
        """HERE flow com status='Sem dados' não deve ser contado como fonte."""
        dados = [{"trecho": "BR-116 SP"}]
        here = {
            "incidentes": {},
            "fluxo": {
                "BR-116 SP": {
                    "trecho": "BR-116 SP",
                    "status": "Sem dados",
                    "jam_factor": 0,
                    "velocidade_atual_kmh": 0,
                    "velocidade_livre_kmh": 0,
                    "fonte": "HERE Flow",
                    "consultado_em": self.ts_fresco,
                }
            },
        }
        gmaps = []
        result = self.adv.enriquecer_dados(dados, here, gmaps)
        assert result[0]["confianca_pct"] == 0
        assert result[0]["confianca"] == "Sem dados"

    def test_google_erro_nao_conta_como_fonte(self):
        """Google Maps com status='Erro' não deve ser contado como fonte."""
        dados = [{"trecho": "BR-116 SP"}]
        here = {"incidentes": {}, "fluxo": {}}
        gmaps = [{
            "trecho": "BR-116 SP",
            "status": "Erro",
            "duracao_normal_min": 0,
            "duracao_transito_min": 0,
            "consultado_em": self.ts_fresco,
        }]
        result = self.adv.enriquecer_dados(dados, here, gmaps)
        assert result[0]["confianca_pct"] == 0
        assert result[0]["confianca"] == "Sem dados"

    def test_ambos_vazios_confianca_zero(self):
        """Quando HERE e Google falham, confiança deve ser 0."""
        dados = [{"trecho": "BR-116 SP"}]
        here = {
            "incidentes": {},
            "fluxo": {
                "BR-116 SP": {"status": "Sem dados", "jam_factor": 0,
                               "consultado_em": self.ts_fresco}
            },
        }
        gmaps = [{"trecho": "BR-116 SP", "status": "Erro",
                  "consultado_em": self.ts_fresco}]
        result = self.adv.enriquecer_dados(dados, here, gmaps)
        assert result[0]["confianca_pct"] == 0
        assert result[0]["confianca"] == "Sem dados"
        assert result[0]["fontes_consultadas"] == "Sem dados"

    def test_google_ok_aqui_sem_dados_parcial(self):
        """Google OK + HERE 'Sem dados' → só Google conta como fonte."""
        dados = [{"trecho": "BR-116 SP"}]
        here = {
            "incidentes": {},
            "fluxo": {
                "BR-116 SP": {"status": "Sem dados", "consultado_em": self.ts_fresco}
            },
        }
        gmaps = [{
            "trecho": "BR-116 SP",
            "status": "Normal",
            "duracao_normal_min": 120,
            "duracao_transito_min": 125,
            "consultado_em": self.ts_fresco,
        }]
        result = self.adv.enriquecer_dados(dados, here, gmaps)
        assert result[0]["confianca_pct"] > 0
        assert result[0]["fonte_escolhida"] == "Google"
        assert "HERE" not in result[0]["fontes_consultadas"]
