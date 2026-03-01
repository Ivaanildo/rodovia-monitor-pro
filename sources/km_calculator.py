"""
Modulo de calculo de KM e localizacao precisa para incidentes.
"""

import logging
import math
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# Raio medio da Terra em km
RAIO_TERRA_KM = 6371.0
MAX_GAP_INTERMUNICIPAL_KM = 60
MAX_GAP_METROPOLITANO_KM = 20
MAX_DISTANCIA_REFERENCIA_KM = 50

_HINTS_METROPOLITANOS = (
    "marginal",
    "avenida",
    "ponte",
    "centro",
    "urbano",
    "recife",
    "olinda",
    "paulista",
    "deodoro",
    "jaguare",
    "sao paulo",
    "taboao",
    "embu",
    "itapecerica",
    "diadema",
    "sao bernardo",
)

_SUFIXO_PONTO_PATTERN = re.compile(
    r"\s*-\s*(?:in(?:i|\u00ed)cio|fim)\b.*$",
    flags=re.IGNORECASE,
)
_PARENTESES_FINAIS_PATTERN = re.compile(r"\s*\([^)]*\)\s*$")


def _normalizar_texto(txt: str) -> str:
    base = unicodedata.normalize("NFKD", str(txt or ""))
    sem_acento = "".join(ch for ch in base if not unicodedata.combining(ch))
    return " ".join(sem_acento.split()).strip().lower()


def _to_float(valor) -> Optional[float]:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _normalizar_ponto(ponto: dict) -> Optional[dict]:
    if not isinstance(ponto, dict):
        return None

    km = _to_float(ponto.get("km"))
    lat = _to_float(ponto.get("lat"))
    lng = _to_float(ponto.get("lng"))

    if km is None or lat is None or lng is None:
        return None

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None

    normalizado = dict(ponto)
    normalizado["km"] = km
    normalizado["lat"] = lat
    normalizado["lng"] = lng
    return normalizado


def _normalizar_pontos_referencia(pontos_referencia: list) -> list:
    pontos = []
    for ponto in pontos_referencia or []:
        ponto_ok = _normalizar_ponto(ponto)
        if ponto_ok is not None:
            pontos.append(ponto_ok)
    return sorted(pontos, key=lambda p: p["km"])


def _limpar_nome_local(local: str) -> str:
    """Normaliza nome de ponto para exibicao curta no relatorio."""
    txt = (local or "").strip()
    if not txt:
        return ""
    txt = _SUFIXO_PONTO_PATTERN.sub("", txt)
    txt = _PARENTESES_FINAIS_PATTERN.sub("", txt)
    return " ".join(txt.split()).strip(" -")


def _eh_trecho_metropolitano(local1: str, local2: str) -> bool:
    txt = _normalizar_texto(f"{_limpar_nome_local(local1)} {_limpar_nome_local(local2)}")
    return any(hint in txt for hint in _HINTS_METROPOLITANOS)


def _limite_gap_km(p1: dict, p2: dict, gap_limit_km: Optional[float]) -> float:
    candidatos = (
        gap_limit_km,
        p1.get("limite_gap_km"),
        p2.get("limite_gap_km"),
    )
    for limite in candidatos:
        limite_float = _to_float(limite)
        if limite_float is not None and limite_float > 0:
            return limite_float

    if _eh_trecho_metropolitano(p1.get("local", ""), p2.get("local", "")):
        return MAX_GAP_METROPOLITANO_KM

    return MAX_GAP_INTERMUNICIPAL_KM


def _local_mais_proximo_por_km(km_estimado: float, p1: dict, p2: dict) -> str:
    d1 = abs(km_estimado - p1["km"])
    d2 = abs(km_estimado - p2["km"])
    escolhido = p1 if d1 <= d2 else p2
    return _limpar_nome_local(escolhido.get("local", ""))


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calcula distancia em km entre dois pontos usando formula de Haversine."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return RAIO_TERRA_KM * c


def calcular_bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calcula bearing (azimute) em graus de ponto1 para ponto2. Retorna 0-360."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)

    x = math.sin(dlng) * math.cos(lat2_r)
    y = (
        math.cos(lat1_r) * math.sin(lat2_r)
        - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlng)
    )

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def _calcular_confianca(desvio_lateral: float, gap_km: float, dist_min_km: float) -> float:
    if desvio_lateral < 2:
        confianca = 1.0
    elif desvio_lateral < 5:
        confianca = 0.8
    elif desvio_lateral < 15:
        confianca = 0.6
    else:
        confianca = 0.4

    if gap_km > 120:
        confianca *= 0.6
    elif gap_km > 80:
        confianca *= 0.75
    elif gap_km > 60:
        confianca *= 0.85

    if dist_min_km > 35:
        confianca *= 0.7
    elif dist_min_km > 20:
        confianca *= 0.85

    return max(0.1, min(1.0, round(confianca, 2)))


