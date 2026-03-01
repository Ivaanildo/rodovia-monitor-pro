"""Testes unitarios do adaptador TomTom."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import tomtom_api as tt


def test_classificar_categoria_tomtom_lane_closed_e_parcial():
    cat, escopo, causa = tt._classificar_categoria_tomtom(
        7, "Faixa fechada com trafego fluindo",
    )
    assert cat == "Bloqueio Parcial"
    assert escopo == "parcial"
    assert causa == "indefinida"


def test_classificar_categoria_tomtom_road_closed_e_interdicao():
    cat, escopo, causa = tt._classificar_categoria_tomtom(
        8, "Road closed por deslizamento",
    )
    assert cat == "Interdição"
    assert escopo == "total"
    assert causa == "risco"


def test_classificar_categoria_tomtom_acidente_sem_bloqueio_total():
    cat, escopo, causa = tt._classificar_categoria_tomtom(
        1, "Acidente com desvio operacional",
    )
    assert cat == "Colisão"
    assert escopo == "parcial"
    assert causa == "acidente"
