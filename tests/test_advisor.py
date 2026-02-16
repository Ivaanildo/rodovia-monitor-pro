"""
Testes unitários do módulo advisor.
"""
import pytest
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.advisor import DataAdvisor


class TestDataAdvisor:
    def test_calculate_freshness_recente(self):
        adv = DataAdvisor()
        ts = datetime.now() - timedelta(minutes=2)
        assert adv.calculate_freshness_score(ts) == 1.0

    def test_calculate_freshness_antigo(self):
        adv = DataAdvisor()
        ts = datetime.now() - timedelta(hours=2)
        assert adv.calculate_freshness_score(ts) == 0.0

    def test_get_best_source_vazio(self):
        adv = DataAdvisor()
        r = adv.get_best_source({})
        assert r["source"] == "nenhuma"
        assert r["confidence"] == 0

    def test_enriquecer_dados(self):
        adv = DataAdvisor()
        dados = [{"trecho": "SP-Registro", "status": "Normal", "fontes_utilizadas": []}]
        here = {
            "incidentes": {"SP-Registro": [{"consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]},
            "fluxo": {"SP-Registro": {"consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}},
        }
        gmaps = [{"trecho": "SP-Registro", "consultado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]
        r = adv.enriquecer_dados(dados, here, gmaps)
        assert "confianca_pct" in r[0]
        assert r[0]["confianca"] in ("Alta", "Média", "Baixa")