def estimar_km(lat: float, lng: float, pontos_referencia: list) -> dict:
    """
    Estima posicao quilometrica a partir de coordenadas lat/lng.

    Estrategia:
    1. Calcula distancia do incidente para cada ponto de referencia valido.
    2. Usa somente pares adjacentes ao ponto mais proximo.
    3. Interpola o KM com proporcao de distancia e aplica penalidades.

    Returns:
        dict com km_estimado, confianca (0-1), ponto_mais_proximo
    """
    lat = _to_float(lat)
    lng = _to_float(lng)
    if lat is None or lng is None:
        return {"km_estimado": None, "confianca": 0.0, "ponto_mais_proximo": None}

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return {"km_estimado": None, "confianca": 0.0, "ponto_mais_proximo": None}

    pontos = _normalizar_pontos_referencia(pontos_referencia)
    if len(pontos) < 2:
        return {"km_estimado": None, "confianca": 0.0, "ponto_mais_proximo": None}

    distancias = []
    for idx, ponto in enumerate(pontos):
        distancias.append(
            {
                "idx": idx,
                "ponto": ponto,
                "distancia_km": haversine(lat, lng, ponto["lat"], ponto["lng"]),
            }
        )

    mais_proximo = min(distancias, key=lambda x: x["distancia_km"])
    dist_min_km = mais_proximo["distancia_km"]

    if dist_min_km > MAX_DISTANCIA_REFERENCIA_KM:
        return {
            "km_estimado": round(mais_proximo["ponto"]["km"], 1),
            "confianca": 0.2,
            "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
            "distancia_mais_proxima_km": round(dist_min_km, 2),
        }

    j = mais_proximo["idx"]
    pares_candidatos = []
    if j > 0:
        pares_candidatos.append((j - 1, j))
    if j < len(pontos) - 1:
        pares_candidatos.append((j, j + 1))

    melhor_par = None
    melhor_custo = float("inf")

    for i1, i2 in pares_candidatos:
        p1 = pontos[i1]
        p2 = pontos[i2]

        d1 = distancias[i1]["distancia_km"]
        d2 = distancias[i2]["distancia_km"]
        d_entre = haversine(p1["lat"], p1["lng"], p2["lat"], p2["lng"])
        if d_entre <= 0:
            continue

        soma = d1 + d2
        proporcao = 0.5 if soma == 0 else d1 / soma
        proporcao = max(0.0, min(1.0, proporcao))

        erro_linha = abs((d1 + d2) - d_entre)
        custo = erro_linha + 0.05 * soma

        if custo < melhor_custo:
            melhor_custo = custo
            melhor_par = {
                "p1": p1,
                "p2": p2,
                "proporcao": proporcao,
                "erro_linha": erro_linha,
                "gap_km": abs(p2["km"] - p1["km"]),
            }

    if melhor_par is None:
        return {
            "km_estimado": round(mais_proximo["ponto"]["km"], 1),
            "confianca": 0.3,
            "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
            "distancia_mais_proxima_km": round(dist_min_km, 2),
        }

    km1 = melhor_par["p1"]["km"]
    km2 = melhor_par["p2"]["km"]
    km_estimado = km1 + (km2 - km1) * melhor_par["proporcao"]

    confianca = _calcular_confianca(
        melhor_par["erro_linha"],
        melhor_par["gap_km"],
        dist_min_km,
    )

    return {
        "km_estimado": round(km_estimado, 1),
        "confianca": confianca,
        "ponto_mais_proximo": mais_proximo["ponto"].get("local", ""),
        "distancia_mais_proxima_km": round(dist_min_km, 2),
    }


def detectar_sentido(lat: float, lng: float, segmento: dict) -> Optional[str]:
    """
    Detecta sentido do incidente relativo ao segmento.

    Usa o campo 'sentido' do segmento como referencia principal.
    """
    if not segmento:
        return None
    return segmento.get("sentido")


def identificar_trecho_local(
    km_estimado: float,
    pontos_referencia: list,
    gap_limit_km: Optional[float] = None,
) -> str:
    """
    Identifica trecho local em formato curto para uso operacional.

    Exemplo: "Resende -> Rio de Janeiro"
    Para gaps grandes, retorna "proximo a <local>" para evitar inferencia imprecisa.
    """
    km_estimado = _to_float(km_estimado)
    if km_estimado is None:
        return ""

    pontos = _normalizar_pontos_referencia(pontos_referencia)
    if not pontos:
        return ""

    if km_estimado <= pontos[0]["km"]:
        local = _limpar_nome_local(pontos[0].get("local", ""))
        return f"proximo a {local}" if local else ""

    if km_estimado >= pontos[-1]["km"]:
        local = _limpar_nome_local(pontos[-1].get("local", ""))
        return f"proximo a {local}" if local else ""

    for i in range(len(pontos) - 1):
        p1 = pontos[i]
        p2 = pontos[i + 1]
        if p1["km"] <= km_estimado <= p2["km"]:
            local1 = _limpar_nome_local(p1.get("local", ""))
            local2 = _limpar_nome_local(p2.get("local", ""))

            gap_km = abs(p2["km"] - p1["km"])
            limite = _limite_gap_km(p1, p2, gap_limit_km)
            if gap_km > limite:
                local_proximo = _local_mais_proximo_por_km(km_estimado, p1, p2)
                return f"proximo a {local_proximo}" if local_proximo else ""

            if local1 and local2:
                return f"{local1} -> {local2}"
            return local1 or local2

    return ""


def enriquecer_incidente(incidente: dict, segmentos: list) -> dict:
    """
    Enriquece um incidente HERE com dados de localizacao precisa.

    Adiciona ao incidente:
    - km_estimado: posicao KM estimada
    - sentido: direcao (ex: "Sul", "Norte->Sul")
    - trecho_especifico: "entre X e Y"
    - confianca_localizacao: 0.0-1.0
    - rodovia_segmento: rodovia do segmento correspondente
    - nome_popular: nome popular da rodovia

    Returns:
        incidente enriquecido (mesmo dict, modificado in-place)
    """
    incidente.setdefault("km_estimado", None)
    incidente.setdefault("sentido", None)
    incidente.setdefault("trecho_especifico", "")
    incidente.setdefault("confianca_localizacao", 0.0)
    incidente.setdefault("rodovia_segmento", "")
    incidente.setdefault("nome_popular", "")

    if not segmentos:
        return incidente

    lat = _to_float(incidente.get("latitude"))
    lng = _to_float(incidente.get("longitude"))
    if lat is None or lng is None:
        return incidente

    melhor_resultado = None
    melhor_metricas = (-1.0, float("inf"))

    for seg in segmentos:
        pontos = seg.get("pontos_referencia", [])
        if not pontos:
            continue

        resultado = estimar_km(lat, lng, pontos)
        metricas = (
            float(resultado.get("confianca", 0.0)),
            float(resultado.get("distancia_mais_proxima_km", float("inf"))),
        )

        if metricas[0] > melhor_metricas[0] or (
            metricas[0] == melhor_metricas[0] and metricas[1] < melhor_metricas[1]
        ):
            melhor_metricas = metricas
            melhor_resultado = dict(resultado)
            melhor_resultado["segmento"] = seg

    if melhor_resultado and melhor_resultado["km_estimado"] is not None:
        seg = melhor_resultado["segmento"]
        pontos = seg.get("pontos_referencia", [])

        incidente["km_estimado"] = melhor_resultado["km_estimado"]
        incidente["confianca_localizacao"] = melhor_resultado["confianca"]
        incidente["sentido"] = detectar_sentido(lat, lng, seg)
        incidente["trecho_especifico"] = identificar_trecho_local(
            melhor_resultado["km_estimado"],
            pontos,
            gap_limit_km=seg.get("limite_gap_km"),
        )
        incidente["rodovia_segmento"] = seg.get("rodovia", "")
        incidente["nome_popular"] = seg.get("nome_popular", "")

        desc_parts = []
        rodovia = seg.get("rodovia", "")
        nome_pop = seg.get("nome_popular", "")

        if rodovia:
            label = f"{rodovia} ({nome_pop})" if nome_pop else rodovia
            desc_parts.append(label)

        km = melhor_resultado["km_estimado"]
        if melhor_resultado["confianca"] >= 0.6:
            desc_parts.append(f"KM {km}")
        else:
            desc_parts.append(f"KM ~{km} (estimado)")

        sentido = incidente["sentido"]
        if sentido:
            desc_parts.append(f"sentido {sentido}")

        trecho = incidente["trecho_especifico"]
        if trecho:
            desc_parts.append(trecho)

        incidente["localizacao_precisa"] = ", ".join(desc_parts)

        logger.debug(
            "Incidente enriquecido: %s (confianca: %.0f%%)",
            incidente["localizacao_precisa"],
            melhor_resultado["confianca"] * 100,
        )

    return incidente
